"""ServiceRegistry — load service definitions from ``config/services/*.yaml``.

Files are read fresh on every lookup (no caching) so edits made by an
operator or by training write-back are visible to the next task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from soren.models.service import Service, ServiceConfigError
from soren.services.naming import safe_service_name

logger = logging.getLogger(__name__)


def get_services_dir() -> Path:
    """Return the default ``config/services`` directory."""
    return Path(__file__).resolve().parents[3] / "config" / "services"


def load_service_file(path: Path) -> Service:
    """Parse and validate one service file. Raises ServiceConfigError (with the path) on any problem.

    ``name`` defaults to the file stem; an explicit ``name`` must map to the
    same stem so lookups by name are unambiguous.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ServiceConfigError(f"{path}: cannot read service file: {exc}") from exc

    if not isinstance(raw, dict):
        kind = "empty file" if raw is None else type(raw).__name__
        raise ServiceConfigError(f"{path}: expected a YAML mapping, got {kind}")

    name = raw.get("name")
    if name is None:
        raw = {**raw, "name": path.stem}
    elif isinstance(name, str) and safe_service_name(name) != path.stem:
        raise ServiceConfigError(
            f"{path}: name {name!r} does not match the file name; rename the file to "
            f"{safe_service_name(name)}.yaml or remove the name field"
        )

    try:
        return Service.model_validate(raw)
    except ValidationError as exc:
        raise ServiceConfigError(f"{path}: invalid service definition:\n{exc}") from exc


@dataclass(frozen=True)
class ServiceValidationResult:
    """Outcome of validating one service file."""

    path: Path
    service: Service | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ServiceRegistry:
    """Read-only access to service definitions in a directory."""

    def __init__(self, services_dir: Path | None = None) -> None:
        self._dir = services_dir or get_services_dir()

    @property
    def services_dir(self) -> Path:
        return self._dir

    def path_for(self, name: str) -> Path:
        """Path where the definition for ``name`` lives (whether or not it exists)."""
        return self._dir / f"{safe_service_name(name)}.yaml"

    def get(self, name: str) -> Service | None:
        """Load the service called ``name``. None if no file; ServiceConfigError if the file is invalid."""
        path = self.path_for(name)
        if not path.is_file():
            return None
        return load_service_file(path)

    def _files(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return sorted(p for p in self._dir.glob("*.yaml") if p.is_file())

    def list(self) -> list[Service]:
        """All valid services. Invalid files are skipped and logged (use validate_all to report them)."""
        services: list[Service] = []
        for path in self._files():
            try:
                services.append(load_service_file(path))
            except ServiceConfigError as exc:
                logger.warning("Skipping invalid service file %s: %s", path.name, exc)
        return services

    def validate_all(self) -> list[ServiceValidationResult]:
        """Validate every service file, reporting each result (never stops at the first error)."""
        results: list[ServiceValidationResult] = []
        for path in self._files():
            try:
                results.append(ServiceValidationResult(path=path, service=load_service_file(path)))
            except ServiceConfigError as exc:
                results.append(ServiceValidationResult(path=path, error=str(exc)))
        return results
