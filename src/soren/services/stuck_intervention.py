"""StuckIntervention — what happens when a task gets stuck during a cycle.

Attended mode asks the operator (train / skip / abort) via ``InterventionCallbacks``;
"train" launches a TrainingSession for the task's source with its resolved
service config. Unattended mode waits ``scheduler.stuck_auto_skip_timeout``
(so external monitoring can react) and auto-skips. Every decision is recorded
in ``CycleReport.stuck_sources``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Literal, Protocol

from soren.services.service_resolution import ResolvedService

if TYPE_CHECKING:
    from soren.models.persona import Persona
    from soren.models.session import CycleReport, TaskResult
    from soren.models.task import Task
    from soren.services.config_loader import SorenSettings
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.task_executor import TaskExecutor
    from soren.services.training_session import TrainingCallbacks
    from soren.services.vm_manager import VMManager

logger = logging.getLogger(__name__)


class InterventionCallbacks(Protocol):
    """Callback protocol for stuck intervention during cycle run.

    Decouples terminal I/O from orchestration logic for testability.
    """

    async def on_stuck(
        self,
        source_name: str,
        stuck_reason: str,
        cycle_context: dict[str, Any],
    ) -> Literal["train", "skip", "abort"]: ...


class StuckIntervention:
    """Stuck handling for one cycle. Build it per use from the agent's current collaborators."""

    def __init__(
        self,
        settings: SorenSettings,
        vm: VMManager,
        llm: LLMService,
        executor: TaskExecutor,
        playbook_service: PlaybookService | None,
        intervention_callbacks: InterventionCallbacks | None,
        training_callbacks: TrainingCallbacks | None,
    ) -> None:
        self._settings = settings
        self._vm = vm
        self._llm = llm
        self._executor = executor
        self._playbook_service = playbook_service
        self._intervention_callbacks = intervention_callbacks
        self._training_callbacks = training_callbacks

    async def handle(self, task: Task, persona: Persona, task_result: TaskResult | None, report: CycleReport) -> bool:
        """Record the stuck source and apply the operator's (or unattended) decision. True means abort the cycle."""
        report.stuck_detected = True
        stuck_reason = (task_result.error if task_result else None) or "Unknown stuck condition"

        if self._intervention_callbacks:
            # Attended mode: prompt user
            cycle_context = {
                "task_id": task.id,
                "source_name": task.source_name,
                "source_url": task.source_url,
                "task_type": task.task_type,
                "steps_taken": task_result.steps_taken if task_result else 0,
                "error": task_result.error if task_result else None,
            }
            try:
                choice = await self._intervention_callbacks.on_stuck(
                    source_name=task.source_name or "unknown",
                    stuck_reason=stuck_reason,
                    cycle_context=cycle_context,
                )
            except Exception:
                logger.exception("Intervention callback failed; treating as skip")
                choice = "skip"

            stuck_entry = {
                "source_name": task.source_name or "unknown",
                "stuck_reason": stuck_reason,
                "skipped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "auto_skipped": False,
            }
            report.stuck_sources.append(stuck_entry)

            if choice == "train":
                await self._handle_train_intervention(task, persona)
            elif choice == "abort":
                report.task_executed_id = task.id
                report.task_result = task_result
                report.total_agent_steps = (task_result.steps_taken or 0) if task_result else 0
                return True
            # "skip" falls through to normal finalization
        else:
            # Unattended mode: auto-skip after timeout.
            # The sleep window allows external monitoring/alerting to react before
            # the source is skipped and the cycle moves on.
            timeout = getattr(self._settings.scheduler, "stuck_auto_skip_timeout", 30.0)
            logger.info(
                "Source '%s' is stuck (%s). Auto-skipping after %.1fs timeout (unattended mode).",
                task.source_name or "unknown",
                stuck_reason,
                timeout,
            )
            await asyncio.sleep(timeout)
            report.stuck_sources.append(
                {
                    "source_name": task.source_name or "unknown",
                    "stuck_reason": stuck_reason,
                    "skipped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "auto_skipped": True,
                }
            )
        return False

    def _service_for_training(self, task: Task, persona: Persona) -> ResolvedService | None:
        """The task's resolved service for the training session, or None (legacy env-only crop)."""
        try:
            service = self._executor.resolve_service_for(task, persona)
        except Exception as exc:
            logger.warning("Training without service config for %r: %s", task.source_name, exc)
            return None
        return service if isinstance(service, ResolvedService) else None

    async def _handle_train_intervention(self, task: Task, persona: Persona) -> None:
        """Invoke a training session for the stuck source and flow."""
        if not self._training_callbacks or not self._playbook_service:
            logger.warning("Training requested but no training_callbacks or playbook_service available; skipping.")
            return
        service = self._service_for_training(task, persona)
        try:
            from soren.services.training_session import TrainingSession

            session = TrainingSession(
                vm_manager=self._vm,
                playbook_service=self._playbook_service,
                llm=self._llm,
                callbacks=self._training_callbacks,
                source_name=task.source_name or "unknown",
                flow_type="execution",
                source_url=task.source_url or "",
                task_type=task.task_type,
                service=service,
            )
            await session.run()
        except Exception:
            logger.exception("Training session failed for source '%s'", task.source_name)
