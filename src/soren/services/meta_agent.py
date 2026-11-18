"""SorenMetaAgent — facade and orchestrator for the full execution cycle.

``run_cycle`` runs: memory check → init → assess → discover (if empty) → select
→ execute → finalize → stuck handling, then always finalizes the report.
Post-run learning lives in ``cycle_learning.CycleLearning`` and stuck handling
in ``stuck_intervention.StuckIntervention``; both are built per use from the
agent's current collaborators.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from soren.adapters.base import get_adapter
from soren.models.session import CycleReport, DiscoverySummary, TaskResult
from soren.services.cycle_learning import CycleLearning
from soren.services.memory_guard import MemoryGuard
from soren.services.memory_service import MemoryService
from soren.services.report_writer import write_report
from soren.services.retry_policy import classify_failure
from soren.services.stuck_intervention import InterventionCallbacks, StuckIntervention

MEMORY_ABORT_THRESHOLD_PCT = 92.0
UNATTENDED_CONFIDENCE_FLOOR = 0.1

logger = logging.getLogger(__name__)

__all__ = ["InterventionCallbacks", "SorenMetaAgent"]

if TYPE_CHECKING:
    from soren.models.persona import Persona
    from soren.models.task import Task
    from soren.services.config_loader import SorenSettings
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.task_backlog import TaskBacklog
    from soren.services.task_discovery import TaskDiscovery
    from soren.services.task_executor import TaskExecutor
    from soren.services.training_session import TrainingCallbacks
    from soren.services.vm_manager import VMManager


class SorenMetaAgent:
    """Orchestrates the full Soren cycle: assess → discover (if empty) → select → execute → finalize."""

    def __init__(
        self,
        settings: SorenSettings,
        db: DatabaseManager,
        llm: LLMService,
        vm: VMManager,
        backlog: TaskBacklog,
        discovery: TaskDiscovery,
        executor: TaskExecutor,
        persona_loader: Any,
        *,
        identity_mgr: Any = None,
        report_dir: Path | None = None,
        playbook_service: PlaybookService | None = None,
        dry_run: bool = False,
        intervention_callbacks: InterventionCallbacks | None = None,
        training_callbacks: TrainingCallbacks | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._llm = llm
        self._vm = vm
        self._backlog = backlog
        self._discovery = discovery
        self._executor = executor
        self._playbook_service = playbook_service
        self._dry_run = dry_run
        self._persona_loader = persona_loader
        self._identity_mgr = identity_mgr
        self._report_dir = Path(report_dir or "data/reports")
        self._memory_guard = MemoryGuard(settings.llm)
        self._memory_service = MemoryService(db)
        self._intervention_callbacks = intervention_callbacks
        self._training_callbacks = training_callbacks
        from soren.services.priority_scorer import TaskPriorityScorer

        self._scorer = TaskPriorityScorer(settings)

    def _memory_over_threshold(self) -> bool:
        """Return True if memory usage exceeds threshold (92% for cloud models, was 85% for local)."""
        try:
            return psutil.virtual_memory().percent >= MEMORY_ABORT_THRESHOLD_PCT
        except Exception:
            return False

    def _load_last_report(self) -> CycleReport | None:
        """Load the most recent cycle report via the latest.json symlink."""
        latest = self._report_dir / "latest.json"
        if not latest.exists():
            return None
        try:
            return CycleReport.model_validate_json(latest.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _warn_previous_stuck_sources(self) -> None:
        """Warn about sources that were auto-skipped in the last unattended run."""
        last_report = self._load_last_report()
        if last_report is None:
            return
        auto_skipped = [s for s in last_report.stuck_sources if s.get("auto_skipped")]
        if not auto_skipped:
            return
        names = [s.get("source_name", "unknown") for s in auto_skipped]
        logger.warning(
            "The following sources were auto-skipped in the last unattended run: %s. "
            "Consider running `soren train <source>` for these.",
            ", ".join(names),
        )

    async def run_cycle(self) -> CycleReport:
        """Execute full lifecycle: init → assess → discover (if empty) → select → execute → finalize."""
        cycle_id = str(uuid.uuid4())[:8]
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        start_ts = time.perf_counter()

        report = CycleReport(
            cycle_id=cycle_id,
            cycle_type="execute",
            started_at=started_at,
            vm_name=self._vm.vm_name,
        )

        computer = None
        personas: list[Persona] = []
        task_executed: Task | None = None
        task_result: TaskResult | None = None
        discovery_summary: dict[str, Any] | None = None

        try:
            # 0. Pre-cycle memory check (32 GB safeguard)
            if self._memory_over_threshold():
                report.task_result = TaskResult(
                    success=False,
                    error=f"Memory over {MEMORY_ABORT_THRESHOLD_PCT:.0f}% before cycle; aborting to avoid OOM. Run `just check-resources`.",
                )
                report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                report.total_duration_seconds = time.perf_counter() - start_ts
                return report

            # 0b. Warn about previously stuck-skipped sources (attended mode only)
            if self._intervention_callbacks:
                self._warn_previous_stuck_sources()

            # 1. Init
            personas = self._persona_loader()
            if not personas:
                report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                report.total_duration_seconds = time.perf_counter() - start_ts
                return report

            self._memory_guard.reset_cycle()
            computer = await self._prepare_vm()

            # 2. Assess
            eligible = await self._backlog.get_eligible_tasks()

            # 3. Discover (if empty)
            if not eligible:
                summary = await self._discover_all(personas, computer)
                discovery_summary = summary.model_dump(
                    include={"total_inserted", "total_skipped", "personas_processed", "source_count"}
                )
                report.discovery_ran = True
                report.discovery_summary = discovery_summary
                eligible = await self._backlog.get_eligible_tasks()

            # 4. Select
            if not eligible:
                report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                report.total_duration_seconds = time.perf_counter() - start_ts
                report.backlog_snapshot = await self._backlog.get_summary()
                return report

            task, persona = await self._select_task(eligible, personas)

            # 5. Execute
            await self._backlog.mark_in_progress(task.id)
            task_executed = task  # from here on, an exception marks this task failed
            task_result, should_offer_training = await self._execute_task(task, persona, computer)

            # 6. Finalize
            await self._record_outcome(task, task_result, should_offer_training, report)
            # 7. Stuck intervention
            if should_offer_training and await self._stuck_intervention().handle(task, persona, task_result, report):
                return report  # operator aborted; `finally` still finalizes the report
            report.task_executed_id = task.id
            report.task_result = task_result
            report.total_agent_steps = (task_result.steps_taken or 0) if task_result else 0
            report.total_screenshots_captured = len(task_result.screenshot_paths) if task_result else 0
            report.screenshots = list(task_result.screenshot_paths) if task_result else []

        except Exception as e:
            report.task_result = TaskResult(success=False, error=str(e))
            if task_executed:
                reason = classify_failure(e)
                await self._backlog.mark_failed(
                    task_executed.id,
                    reason,
                    getattr(self._settings.scheduler, "retry_backoff_minutes", None),
                )

        finally:
            await self._finalize_report(report, personas, computer, start_ts)

        return report

    # --- run_cycle steps ---

    async def _prepare_vm(self) -> Any:
        """Make sure the VM is running, connect, and isolate the desktop. Returns the CUA computer."""
        await self._vm.ensure_ready()
        computer = await self._vm.connect_computer()
        prep = await self._vm.prepare_session_environment()
        if prep.warnings:
            logger.warning("Session prep warnings before run_cycle: %s", "; ".join(prep.warnings))
        return computer

    async def _select_task(self, eligible: list[Task], personas: list[Persona]) -> tuple[Task, Persona]:
        """Highest-scoring eligible task (ties: oldest first) and the persona that owns it."""
        history = await self._backlog.list_tasks(status="completed", limit=100)
        persona_by_id = {p.id: p for p in personas}
        default_persona = personas[0]
        scored = [
            (
                self._scorer.score(
                    t,
                    persona_by_id.get(t.persona_id or "", default_persona),
                    eligible,
                    history,
                ),
                t,
            )
            for t in eligible
        ]
        scored.sort(key=lambda x: (-x[0], x[1].created_at or ""))
        task = scored[0][1]
        persona = persona_by_id.get(task.persona_id or "", default_persona)
        return task, persona

    async def _execute_task(self, task: Task, persona: Persona, computer: Any) -> tuple[TaskResult, bool]:
        """Run the task with recalled memories. Returns (result, should_offer_training); never raises from execution."""
        memory_key = f"{task.source_name or 'default'}:{task.task_type or 'task'}:{task.persona_id or ''}"
        recalled = await self._memory_service.recall(key=memory_key, entry_type=None, limit=5, min_importance=0.0)
        memories = [e.to_prompt_line() for e in recalled]
        try:
            return await self._executor.execute_with_learning(task, persona, computer, memories=memories)
        except Exception as e:
            return TaskResult(success=False, error=str(e)), False

    async def _record_outcome(
        self, task: Task, task_result: TaskResult, should_offer_training: bool, report: CycleReport
    ) -> None:
        """Mark the task completed/failed in the backlog and learn from the outcome."""
        if task_result.success:
            await self._backlog.mark_completed(task.id, task_result.model_dump())
            await self._learning().after_success(task, task_result, report)
            return
        # Unattended stuck runs keep a confidence floor so one bad run can't zero a playbook.
        is_unattended_stuck = should_offer_training and self._intervention_callbacks is None
        confidence_floor = UNATTENDED_CONFIDENCE_FLOOR if is_unattended_stuck else 0.0
        reason = classify_failure(task_result.error or "unknown")
        await self._backlog.mark_failed(
            task.id,
            reason,
            getattr(self._settings.scheduler, "retry_backoff_minutes", None),
        )
        self._learning().after_failure(task, task_result, reason, confidence_floor)

    async def _finalize_report(
        self, report: CycleReport, personas: list[Persona], computer: Any, start_ts: float
    ) -> None:
        """Always-run bookkeeping: timings, backlog snapshot, telemetry, memory pruning, report, VM reset."""
        report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report.total_duration_seconds = time.perf_counter() - start_ts
        report.backlog_snapshot = await self._backlog.get_summary()
        report.personas_used = [p.id for p in personas]

        try:
            telemetry = self._llm.get_telemetry()
            model_used = telemetry.get("model_used")
            memory_metrics = telemetry.get("memory_metrics")
            if isinstance(model_used, str):
                report.model_used = model_used
            report.fallback_triggered = bool(telemetry.get("fallback_triggered"))
            if isinstance(memory_metrics, dict):
                report.memory_metrics = memory_metrics
        except Exception:
            pass

        try:
            pruned = await self._memory_service.prune(min_importance=0.3)
            report.memory_entries_pruned = pruned
        except Exception:
            pass

        self._memory_service.clear_session()

        try:
            write_report(report, self._report_dir)
        except Exception:
            pass

        try:
            if computer:
                await self._vm.reset_browser_state()
        except Exception:
            pass

        try:
            if self._vm.notify_cycle_complete():
                await self._vm.recreate_vm()
        except Exception:
            pass

    def _learning(self) -> CycleLearning:
        return CycleLearning(
            self._settings, self._llm, self._playbook_service, self._memory_service, dry_run=self._dry_run
        )

    def _stuck_intervention(self) -> StuckIntervention:
        return StuckIntervention(
            self._settings,
            self._vm,
            self._llm,
            self._executor,
            self._playbook_service,
            self._intervention_callbacks,
            self._training_callbacks,
        )

    async def refresh_tasks(self, persona_filter: str | None = None) -> DiscoverySummary:
        """Discovery-only mode. No execution."""
        personas = self._persona_loader()
        if persona_filter:
            personas = [p for p in personas if p.id == persona_filter]
        if not personas:
            return DiscoverySummary()

        await self._vm.ensure_ready()
        computer = await self._vm.connect_computer()
        prep = await self._vm.prepare_session_environment()
        if prep.warnings:
            logger.warning("Session prep warnings before refresh_tasks: %s", "; ".join(prep.warnings))

        summary = await self._discover_all(personas, computer)
        # Historical behavior: refresh_tasks reports every loaded persona
        # (run_cycle reports only enabled ones).
        return summary.model_copy(update={"personas_processed": len(personas)})

    async def _discover_all(self, personas: list[Persona], computer: Any) -> DiscoverySummary:
        """Run discovery for every enabled source of every enabled persona and insert results.

        Sources whose (persona-level) type has no adapter are skipped; a failing
        source never stops the others. ``personas_processed`` counts enabled personas.
        """
        total_inserted = total_skipped = source_count = 0
        enabled_personas = [p for p in personas if getattr(p, "enabled", True)]
        for p in enabled_personas:
            for source in getattr(p, "task_sources", []):
                if not getattr(source, "enabled", True):
                    continue
                if not get_adapter(getattr(source, "type", "")):
                    continue
                try:
                    tasks = await self._discovery.discover(p, source, computer)
                    if tasks:
                        result = await self._backlog.insert_discovered(tasks)
                        total_inserted += result.inserted
                        total_skipped += result.skipped
                        source_count += 1
                except Exception:
                    logger.exception("Discovery failed for source %r (persona %r)", source.name, p.id)
        return DiscoverySummary(
            total_inserted=total_inserted,
            total_skipped=total_skipped,
            personas_processed=len(enabled_personas),
            source_count=source_count,
        )
