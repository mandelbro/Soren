"""Task source configuration for persona discovery."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TaskSourceType(str, Enum):
    """Task source type."""

    survey_platform = "survey_platform"
    email = "email"
    generic_form = "generic_form"


class TaskSource(BaseModel):
    """External service where a persona discovers tasks.

    Legacy form: all service details inline (``name``, ``url``, ``type``, ...).
    Binding form: ``service: <name>`` references ``config/services/<name>.yaml``;
    ``name`` then defaults to the service name. Inline fields that are
    explicitly set still override the service file (see ADR-0002).
    """

    name: str
    service: str | None = None
    type: str = "survey_platform"
    url: str = ""
    credential_ref: str = ""
    discovery_hint: str | None = None
    typing_delay_ms: int = Field(default=50, ge=0)
    action_delay_ms: int = Field(default=500, ge=0)
    cooldown_between_tasks_s: int = Field(default=30, ge=0)
    enabled: bool = True

    model_config = {"from_attributes": True, "extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _name_defaults_to_service(cls, data: Any) -> Any:
        """Fill a blank ``name`` from ``service``; never rewrite a non-blank legacy name."""
        if not isinstance(data, dict):
            return data
        name = data.get("name")
        service = data.get("service")
        service = service.strip() if isinstance(service, str) else service
        data = dict(data)
        if "service" in data:
            data["service"] = service or None
        if not (isinstance(name, str) and name.strip()):
            if not service:
                raise ValueError("task source needs a 'name' or a 'service' reference")
            data["name"] = service
        return data

    @property
    def service_key(self) -> str:
        """Name used to look up the service definition and settings overrides."""
        return self.service or self.name
