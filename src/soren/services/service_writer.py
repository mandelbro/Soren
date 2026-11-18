"""ServiceWriter — write config/services/<name>.yaml safely with rotated backups (ADR-0002 Phase 4).

Writes use PyYAML, so ``#`` comments in an existing file are not preserved
(``has_user_comments`` lets callers warn first); knowledge belongs in
``notes:``. Every write is validated before touching disk, lands atomically
(a unique, fsynced temp file + ``os.replace``), can refuse to overwrite a file
that changed since the caller read it, and the previous version is copied to
``<services_dir>/.backups/`` where only the newest ``keep_backups`` per
service are kept. The registry only reads top-level ``*.yaml``, so backups are
never loaded as services.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
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


ABSENT_FINGERPRINT = "absent"


class ServiceFileChangedError(ServiceConfigError):
    """The file changed after the caller read it; nothing was written."""


@dataclass(frozen=True)
class ServiceWriteResult:
    path: Path
    backup_path: Path | None
    created: bool


# A quote opens a quoted scalar only at the start of a value: after these (plus whitespace) or at line start.
_VALUE_STARTERS = ":-[{,?"


def _has_inline_comment(line: str) -> bool:
    """True if ``line`` has a ``#`` comment outside quoted scalars (YAML needs whitespace before it).

    Follows YAML quoting: an apostrophe inside a plain value (``it's``) is literal,
    ``''`` escapes a quote inside single quotes, and ``\\"`` inside double quotes.
    """
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == "'":
            if ch == "'":
                if line[i + 1 : i + 2] == "'":
                    i += 1  # escaped quote
                else:
                    quote = None
        elif quote == '"':
            if ch == "\\":
                i += 1  # skip the escaped character
            elif ch == '"':
                quote = None
        elif ch in ("'", '"'):
            before = line[:i].rstrip()
            if not before or before[-1] in _VALUE_STARTERS:
                quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return True
        i += 1
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

    @staticmethod
    def fingerprint(path: Path) -> str:
        """Identifies the file's current content (``ABSENT_FINGERPRINT`` if missing), for ``expected_fingerprint``."""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return ABSENT_FINGERPRINT

    def write(self, service: Service, *, expected_fingerprint: str | None = None) -> ServiceWriteResult:
        """Validate, back up the current file, and atomically replace it.

        With ``expected_fingerprint`` (from ``fingerprint()`` when the caller read
        the file), raises ServiceFileChangedError instead of overwriting a file that
        changed in between. Raises ServiceConfigError/OSError.
        """
        text = self.render(service)
        path = self._registry.path_for(service.name)
        try:
            round_trip = Service.model_validate(yaml.safe_load(text))
        except (ValidationError, yaml.YAMLError) as exc:
            raise ServiceConfigError(f"{path}: refusing to write invalid service definition:\n{exc}") from exc
        if round_trip.model_dump() != service.model_dump():
            raise ServiceConfigError(f"{path}: service definition does not survive a YAML round trip")

        if expected_fingerprint is not None and self.fingerprint(path) != expected_fingerprint:
            raise ServiceFileChangedError(f"{path} changed since it was read; not overwritten")

        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        backup = None if created else self._backup(path)
        # Unique name in the same directory: concurrent writers can't clobber each
        # other, and os.replace stays an atomic same-filesystem rename.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
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
