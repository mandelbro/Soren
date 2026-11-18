"""Resolve the effective configuration for a service by layering every config surface.

Precedence, highest first (ADR-0002):

1. ``SOREN_CHROME_CROP_Y`` env var -- ``chrome_height_px`` only; operator debug override
2. ``settings.yaml`` ``services.<name>`` -- global per-service overrides
3. persona ``task_sources[]`` fields that were explicitly set (legacy inline config)
4. ``config/services/<name>.yaml`` -- the service definition
5. ``settings.yaml`` ``service_defaults``, then the legacy top-level ``enter_key_behavior``
6. built-in defaults

Scalars from a higher layer replace lower ones; lists are replaced, never
merged (an explicit ``[]`` clears a lower layer). Resolution is pure: it never
mutates the environment, the settings, or the registry, and every resolved
field records which layer it came from (``provenance``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from soren.models.service import (
    ENTER_KEY_BEHAVIORS,
    EnterKeyBehavior,
    KeyboardShortcut,
    Service,
    ServiceConfig,
    ServiceNotFoundError,
)
from soren.models.task_source import TaskSource
from soren.services.persona_context import DEFAULT_STEP_BUDGET

if TYPE_CHECKING:
    from soren.models.persona import Persona
    from soren.models.task import Task
    from soren.services.config_loader import SorenSettings
    from soren.services.service_registry import ServiceRegistry

logger = logging.getLogger(__name__)

CHROME_HEIGHT_ENV_VAR = "SOREN_CHROME_CROP_Y"

# Service-level (non-config) fields, derived from the model so new fields are never missed.
_SERVICE_FIELDS = tuple(f for f in Service.model_fields if f not in {"version", "name", "config"})
# TaskSource fields that map 1:1 onto resolved fields (persona layer).
PERSONA_FIELDS = ("type", "url", "discovery_hint", "typing_delay_ms", "action_delay_ms", "cooldown_between_tasks_s")


def _task_source_default(field_name: str) -> Any:
    return TaskSource.model_fields[field_name].default


# Built-in defaults. Legacy TaskSource defaults are reused so both paths agree.
BUILTIN_DEFAULTS: dict[str, Any] = {
    "type": _task_source_default("type"),
    "url": _task_source_default("url"),
    "discovery_hint": None,
    "notes": None,
    "chrome_height_px": 0,
    "enter_key_behavior": "enter_only",
    "step_budget": DEFAULT_STEP_BUDGET,
    "typing_delay_ms": _task_source_default("typing_delay_ms"),
    "action_delay_ms": _task_source_default("action_delay_ms"),
    "cooldown_between_tasks_s": _task_source_default("cooldown_between_tasks_s"),
    "required_apps": [],
    "keyboard_shortcuts": [],
    "instructions": [],
    "include_notes_in_prompt": False,
}


class ResolvedServiceConfig(BaseModel):
    """Fully-populated effective ServiceConfig (no unset fields)."""

    chrome_height_px: int
    enter_key_behavior: EnterKeyBehavior
    step_budget: int
    typing_delay_ms: int
    action_delay_ms: int
    cooldown_between_tasks_s: int
    required_apps: list[str]
    keyboard_shortcuts: list[KeyboardShortcut]
    instructions: list[str]
    include_notes_in_prompt: bool


class ResolvedService(BaseModel):
    """Effective service definition plus where each value came from."""

    name: str
    type: str
    url: str
    discovery_hint: str | None
    notes: str | None
    config: ResolvedServiceConfig
    service_file: Path | None = None
    provenance: dict[str, str] = Field(default_factory=dict)

    def source_of(self, field_name: str) -> str:
        """Layer label that supplied ``field_name`` (raises KeyError for unknown fields)."""
        return self.provenance[field_name]


def parse_chrome_height_env(environ: Mapping[str, str]) -> int | None:
    """Parse ``SOREN_CHROME_CROP_Y``. None when unset or malformed; negatives clamp to 0 (legacy)."""
    raw = environ.get(CHROME_HEIGHT_ENV_VAR)
    if raw is None:
        return None
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return None


def _env_layer(environ: Mapping[str, str]) -> dict[str, Any]:
    value = parse_chrome_height_env(environ)
    return {} if value is None else {"chrome_height_px": value}


def explicit_persona_values(source: TaskSource) -> dict[str, Any]:
    """Service values a persona task source sets explicitly (model defaults and blank strings excluded).

    This is the persona layer of resolution, and what ``soren services migrate`` moves into service files.
    """
    values: dict[str, Any] = {}
    for field_name in PERSONA_FIELDS:
        if field_name not in source.model_fields_set:
            continue
        value = getattr(source, field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue  # explicit empty string == unset
        values[field_name] = value
    return values


def _settings_services_layer(
    settings: SorenSettings, service_name: str, source: TaskSource | None
) -> tuple[str, dict[str, Any]] | None:
    overrides = getattr(settings, "services", None)
    if not isinstance(overrides, dict):  # tolerate partial/mock settings objects
        return None
    for key in (service_name, source.name if source else None):
        if key and key in overrides and isinstance(overrides[key], ServiceConfig):
            return f"settings.services.{key}", overrides[key].set_values()
    return None


def _settings_default_layers(settings: SorenSettings) -> list[tuple[str, dict[str, Any]]]:
    layers: list[tuple[str, dict[str, Any]]] = []
    defaults = getattr(settings, "service_defaults", None)
    if isinstance(defaults, ServiceConfig):
        layers.append(("settings.service_defaults", defaults.set_values()))
    legacy_enter = getattr(settings, "enter_key_behavior", None)
    if legacy_enter in ENTER_KEY_BEHAVIORS:
        layers.append(("settings", {"enter_key_behavior": legacy_enter}))
    return layers


def _warn_if_env_overrides(service_name: str, layers: list[tuple[str, dict[str, Any]]]) -> None:
    """Warn when the env var silently replaces a different configured chrome height."""
    env_label, env_values = layers[0]
    if "chrome_height_px" not in env_values:
        return
    for label, values in layers[1:]:
        if label == "default":
            return
        if "chrome_height_px" in values:
            if values["chrome_height_px"] != env_values["chrome_height_px"]:
                logger.warning(
                    "%s=%s overrides chrome_height_px=%s from %s for service %r. "
                    "Unset the env var (check .env) to use the configured value.",
                    CHROME_HEIGHT_ENV_VAR,
                    env_values["chrome_height_px"],
                    values["chrome_height_px"],
                    label,
                    service_name,
                )
            return


def resolve_service(
    service_name: str,
    *,
    registry: ServiceRegistry,
    settings: SorenSettings,
    source: TaskSource | None = None,
    persona_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedService:
    """Resolve the effective configuration for ``service_name``.

    Raises ServiceConfigError if the service file is invalid, and
    ServiceNotFoundError if ``source`` explicitly references a service
    (``service:``) that has no definition file. A legacy source (no
    ``service:`` field) without a file resolves from persona values.
    """
    env = os.environ if environ is None else environ

    service = registry.get(service_name)
    if service is None and source is not None and source.service:
        raise ServiceNotFoundError(
            f"task source {source.name!r} references service {service_name!r}, "
            f"but {registry.path_for(service_name)} does not exist"
        )
    service_file = registry.path_for(service_name) if service is not None else None

    layers: list[tuple[str, dict[str, Any]]] = [(f"env:{CHROME_HEIGHT_ENV_VAR}", _env_layer(env))]
    settings_layer = _settings_services_layer(settings, service_name, source)
    if settings_layer is not None:
        layers.append(settings_layer)
    if source is not None:
        layers.append((f"persona:{persona_id}" if persona_id else "persona", explicit_persona_values(source)))
    if service is not None:
        file_values = {f: getattr(service, f) for f in _SERVICE_FIELDS if getattr(service, f) is not None}
        file_values.update(service.config.set_values())
        layers.append((f"service_file:{service_file}", file_values))
    layers.extend(_settings_default_layers(settings))
    layers.append(("default", BUILTIN_DEFAULTS))
    _warn_if_env_overrides(service_name, layers)

    resolved: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for field_name in (*_SERVICE_FIELDS, *ServiceConfig.model_fields):
        for label, values in layers:
            if field_name in values:
                resolved[field_name] = values[field_name]
                provenance[field_name] = label
                break

    config = ResolvedServiceConfig.model_validate(
        {f: resolved[f] for f in ServiceConfig.model_fields},
    ).model_copy(deep=True)
    return ResolvedService(
        name=service_name,
        type=resolved["type"],
        url=resolved["url"],
        discovery_hint=resolved["discovery_hint"],
        notes=resolved["notes"],
        config=config,
        service_file=service_file,
        provenance=provenance,
    )


def iter_matching_sources(personas: Iterable[Persona], name: str) -> Iterator[tuple[Persona, TaskSource]]:
    """Yield (persona, source) pairs whose source name *or* service reference equals ``name``, in load order."""
    for persona in personas:
        for source in persona.task_sources:
            if name in (source.name, source.service_key):
                yield persona, source


class SourceSelectionError(ValueError):
    """A requested persona does not exist, or has no task source for the requested name."""


def select_task_sources(
    personas: Iterable[Persona], name: str, persona_id: str | None = None
) -> list[tuple[Persona, TaskSource]]:
    """Persona task sources matching ``name`` (by source name or ``service:`` ref), in load order.

    With ``persona_id``, only that persona is considered and it must exist and
    have a match (SourceSelectionError otherwise). Without it, an empty list
    means nothing matched and callers fall back to ``name`` as a service name.
    """
    candidates = list(personas)
    if persona_id is not None:
        candidates = [p for p in candidates if p.id == persona_id]
        if not candidates:
            raise SourceSelectionError(f"Persona {persona_id!r} not found.")
    matches = list(iter_matching_sources(candidates, name))
    if persona_id is not None and not matches:
        raise SourceSelectionError(f"Persona {persona_id!r} has no task source for {name!r}.")
    return matches


def find_task_source(persona: Persona, source_name: str) -> TaskSource | None:
    """The persona's task source named ``source_name`` (first match), if any."""
    for source in persona.task_sources:
        if source.name == source_name:
            return source
    return None


def resolve_for_task(
    task: Task,
    persona: Persona,
    *,
    registry: ServiceRegistry,
    settings: SorenSettings,
    environ: Mapping[str, str] | None = None,
) -> ResolvedService | None:
    """Resolve the service a task runs on. None when the task has no source.

    Uses the persona's matching task source (and its ``service:`` reference)
    when present, otherwise the task's ``source_name`` as the service name.
    Raises ServiceConfigError / ServiceNotFoundError like ``resolve_service``.
    """
    if not task.source_name:
        return None
    source = find_task_source(persona, task.source_name)
    return resolve_service(
        source.service_key if source else task.source_name,
        registry=registry,
        settings=settings,
        source=source,
        persona_id=persona.id or None,
        environ=environ,
    )
