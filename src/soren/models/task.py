"""Task and execution domain models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """Task type classification."""

    survey = "survey"
    email_reply = "email_reply"
    form_submission = "form_submission"
    proposal = "proposal"


class TaskStatus(str, Enum):
    """Task lifecycle status."""

    queued = "queued"
    in_progress = "in_progress"
    completed = "completed"
    dead_letter = "dead_letter"


class StepStatus(str, Enum):
    """Task step status."""

    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class ActionType(str, Enum):
    """GUI action types for task steps."""

    click = "click"
    type = "type"
    scroll = "scroll"
    key_press = "key_press"
    screenshot = "screenshot"
    navigate = "navigate"
    verify = "verify"


class TaskStep(BaseModel):
    """Single step in a task execution."""

    id: str
    task_id: str
    description: str = ""
    action_type: str = "click"
    action_data: dict[str, Any] = Field(default_factory=dict)
    vlm_reasoning: str | None = None
    status: str = "pending"
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None

    model_config = {"from_attributes": True, "extra": "ignore"}


class Task(BaseModel):
    """Task from the backlog, ready for execution."""

    id: str
    dedup_key: str = Field(..., min_length=1)
    title: str = ""
    description: str = ""
    task_type: str = "survey"
    source_name: str = ""
    source_type: str = ""
    source_url: str = ""
    provider_task_id: str | None = None
    persona_id: str = ""
    status: str = "queued"
    priority: int = Field(default=5, ge=0, le=10)
    estimated_duration_minutes: int | None = None
    estimated_reward: float | None = None
    attempt_count: int = 0
    max_retries: int = 3
    next_eligible_at: str | None = None
    last_failure_reason: str | None = None
    result_data: dict[str, Any] | None = None
    discovered_at: str = ""
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    steps: list[TaskStep] = Field(default_factory=list)

    model_config = {"from_attributes": True, "extra": "ignore"}
