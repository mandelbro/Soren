"""Task source configuration for persona discovery."""

from enum import Enum

from pydantic import BaseModel, Field


class TaskSourceType(str, Enum):
    """Task source type."""

    survey_platform = "survey_platform"
    email = "email"
    generic_form = "generic_form"


class TaskSource(BaseModel):
    """External service where a persona discovers tasks."""

    name: str
    type: str = "survey_platform"
    url: str = ""
    credential_ref: str = ""
    discovery_hint: str | None = None
    typing_delay_ms: int = Field(default=50, ge=0)
    action_delay_ms: int = Field(default=500, ge=0)
    cooldown_between_tasks_s: int = Field(default=30, ge=0)
    enabled: bool = True

    model_config = {"from_attributes": True, "extra": "ignore"}
