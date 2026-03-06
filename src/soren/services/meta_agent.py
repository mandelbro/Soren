"""SorenMetaAgent — central orchestration engine for the full execution cycle."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from soren.adapters.base import get_adapter
from soren.models.session import CycleReport, DiscoverySummary, TaskResult
from soren.services.memory_guard import MemoryGuard
from soren.services.memory_service import MemoryService
from soren.services.report_writer import write_report
from soren.services.retry_policy import classify_failure

MEMORY_ABORT_THRESHOLD_PCT = 85.0

if TYPE_CHECKING:
    from soren.models.persona import Persona
    from soren.models.task import Task
    from soren.services.config_loader import SorenSettings
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.task_backlog import TaskBacklog
    from soren.services.task_discovery import TaskDiscovery
    from soren.services.task_executor import TaskExecutor
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
    ) -> None:
        self._settings = settings
        self._db = db
        self._llm = llm
        self._vm = vm
        self._backlog = backlog
        self._discovery = discovery
        self._executor = executor
        self._persona_loader = persona_loader
        self._identity_mgr = identity_mgr
        self._report_dir = Path(report_dir or "data/reports")
        self._memory_guard = MemoryGuard(settings.llm)
        self._memory_service = MemoryService(db)
        from soren.services.priority_scorer import TaskPriorityScorer

        self._scorer = TaskPriorityScorer(settings)

    def _memory_over_threshold(self) -> bool:
        """Return True if memory usage exceeds 85% (32 GB safeguard)."""
        try:
            return psutil.virtual_memory().percent >= MEMORY_ABORT_THRESHOLD_PCT
        except Exception:
            return False

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
                    error="Memory over 85% before cycle; aborting to avoid OOM. Run `just check-resources`.",
                )
                report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                report.total_duration_seconds = time.perf_counter() - start_ts
                return report

            # 1. Init
            personas = self._persona_loader()
            if not personas:
                report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                report.total_duration_seconds = time.perf_counter() - start_ts
                return report

            self._memory_guard.reset_cycle()
            await self._vm.ensure_ready()
            computer = await self._vm.connect_computer()

            # 2. Assess
            eligible = await self._backlog.get_eligible_tasks()

            # 3. Discover (if empty)
            if not eligible:
                total_inserted = 0
                total_skipped = 0
                source_count = 0
                for p in personas:
                    if not getattr(p, "enabled", True):
                        continue
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
                            pass
                discovery_summary = {
                    "total_inserted": total_inserted,
                    "total_skipped": total_skipped,
                    "personas_processed": len([p for p in personas if getattr(p, "enabled", True)]),
                    "source_count": source_count,
                }
                report.discovery_ran = True
                report.discovery_summary = discovery_summary
                eligible = await self._backlog.get_eligible_tasks()

            # 4. Select
            if not eligible:
                report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                report.total_duration_seconds = time.perf_counter() - start_ts
                report.backlog_snapshot = await self._backlog.get_summary()
                return report

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

            # 5. Execute
            await self._backlog.mark_in_progress(task.id)
            task_executed = task
            memory_key = f"{task.source_name or 'default'}:{task.task_type or 'task'}:{task.persona_id or ''}"
            recalled = await self._memory_service.recall(
                key=memory_key, entry_type=None, limit=5, min_importance=0.0
            )
            memories = [e.to_prompt_line() for e in recalled]
            try:
                task_result = await self._executor.execute(
                    task, persona, computer, memories=memories
                )
            except Exception as e:
                task_result = TaskResult(success=False, error=str(e))

            # 6. Finalize
            if task_result and task_result.success:
                await self._backlog.mark_completed(task.id, task_result.model_dump())
            elif task_result and task_executed:
                reason = classify_failure(task_result.error or "unknown")
                await self._backlog.mark_failed(
                    task.id,
                    reason,
                    getattr(self._settings.scheduler, "retry_backoff_minutes", None),
                )

            report.task_executed_id = task.id
            report.task_result = task_result
            report.total_agent_steps = (task_result.steps_taken or 0) if task_result else 0

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
            report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            report.total_duration_seconds = time.perf_counter() - start_ts
            report.backlog_snapshot = await self._backlog.get_summary()
            report.personas_used = [p.id for p in personas]

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

        return report

    async def refresh_tasks(self, persona_filter: str | None = None) -> DiscoverySummary:
        """Discovery-only mode. No execution."""
        personas = self._persona_loader()
        if persona_filter:
            personas = [p for p in personas if p.id == persona_filter]
        if not personas:
            return DiscoverySummary()

        await self._vm.ensure_ready()
        computer = await self._vm.connect_computer()

        total_inserted = 0
        total_skipped = 0
        source_count = 0

        for p in personas:
            if not getattr(p, "enabled", True):
                continue
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
                    pass

        return DiscoverySummary(
            total_inserted=total_inserted,
            total_skipped=total_skipped,
            personas_processed=len(personas),
            source_count=source_count,
        )
