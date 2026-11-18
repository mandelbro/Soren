"""Shared factories for tests that need a resolved service (config/services/*.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from soren.services.config_loader import SorenSettings
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import ResolvedService, resolve_service


def write_service(services_dir: Path, name: str, **fields: Any) -> Path:
    """Write ``<services_dir>/<name>.yaml`` from keyword fields (``config=`` for the config block)."""
    services_dir.mkdir(parents=True, exist_ok=True)
    path = services_dir / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"name": name, **fields}, sort_keys=False), encoding="utf-8")
    return path


def make_resolved_service(
    tmp_path: Path,
    name: str = "svc",
    *,
    config: dict[str, Any] | None = None,
    settings: SorenSettings | None = None,
    environ: dict[str, str] | None = None,
    **fields: Any,
) -> ResolvedService:
    """Write a service file under ``tmp_path/services`` and resolve it in isolation from the real env."""
    services_dir = tmp_path / "services"
    write_service(services_dir, name, config=config or {}, **fields)
    return resolve_service(
        name,
        registry=ServiceRegistry(services_dir),
        settings=settings or SorenSettings(),
        environ=environ or {},
    )
