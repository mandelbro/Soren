"""Deprecation notices for legacy service configuration (ADR-0002 Phase 5).

Legacy surfaces keep working; these notices point at the replacement:

- ``SOREN_CHROME_CROP_Y``: a global debug override. Configure ``chrome_height_px``
  per service (or in ``settings.yaml`` ``service_defaults``) instead.
- Top-level ``enter_key_behavior`` in ``settings.yaml``: move it under ``service_defaults``.
- Persona task sources with inline service values: move them to
  ``config/services/<name>.yaml`` (``soren services migrate``), or drop values
  that merely repeat the service file.

``collect_notices`` feeds ``soren services validate``. ``warn_once`` is used by
the runtime paths and logs each warning once per process.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from soren.models.persona import Persona
from soren.models.service import ServiceConfigError
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import explicit_persona_values, parse_chrome_height_env

if TYPE_CHECKING:
    from soren.services.config_loader import SorenSettings

logger = logging.getLogger(__name__)

_WARNED: set[str] = set()
_TOP_LEVEL_FIELDS = ("type", "url", "discovery_hint")

TOP_LEVEL_ENTER_KEY_MESSAGE = (
    "settings.yaml top-level `enter_key_behavior` is deprecated; move it under `service_defaults:` "
    "(same value) or set it per service in config/services/<name>.yaml."
)


def env_crop_message(value: int) -> str:
    return (
        f"SOREN_CHROME_CROP_Y={value} overrides chrome_height_px for every service. Configure chrome_height_px "
        "in config/services/<name>.yaml (or settings.yaml service_defaults) and keep the env var for debugging only "
        "(check .env)."
    )


@dataclass(frozen=True)
class Notice:
    level: Literal["deprecated", "info"]
    message: str


def warn_once(key: str, message: str) -> None:
    """Log ``message`` as a warning the first time ``key`` is seen in this process."""
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(message)


def reset_warn_once() -> None:
    """Forget which warnings were logged (tests)."""
    _WARNED.clear()


def collect_notices(
    settings: SorenSettings,
    personas: list[Persona],
    registry: ServiceRegistry,
    environ: Mapping[str, str],
) -> list[Notice]:
    """Deprecated or redundant legacy configuration, most important first."""
    notices: list[Notice] = []
    env_value = parse_chrome_height_env(environ)
    if env_value is not None:
        notices.append(Notice("deprecated", env_crop_message(env_value)))
    if getattr(settings, "enter_key_behavior", None) is not None:
        notices.append(Notice("deprecated", TOP_LEVEL_ENTER_KEY_MESSAGE))
    for persona in personas:
        for source in persona.task_sources:
            notice = _persona_source_notice(persona, source, registry)
            if notice:
                notices.append(notice)
    return notices


def _persona_source_notice(persona: Persona, source: Any, registry: ServiceRegistry) -> Notice | None:
    values = explicit_persona_values(source)
    if not values:
        return None
    label = f"persona {persona.id!r} source {source.name!r}"
    try:
        service = registry.get(source.service_key)
    except ServiceConfigError:
        return None  # invalid files are reported by validation itself
    path = registry.path_for(source.service_key)
    if service is None:
        return Notice(
            "info",
            f"{label} keeps service settings inline ({', '.join(sorted(values))}); "
            f"run `soren services migrate` to move them to {path}.",
        )
    file_values = {f: getattr(service, f) for f in _TOP_LEVEL_FIELDS if getattr(service, f) is not None}
    file_values.update(service.config.set_values())
    repeats = sorted(f for f, v in values.items() if f in file_values and file_values[f] == v)
    if not repeats:
        return None
    return Notice("info", f"{label} repeats {', '.join(repeats)} from {path.name}; it can be removed from the persona.")
