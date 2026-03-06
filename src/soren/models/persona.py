"""Persona model for agent identity and behavior."""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from soren.models.task_source import TaskSource


class Demographics(BaseModel):
    """Demographic profile for persona identity."""

    age: str | None = None
    occupation: str | None = None
    education: str | None = None
    location: str | None = None

    model_config = {"from_attributes": True, "extra": "ignore"}


class Traits(BaseModel):
    """Big Five personality traits (0.0–1.0)."""

    conscientiousness: float = Field(default=0.5, ge=0.0, le=1.0)
    openness: float = Field(default=0.5, ge=0.0, le=1.0)
    agreeableness: float = Field(default=0.5, ge=0.0, le=1.0)
    extraversion: float = Field(default=0.5, ge=0.0, le=1.0)
    neuroticism: float = Field(default=0.5, ge=0.0, le=1.0)

    model_config = {"from_attributes": True, "extra": "ignore"}


class Preferences(BaseModel):
    """Behavioral preferences for response style."""

    response_style: str = "balanced"
    formality: str = "professional"
    decision_speed: str = "moderate"

    model_config = {"from_attributes": True, "extra": "ignore"}


class Persona(BaseModel):
    """YAML-defined agent identity with traits and task sources."""

    name: str
    id: str = ""
    description: str = ""
    demographics: Demographics | dict[str, Any] = Field(default_factory=Demographics)
    traits: Traits | dict[str, Any] = Field(default_factory=Traits)
    preferences: Preferences | dict[str, Any] = Field(default_factory=Preferences)
    task_sources: list[TaskSource] = Field(default_factory=list)
    enabled: bool = True

    model_config = {"from_attributes": True, "extra": "ignore"}

    @model_validator(mode="after")
    def set_default_id(self) -> "Persona":
        if not self.id:
            object.__setattr__(self, "id", self.name.lower().replace(" ", "-"))
        return self

    def get_trait(self, name: str) -> float:
        """Get trait value (0.0–1.0), normalizing dict or Traits model."""
        t = self.traits
        if isinstance(t, dict):
            return float(t.get(name, 0.5))
        return getattr(t, name, 0.5)

    def get_demographic(self, name: str) -> str | None:
        """Get demographic value, normalizing dict or Demographics model."""
        d = self.demographics
        if isinstance(d, dict):
            val = d.get(name)
            return str(val) if val is not None else None
        return getattr(d, name, None)

    def get_preference(self, name: str) -> str:
        """Get preference value, normalizing dict or Preferences model."""
        p = self.preferences
        if isinstance(p, dict):
            return str(p.get(name, ""))
        return getattr(p, name, "")
