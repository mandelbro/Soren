"""RetryPolicy — escalating backoff and dead-letter management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from soren.models.task import Task
    from soren.services.config_loader import SorenSettings

DEFAULT_BACKOFF = [5, 30, 120]
DEFAULT_MAX_RETRIES = 3

FAILURE_CATEGORIES = [
    "timeout",
    "vlm_error",
    "login_failure",
    "step_budget_exceeded",
    "verification_failed",
    "vm_error",
    "unknown",
]


class RetryAction(BaseModel):
    """Result of retry policy computation."""

    action: Literal["retry", "dead_letter"]
    next_eligible_at: datetime | None = None
    backoff_minutes: int | None = None

    model_config = {"from_attributes": True, "extra": "ignore"}


def classify_failure(error: Exception | str) -> str:
    """Categorize failure reason for pattern analysis."""
    msg = str(error).lower() if isinstance(error, Exception) else str(error).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "vlm" in msg or "vision" in msg or "model" in msg or "ollama" in msg or "mlx" in msg:
        return "vlm_error"
    if "login" in msg or "auth" in msg or "credential" in msg or "2fa" in msg or "captcha" in msg:
        return "login_failure"
    if "step budget" in msg or "step_budget" in msg or "exceeded" in msg:
        return "step_budget_exceeded"
    if "verification" in msg or "verify" in msg or "verification_failed" in msg:
        return "verification_failed"
    if "vm" in msg or "lume" in msg or "connection" in msg or "vnc" in msg:
        return "vm_error"
    return "unknown"


class RetryPolicy:
    """Computes retry vs dead-letter and backoff schedule."""

    def __init__(self, settings: SorenSettings) -> None:
        self._settings = settings
        sched = getattr(settings, "scheduler", None)
        self._backoff = (
            getattr(sched, "retry_backoff_minutes", None) or DEFAULT_BACKOFF
        )
        self._max_retries = (
            getattr(sched, "max_task_retries", None) or DEFAULT_MAX_RETRIES
        )

    def compute_next_action(self, task: Task, failure_reason: str) -> RetryAction:
        """Determine retry or dead-letter based on attempt_count and max_retries."""
        attempt = (task.attempt_count or 0) + 1
        max_retries = task.max_retries or self._max_retries

        if attempt >= max_retries:
            return RetryAction(action="dead_letter", next_eligible_at=None, backoff_minutes=None)

        idx = min(attempt - 1, len(self._backoff) - 1)
        mins = self._backoff[idx]
        next_at = datetime.now(timezone.utc) + timedelta(minutes=mins)
        return RetryAction(action="retry", next_eligible_at=next_at, backoff_minutes=mins)
