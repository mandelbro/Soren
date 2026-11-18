"""Shared builders for resolve_service() tests: isolated registry/settings and a full example service file."""

from __future__ import annotations

from pathlib import Path

from soren.models.service import ServiceConfig
from soren.services.config_loader import SorenSettings
from soren.services.service_registry import ServiceRegistry

NO_ENV: dict[str, str] = {}


def make_registry(tmp_path: Path, files: dict[str, str] | None = None) -> ServiceRegistry:
    services_dir = tmp_path / "services"
    services_dir.mkdir(exist_ok=True)
    for filename, text in (files or {}).items():
        (services_dir / filename).write_text(text, encoding="utf-8")
    return ServiceRegistry(services_dir)


def make_settings(services: dict[str, dict] | None = None, enter_key_behavior: str | None = None) -> SorenSettings:
    return SorenSettings.model_validate(
        {
            "services": {k: ServiceConfig.model_validate(v) for k, v in (services or {}).items()},
            "enter_key_behavior": enter_key_behavior,
        }
    )


ACME_FILE = """\
name: acmepanel
type: survey_platform
url: https://file.example
discovery_hint: from file
notes: file notes
config:
  chrome_height_px: 100
  enter_key_behavior: escape_enter
  step_budget: 40
  typing_delay_ms: 70
  keyboard_shortcuts:
    - {keys: w, action: Week view}
    - {keys: m, action: Month view}
  instructions: [file instruction]
"""
