"""Configuration loader for Soren settings."""

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from soren.models.llm import LLMConfig
from soren.models.playbooks import PlaybooksConfig
from soren.models.scheduler import SchedulerConfig
from soren.models.service import ENTER_KEY_BEHAVIORS, EnterKeyBehavior, ServiceConfig
from soren.models.verification import VerificationConfig
from soren.models.vlm import VLMConfig
from soren.models.vm import VMConfig

logger = logging.getLogger(__name__)


class SorenSettings(BaseModel):
    """Soren application settings."""

    vm: VMConfig = VMConfig()
    llm: LLMConfig = LLMConfig()
    vlm: VLMConfig = VLMConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    verification: VerificationConfig = VerificationConfig()
    playbooks: PlaybooksConfig = PlaybooksConfig()
    # Global per-service overrides (settings.yaml `services.<name>`); highest
    # config-file precedence for that service. See ADR-0002.
    services: dict[str, ServiceConfig] = Field(default_factory=dict)
    # Global defaults for every service (settings.yaml `service_defaults`), below service files.
    service_defaults: ServiceConfig = Field(default_factory=ServiceConfig)
    # Legacy global default (settings.yaml top-level key), below service_defaults. None = unset.
    enter_key_behavior: EnterKeyBehavior | None = None


def _parse_service_config_entry(label: str, entry: Any) -> ServiceConfig | None:
    """Leniently parse one ServiceConfig-shaped mapping; None if it is not a mapping.

    The pre-ADR-0002 reader (patches.enter_key_config) silently ignored
    unknown keys and invalid values, so existing settings files may contain
    them. Those are dropped with a warning rather than raising.
    """
    if entry is None:
        entry = {}
    if not isinstance(entry, dict):
        logger.warning("settings.yaml %s must be a mapping; ignoring it", label)
        return None
    kept: dict[str, Any] = {}
    for key, value in entry.items():
        if key not in ServiceConfig.model_fields:
            logger.warning("settings.yaml %s: unknown key %r ignored", label, key)
            continue
        try:
            ServiceConfig.model_validate({key: value})
        except ValidationError as exc:
            logger.warning(
                "settings.yaml %s: invalid %s=%r ignored (%s)",
                label,
                key,
                value,
                exc.errors()[0].get("msg", "invalid"),
            )
            continue
        kept[key] = value
    return ServiceConfig.model_validate(kept)


def _parse_service_overrides(raw: Any) -> dict[str, ServiceConfig]:
    """Parse the `services:` block (service name -> ServiceConfig overrides) leniently."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning("settings.yaml `services` must be a mapping of service name -> overrides; ignoring it")
        return {}
    parsed: dict[str, ServiceConfig] = {}
    for name, entry in raw.items():
        cfg = _parse_service_config_entry(f"services.{name}", entry)
        if cfg is not None:
            parsed[str(name)] = cfg
    return parsed


def _parse_enter_key_default(raw: Any) -> EnterKeyBehavior | None:
    if raw is None:
        return None
    if raw in ENTER_KEY_BEHAVIORS:
        from soren.services.service_deprecations import TOP_LEVEL_ENTER_KEY_MESSAGE, warn_once

        warn_once("top_level_enter_key_behavior", TOP_LEVEL_ENTER_KEY_MESSAGE)
        return raw
    logger.warning("settings.yaml enter_key_behavior=%r is not one of %s; ignored", raw, ", ".join(ENTER_KEY_BEHAVIORS))
    return None


# settings.yaml section -> model. Sections are validated strictly (invalid values raise),
# unlike the lenient legacy `services` / `service_defaults` / `enter_key_behavior` keys.
_SECTION_MODELS: dict[str, type[BaseModel]] = {
    "vm": VMConfig,
    "llm": LLMConfig,
    "vlm": VLMConfig,
    "scheduler": SchedulerConfig,
    "verification": VerificationConfig,
    "playbooks": PlaybooksConfig,
}


def get_default_settings_path() -> Path:
    """Return the repo's ``config/settings.yaml`` path."""
    return Path(__file__).resolve().parents[3] / "config" / "settings.yaml"


def load_settings(path: Path | None = None) -> SorenSettings:
    """Load settings from YAML, merging with defaults."""
    config_path = path or get_default_settings_path()
    defaults = SorenSettings()

    if not config_path.exists():
        return defaults

    raw = yaml.safe_load(config_path.read_text() or "{}")
    if not raw:
        return defaults

    # Strictly validated sections; a missing, empty, or null section keeps its defaults.
    sections = {name: model.model_validate(raw[name]) for name, model in _SECTION_MODELS.items() if raw.get(name)}
    return SorenSettings.model_validate(
        {
            **sections,
            "services": _parse_service_overrides(raw.get("services")),
            "service_defaults": _parse_service_config_entry("service_defaults", raw.get("service_defaults"))
            or ServiceConfig(),
            "enter_key_behavior": _parse_enter_key_default(raw.get("enter_key_behavior")),
        }
    )
