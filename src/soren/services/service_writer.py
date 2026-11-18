"""ServiceWriter — write config/services/<name>.yaml safely with rotated backups (ADR-0002 Phase 4).

Writes use PyYAML, so ``#`` comments in an existing file are not preserved
(``has_user_comments`` lets callers warn first); knowledge belongs in
``notes:``. Every write is validated before touching disk, lands atomically
(temp file + ``os.replace``), and the previous version is copied to
``<services_dir>/.backups/`` where only the newest ``keep_backups`` per
service are kept. The registry only reads top-level ``*.yaml``, so backups are
never loaded as services.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from soren.models.service import Service, ServiceConfigError
from soren.services.service_registry import ServiceRegistry

MANAGED_HEADER = "# Written by Soren. `#` comments are not preserved when Soren updates this file; use `notes:`.\n"
BACKUP_DIR_NAME = ".backups"
DEFAULT_KEEP_BACKUPS = 3
_TOP_LEVEL_ORDER = ("version", "name", "type", "url", "discovery_hint", "notes")
_BLOCK_START = re.compile(r"^(\s*)(?:-\s+|[^#'\"]*?:\s+)[|>][-+0-9]*\s*$")


class _ServiceDumper(yaml.SafeDumper):
    """SafeDumper that writes multi-line strings as literal blocks (readable notes)."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_ServiceDumper.add_representer(str, _represent_str)


@dataclass(frozen=True)
class ServiceWriteResult:
    path: Path
    backup_path: Path | None
    created: bool


def _has_inline_comment(line: str) -> bool:
    """True if ``line`` has a ``#`` comment outside quotes (YAML needs whitespace before it)."""
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return True
    return False


class ServiceWriter:
    """Validated, atomic writes of service definitions with bounded backups."""

    def __init__(self, registry: ServiceRegistry | None = None, keep_backups: int = DEFAULT_KEEP_BACKUPS) -> None:
        if keep_backups < 0:
            raise ValueError("keep_backups must be >= 0")
        self._registry = registry or ServiceRegistry()
        self._keep = keep_backups

    @property
    def registry(self) -> ServiceRegistry:
        return self._registry

    def render(self, service: Service) -> str:
        data: dict[str, Any] = service.model_dump(exclude_none=True, mode="json")
        ordered = {key: data[key] for key in _TOP_LEVEL_ORDER if key in data}
        if data.get("config"):
            ordered["config"] = data["config"]
        body = yaml.dump(
            ordered, Dumper=_ServiceDumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
        )
        return MANAGED_HEADER + body

    def write(self, service: Service) -> ServiceWriteResult:
        """Validate, back up the current file, and atomically replace it. Raises ServiceConfigError/OSError."""
        text = self.render(service)
        path = self._registry.path_for(service.name)
        try:
            round_trip = Service.model_validate(yaml.safe_load(text))
        except (ValidationError, yaml.YAMLError) as exc:
            raise ServiceConfigError(f"{path}: refusing to write invalid service definition:\n{exc}") from exc
        if round_trip.model_dump() != service.model_dump():
            raise ServiceConfigError(f"{path}: service definition does not survive a YAML round trip")

        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        backup = None if created else self._backup(path)
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return ServiceWriteResult(path=path, backup_path=backup, created=created)

    def has_user_comments(self, path: Path) -> bool:
        """True if ``path`` contains ``#`` comments (other than Soren's header) that a rewrite would drop."""
        if not path.is_file():
            return False
        block_indent: int | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line == MANAGED_HEADER.rstrip("\n"):
                continue
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if block_indent is not None:
                if not stripped or indent > block_indent:
                    continue  # inside a literal/folded block scalar
                block_indent = None
            if _has_inline_comment(line):
                return True
            match = _BLOCK_START.match(line)
            if match:
                block_indent = len(match.group(1))
        return False

    # --- backups ---

    def _backup(self, path: Path) -> Path | None:
        if self._keep == 0:
            return None
        backup_dir = path.parent / BACKUP_DIR_NAME
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_dir / f"{path.stem}.{stamp}.yaml.bak"
        counter = 1
        while backup.exists():  # two writes within the same microsecond
            backup = backup_dir / f"{path.stem}.{stamp}-{counter}.yaml.bak"
            counter += 1
        shutil.copy2(path, backup)
        self._prune(backup_dir, path.stem)
        return backup

    def _prune(self, backup_dir: Path, stem: str) -> None:
        backups = sorted(backup_dir.glob(f"{stem}.*.yaml.bak"))
        for old in backups[: -self._keep]:
            old.unlink(missing_ok=True)
