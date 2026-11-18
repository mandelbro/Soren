"""Service slash commands during training, and the write-back plan built at /done (ADR-0002 Phase 4).

The trainer teaches Soren about the *service* while training a flow:
``/shortcut``, ``/instruction``, ``/note``, ``/crop``, and ``/service``. Changes
are staged in a ``ServiceUpdate``; ``/crop`` also applies to the live session
immediately. At /done, ``prepare_save`` adds automatic proposals (the URL used
when the file has none, a crop that only came from ``SOREN_CHROME_CROP_Y``)
and returns a plan (target path, new definition, change list, warnings) for
the trainer to confirm before ``ServiceWriter`` writes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from soren.models.service import MAX_CHROME_HEIGHT_PX, KeyboardShortcut, Service
from soren.services.service_prompt import format_shortcut
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import ResolvedService
from soren.services.service_update import ServiceUpdate
from soren.services.service_writer import ServiceWriter

if TYPE_CHECKING:
    from soren.services.training_types import TrainingCallbacks

SERVICE_COMMANDS_HELP = """\
Service commands (saved to config/services/<name>.yaml at /done, after you confirm):
  /shortcut <keys> <action> [| <when>]  — e.g. /shortcut w Week view | no text field is focused
  /instruction <text>  — tactical instruction given to the agent on every task
  /note <text>         — free-form note about the service (added to `notes`)
  /crop [px]           — show or set the chrome height; applies from the next step
  /service [discard]   — show or discard pending service changes
"""

_UNAVAILABLE = "Service commands are unavailable: this session has no service configuration."
_SHORTCUT_USAGE = "Usage: /shortcut <keys> <action> [| <when>]  (e.g. /shortcut cmd+k Open quick search)"
_CROP_USAGE = f"Usage: /crop <pixels>  (a whole number from 0 to {MAX_CHROME_HEIGHT_PX}, e.g. /crop 100)"
# Layers that beat the service file (ADR-0002); a saved value is shadowed while they are set.
_HIGHER_LAYER_PREFIXES = ("env:", "settings.services.", "persona")
# ServiceUpdate field -> where the effective value lives on ResolvedService.
_CONFIG_FIELDS = {"chrome_height_px", "keyboard_shortcuts", "instructions"}


@dataclass(frozen=True)
class ServiceSavePlan:
    path: Path
    service: Service
    changes: list[str]
    warnings: list[str]


def _display(value: Any) -> str:
    return f"{len(value)} item(s)" if isinstance(value, list) else str(value)


class TrainingServiceCommands:
    """Parses service slash commands and plans the service-file update for one training session."""

    _COMMANDS = frozenset({"/shortcut", "/instruction", "/note", "/crop", "/service"})

    def __init__(self, service: ResolvedService | None) -> None:
        self._original = service
        self._service = service
        self.pending = ServiceUpdate()

    @property
    def service(self) -> ResolvedService | None:
        """The live service for the session (reflects ``/crop``)."""
        return self._service

    def handles(self, text: str) -> bool:
        parts = text.strip().split(maxsplit=1)
        return bool(parts) and parts[0].lower() in self._COMMANDS

    def handle(self, text: str) -> str:
        """Run one service command and return the message to show the trainer."""
        parts = text.strip().split(maxsplit=1)
        command, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        if self._original is None:
            return _UNAVAILABLE
        return getattr(self, f"_cmd_{command[1:]}")(arg)

    # --- commands ---

    def _cmd_shortcut(self, arg: str) -> str:
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            return _SHORTCUT_USAGE
        keys, rest = parts
        action, _, when = rest.partition("|")
        if not action.strip():
            return _SHORTCUT_USAGE
        try:
            shortcut = KeyboardShortcut.model_validate(
                {"keys": keys, "action": action.strip(), "when": when.strip() or None}
            )
        except ValidationError as exc:
            return f"Invalid shortcut {keys!r}: {exc.errors()[0].get('msg', 'invalid')}. {_SHORTCUT_USAGE}"
        self.pending.add_shortcut(shortcut)
        return f"Staged shortcut: {format_shortcut(shortcut)}"

    def _cmd_instruction(self, arg: str) -> str:
        if not arg:
            return "Usage: /instruction <text>"
        self.pending.add_instruction(arg)
        return f"Staged instruction: {arg}"

    def _cmd_note(self, arg: str) -> str:
        if not arg:
            return "Usage: /note <text>"
        self.pending.add_note(arg)
        return f"Staged note: {arg}"

    def _cmd_crop(self, arg: str) -> str:
        assert self._service is not None
        if not arg:
            source = self._service.source_of("chrome_height_px")
            return f"Chrome crop: {self._service.config.chrome_height_px}px (from {source})"
        if not re.fullmatch(r"\d+", arg) or int(arg) > MAX_CHROME_HEIGHT_PX:
            return _CROP_USAGE
        px = int(arg)
        self.pending.set_chrome_height(px)
        self._service = self._service.model_copy(
            update={
                "config": self._service.config.model_copy(update={"chrome_height_px": px}),
                "provenance": {**self._service.provenance, "chrome_height_px": "training:/crop"},
            }
        )
        return f"Chrome crop set to {px}px from the next step (saved to the service file at /done)."

    def _cmd_service(self, arg: str) -> str:
        if arg.lower() == "discard":
            self.pending.discard()
            self._service = self._original
            return "Discarded pending service changes."
        if arg:
            return "Usage: /service [discard]"
        lines = self.pending.describe()
        if not lines:
            return "No pending service changes."
        return "Pending service changes:\n" + "\n".join(f"  {line}" for line in lines)

    # --- write-back ---

    def prepare_save(
        self, *, registry: ServiceRegistry, writer: ServiceWriter, source_url: str
    ) -> ServiceSavePlan | None:
        """Build the update plan for /done, or None if there is nothing to save.

        Raises ServiceConfigError if the current service file has become invalid.
        """
        original = self._original
        if original is None:
            return None
        existing = registry.get(original.name)
        self._add_auto_proposals(original, existing, source_url)
        if self.pending.is_empty:
            return None

        seed = {
            f: getattr(original, f)
            for f in ("type", "url", "discovery_hint")
            if getattr(original, f) and original.source_of(f) != "default"
        }
        new_service = self.pending.apply_to(existing, name=original.name, seed=seed)
        path = registry.path_for(original.name)
        warnings: list[str] = []
        if writer.has_user_comments(path):
            warnings.append(
                f"{path.name} contains # comments that will be removed when it is rewritten "
                "(the previous version is kept in .backups/). Put lasting knowledge in `notes:`."
            )
        warnings += self._override_warnings(original, new_service)
        return ServiceSavePlan(path=path, service=new_service, changes=self.pending.describe(), warnings=warnings)

    async def save(self, callbacks: TrainingCallbacks, writer: ServiceWriter, source_url: str) -> None:
        """Plan, confirm with the trainer, and write the staged changes. Reports errors; never raises."""
        try:
            plan = self.prepare_save(registry=writer.registry, writer=writer, source_url=source_url)
        except Exception as exc:
            await callbacks.show_error(f"Could not prepare the service update: {exc}")
            return
        if plan is None:
            return
        if not await callbacks.confirm_service_update(plan.path, plan.changes, plan.warnings):
            await callbacks.show_message("Service changes not saved.")
            return
        try:
            result = writer.write(plan.service)
        except Exception as exc:
            await callbacks.show_error(f"Could not update {plan.path}: {exc}")
            return
        backup = f" (previous version: {result.backup_path})" if result.backup_path else ""
        await callbacks.show_message(f"Service file updated: {result.path}{backup}")

    def _add_auto_proposals(self, original: ResolvedService, existing: Service | None, source_url: str) -> None:
        if self.pending.url is None and source_url and not (existing and existing.url):
            self.pending.set_url(source_url, origin="auto: training navigation URL")
        # Like the URL: only fill a gap. A configured crop is never replaced by the env debug override.
        file_value = existing.config.chrome_height_px if existing else None
        if (
            self.pending.chrome_height_px is None
            and file_value is None
            and original.source_of("chrome_height_px").startswith("env:")
        ):
            self.pending.set_chrome_height(original.config.chrome_height_px, origin="auto: SOREN_CHROME_CROP_Y")

    def _override_warnings(self, original: ResolvedService, new_service: Service) -> list[str]:
        warnings: list[str] = []
        for field_name in sorted(self.pending.changed_fields()):
            source = original.source_of(field_name)
            if not source.startswith(_HIGHER_LAYER_PREFIXES):
                continue
            if field_name in _CONFIG_FIELDS:
                effective, saved = getattr(original.config, field_name), getattr(new_service.config, field_name)
            else:
                effective, saved = getattr(original, field_name), getattr(new_service, field_name)
            if effective != saved:
                warnings.append(
                    f"{field_name} is overridden by {source} (currently {_display(effective)}); "
                    "the saved value won't take effect until that override is removed."
                )
        return warnings
