"""TaskPriorityScorer — composite priority scoring for task selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from soren.models.persona import Persona
from soren.models.task import Task

if TYPE_CHECKING:
    from soren.services.config_loader import SorenSettings

DEFAULT_WEIGHTS = {"reward": 0.3, "persona_fit": 0.2, "success_rate": 0.2, "backlog_age": 0.3}
MAX_AGE_HOURS = 168  # 7 days
DEFAULT_SUCCESS_RATE = 0.5


class TaskPriorityScorer:
    """Computes composite priority score for eligible tasks."""

    def __init__(self, settings: SorenSettings) -> None:
        self._settings = settings
        w = getattr(settings.scheduler, "priority_weights", None)
        if w:
            self._w_reward = getattr(w, "reward", DEFAULT_WEIGHTS["reward"])
            self._w_fit = getattr(w, "persona_fit", DEFAULT_WEIGHTS["persona_fit"])
            self._w_success = getattr(w, "success_rate", DEFAULT_WEIGHTS["success_rate"])
            self._w_age = getattr(w, "backlog_age", DEFAULT_WEIGHTS["backlog_age"])
        else:
            self._w_reward = DEFAULT_WEIGHTS["reward"]
            self._w_fit = DEFAULT_WEIGHTS["persona_fit"]
            self._w_success = DEFAULT_WEIGHTS["success_rate"]
            self._w_age = DEFAULT_WEIGHTS["backlog_age"]

    def score(
        self,
        task: Task,
        persona: Persona,
        tasks: list[Task] | None = None,
        history: list[Task] | None = None,
    ) -> float:
        """Compute composite score (0.0–1.0). Uses tasks for reward normalization if provided."""
        reward_norm = self._reward_factor(task, tasks or [])
        fit = self._persona_fit(task, persona)
        success = self._success_rate(task, history or [])
        age = self._backlog_age(task)
        return (
            self._w_reward * reward_norm
            + self._w_fit * fit
            + self._w_success * success
            + self._w_age * age
        )

    def _reward_factor(self, task: Task, tasks: list[Task]) -> float:
        """Normalize estimated_reward across backlog. 0 if no reward or max is 0."""
        reward = task.estimated_reward or 0.0
        if reward <= 0:
            return 0.0
        if not tasks:
            return min(1.0, reward / 10.0)  # Arbitrary cap when no context
        max_reward = max((t.estimated_reward or 0.0) for t in tasks) or 1.0
        return min(1.0, reward / max_reward)

    def _persona_fit(self, task: Task, persona: Persona) -> float:
        """Task type alignment with persona traits. Survey/form benefit from conscientiousness."""
        task_type = (task.task_type or "").lower()
        if task_type in ("survey", "form", "form_submission", "generic_form"):
            return persona.get_trait("conscientiousness")
        if task_type in ("email", "email_reply"):
            return (persona.get_trait("agreeableness") + persona.get_trait("openness")) / 2
        return 0.5

    def _success_rate(self, task: Task, history: list[Task]) -> float:
        """Historical success rate for (task_type, source_name). Default 0.5 when no history."""
        key_type = task.task_type or "unknown"
        key_source = task.source_name or ""
        relevant = [
            t for t in history
            if (t.task_type or "") == key_type and (t.source_name or "") == key_source
        ]
        if not relevant:
            return DEFAULT_SUCCESS_RATE
        completed = sum(1 for t in relevant if (t.status or "") == "completed")
        return completed / len(relevant)

    def _backlog_age(self, task: Task) -> float:
        """Hours in backlog / max_age_hours, capped at 1.0."""
        created = task.created_at
        if not created:
            return 0.0
        try:
            if "T" in created:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 0.0
        now = datetime.now(timezone.utc)
        delta = now - dt
        hours = delta.total_seconds() / 3600
        return min(1.0, hours / MAX_AGE_HOURS)

    def rank(
        self,
        tasks: list[Task],
        persona: Persona,
        history: list[Task] | None = None,
    ) -> list[Task]:
        """Sort tasks by score descending. Stable sort for ties."""
        scored = [(self.score(t, persona, tasks, history), t) for t in tasks]
        scored.sort(key=lambda x: (-x[0], x[1].created_at or ""))
        return [t for _, t in scored]
