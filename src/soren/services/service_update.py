"""ServiceUpdate — service-definition changes staged during training, applied at /done (ADR-0002 Phase 4)."""

from __future__ import annotations

import re
from typing import Any

from soren.models.service import KeyboardShortcut, Service
from soren.services.service_prompt import format_shortcut

# Line breaks YAML may rewrite on load (e.g. NEL is written raw and read back as a
# space). Normalizing pasted text keeps writes lossless and notes in `|` blocks.
_LINE_BREAKS = re.compile(r"\r\n|[\r\x85\u2028\u2029]")


def _clean(text: str, what: str) -> str:
    text = _LINE_BREAKS.sub("\n", text).strip()
    if not text:
        raise ValueError(f"{what} must not be empty")
    return text


class ServiceUpdate:
    """Pending changes to one service definition. Scalars replace; lists/notes append."""

    def __init__(self) -> None:
        self.discard()

    def discard(self) -> None:
        self.url: str | None = None
        self.chrome_height_px: int | None = None
        self.shortcuts: list[KeyboardShortcut] = []
        self.instructions: list[str] = []
        self.notes: list[str] = []
        self.origins: dict[str, str] = {}

    # --- staging ---

    def set_url(self, url: str, origin: str = "training") -> None:
        self.url = _clean(url, "url")
        self.origins["url"] = origin

    def set_chrome_height(self, px: int, origin: str = "/crop") -> None:
        if px < 0:
            raise ValueError("chrome height must be >= 0")
        self.chrome_height_px = px
        self.origins["chrome_height_px"] = origin

    def add_shortcut(self, shortcut: KeyboardShortcut) -> None:
        self.shortcuts = [s for s in self.shortcuts if s.keys != shortcut.keys] + [shortcut]

    def add_instruction(self, text: str) -> None:
        text = _clean(text, "instruction")
        if text not in self.instructions:
            self.instructions.append(text)

    def add_note(self, text: str) -> None:
        self.notes.append(_clean(text, "note"))

    # --- inspection ---

    @property
    def is_empty(self) -> bool:
        return not self.changed_fields()

    def changed_fields(self) -> set[str]:
        fields = {"url": self.url is not None, "chrome_height_px": self.chrome_height_px is not None}
        fields |= {"keyboard_shortcuts": bool(self.shortcuts), "instructions": bool(self.instructions)}
        fields["notes"] = bool(self.notes)
        return {name for name, changed in fields.items() if changed}

    def describe(self) -> list[str]:
        lines: list[str] = []
        if self.url is not None:
            lines.append(f"url: {self.url} ({self.origins.get('url', 'training')})")
        if self.chrome_height_px is not None:
            lines.append(f"config.chrome_height_px: {self.chrome_height_px} ({self.origins.get('chrome_height_px')})")
        lines += [f"config.keyboard_shortcuts += {format_shortcut(s)}" for s in self.shortcuts]
        lines += [f"config.instructions += {i}" for i in self.instructions]
        lines += [f"notes += {n}" for n in self.notes]
        return lines

    # --- application ---

    def apply_to(self, existing: Service | None, *, name: str, seed: dict[str, Any] | None = None) -> Service:
        """Return a new Service with the staged changes applied (``existing`` is not mutated).

        ``seed`` supplies top-level fields (e.g. type/url) only when creating a new file.
        """
        data: dict[str, Any] = (
            existing.model_dump(exclude_none=True) if existing else {"name": name, **(seed or {}), "config": {}}
        )
        config: dict[str, Any] = data.setdefault("config", {})
        if self.url is not None:
            data["url"] = self.url
        if self.chrome_height_px is not None:
            config["chrome_height_px"] = self.chrome_height_px
        if self.shortcuts:
            # Same keys replace the file's entry in place; new keys are appended.
            staged = {tuple(sc.keys): sc.model_dump(exclude_none=True) for sc in self.shortcuts}
            merged = [staged.pop(tuple(sc["keys"]), sc) for sc in config.get("keyboard_shortcuts", [])]
            config["keyboard_shortcuts"] = merged + list(staged.values())
        if self.instructions:
            current = list(config.get("instructions", []))
            config["instructions"] = current + [i for i in self.instructions if i not in current]
        if self.notes:
            base = (data.get("notes") or "").rstrip()
            paragraphs = ([base] if base else []) + self.notes
            data["notes"] = "\n\n".join(paragraphs) + "\n"
        return Service.model_validate(data)
