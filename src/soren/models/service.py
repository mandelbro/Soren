"""Service definition models for ``config/services/<name>.yaml``.

A *service* is a website or desktop application that agents operate on
(e.g. a survey panel, Google Calendar, Slack). The service file holds
service knowledge shared by every persona and agent that uses it: where it
lives, how much window chrome sits above the usable area, which keyboard
shortcuts exist, and tactical instructions. Credentials stay persona-bound.

``ServiceConfig`` is also the shape of a ``settings.yaml`` ``services.<name>``
override entry, so the same fields can be layered. Every field defaults to
``None`` meaning "not set at this layer"; built-in defaults are applied only
by resolution (see ``soren.services.service_resolution`` and ADR-0002).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SERVICE_SCHEMA_VERSION = 1

EnterKeyBehavior = Literal["enter_only", "escape_enter"]
ENTER_KEY_BEHAVIORS: tuple[str, ...] = ("enter_only", "escape_enter")


# Window chrome taller than this is a typo on every supported resolution
# (Safari's chrome is ~100px at 1024x768, ~200px on a 2x framebuffer).
MAX_CHROME_HEIGHT_PX = 500


class ServiceConfigError(Exception):
    """A service definition file is unreadable or invalid."""


class ServiceNotFoundError(ServiceConfigError):
    """A persona explicitly references a service that has no definition file."""


def _strip_nonblank(values: list[str] | None, what: str) -> list[str] | None:
    if values is None:
        return None
    cleaned = [v.strip() for v in values]
    if any(not v for v in cleaned):
        raise ValueError(f"{what} entries must be non-empty strings")
    return cleaned


class KeyboardShortcut(BaseModel):
    """A shortcut the agent may use on this service (prompt hint; Soren never dispatches it itself).

    ``keys`` accepts ``"cmd+k"`` or ``["cmd", "k"]`` and is normalized to a
    lowercase token list. The ``+`` key itself is only expressible in list
    form (``["cmd", "+"]``).
    """

    model_config = ConfigDict(extra="forbid")

    keys: list[str]
    action: str
    when: str | None = None

    @field_validator("keys", mode="before")
    @classmethod
    def _normalize_keys(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            tokens = [t.strip().lower() for t in value.split("+")]
        elif isinstance(value, list):
            tokens = []
            for raw in value:
                if not isinstance(raw, str):
                    raise ValueError("keys entries must be strings")
                token = raw.strip().lower()
                if "+" in token and token != "+":
                    raise ValueError(f"key {raw!r} contains '+'; list each key separately")
                tokens.append(token)
        else:
            raise ValueError("keys must be a string like 'cmd+k' or a list like ['cmd', 'k']")
        if not tokens or any(not t for t in tokens):
            raise ValueError(f"malformed shortcut keys: {value!r}")
        return tokens

    @field_validator("action")
    @classmethod
    def _action_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("action must describe what the shortcut does")
        return value


class ServiceConfig(BaseModel):
    """Layerable per-service behavior settings. ``None`` = not set at this layer."""

    model_config = ConfigDict(extra="forbid")

    chrome_height_px: int | None = Field(
        default=None,
        ge=0,
        le=MAX_CHROME_HEIGHT_PX,
        description="Pixels of window chrome above the usable content area; cropped from agent screenshots",
    )
    enter_key_behavior: EnterKeyBehavior | None = None
    step_budget: int | None = Field(default=None, ge=1, description="Max agent steps per task")
    typing_delay_ms: int | None = Field(default=None, ge=0)
    action_delay_ms: int | None = Field(default=None, ge=0)
    cooldown_between_tasks_s: int | None = Field(default=None, ge=0)
    required_apps: list[str] | None = Field(default=None, description="Apps that must stay open for this service")
    keyboard_shortcuts: list[KeyboardShortcut] | None = None
    instructions: list[str] | None = Field(default=None, description="Tactical instructions for the agent")
    include_notes_in_prompt: bool | None = Field(
        default=None, description="Also give the service's free-form `notes` to the agent (default false)"
    )

    @field_validator("required_apps")
    @classmethod
    def _apps_nonblank(cls, value: list[str] | None) -> list[str] | None:
        return _strip_nonblank(value, "required_apps")

    @field_validator("instructions")
    @classmethod
    def _instructions_nonblank(cls, value: list[str] | None) -> list[str] | None:
        return _strip_nonblank(value, "instructions")

    def set_values(self) -> dict[str, Any]:
        """Return only fields explicitly set to a non-null value at this layer."""
        return {name: getattr(self, name) for name in self.model_fields_set if getattr(self, name) is not None}


class Service(BaseModel):
    """A service definition loaded from ``config/services/<name>.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=SERVICE_SCHEMA_VERSION, ge=1)
    name: str
    type: str | None = None
    url: str | None = None
    discovery_hint: str | None = None
    notes: str | None = Field(default=None, description="Free-form knowledge about the service (multi-line text)")
    config: ServiceConfig = Field(default_factory=ServiceConfig)

    @field_validator("version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value > SERVICE_SCHEMA_VERSION:
            raise ValueError(
                f"service file version {value} is newer than supported version "
                f"{SERVICE_SCHEMA_VERSION}. Upgrade Soren to read this file."
            )
        return value

    @field_validator("name")
    @classmethod
    def _name_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must be non-empty")
        return value

    @field_validator("config", mode="before")
    @classmethod
    def _null_config_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value
