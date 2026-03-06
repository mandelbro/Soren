"""Scheduler configuration for retry and backoff."""

from pydantic import BaseModel, Field, model_validator


class PriorityWeights(BaseModel):
    """Weights for composite task priority scoring (should sum to ~1.0)."""

    reward: float = Field(default=0.3, ge=0.0, le=1.0)
    persona_fit: float = Field(default=0.2, ge=0.0, le=1.0)
    success_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    backlog_age: float = Field(default=0.3, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def normalize_weights(self) -> "PriorityWeights":
        total = self.reward + self.persona_fit + self.success_rate + self.backlog_age
        if total <= 0 or abs(total - 1.0) <= 0.01:
            return self
        return PriorityWeights(
            reward=round(self.reward / total, 4),
            persona_fit=round(self.persona_fit / total, 4),
            success_rate=round(self.success_rate / total, 4),
            backlog_age=round(self.backlog_age / total, 4),
        )

    model_config = {"from_attributes": True, "extra": "ignore"}


class SchedulerConfig(BaseModel):
    """Scheduler settings for task retry and backoff."""

    max_task_retries: int = Field(default=3, ge=1, le=10)
    retry_backoff_minutes: list[int] = Field(
        default=[5, 30, 120],
        description="Escalating backoff in minutes, indexed by attempt_count",
    )
    priority_weights: PriorityWeights = Field(
        default_factory=PriorityWeights,
        description="Weights for composite priority scoring",
    )

    model_config = {"from_attributes": True, "extra": "ignore"}
