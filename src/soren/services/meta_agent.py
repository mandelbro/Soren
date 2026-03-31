"""SorenMetaAgent — central orchestration engine for the full execution cycle."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

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
    from soren.services.playbook_service import PlaybookService
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
        playbook_service: PlaybookService | None = None,
        dry_run: bool = False,
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
            recalled = await self._memory_service.recall(key=memory_key, entry_type=None, limit=5, min_importance=0.0)
            memories = [e.to_prompt_line() for e in recalled]
            should_offer_training = False
            try:
                task_result, should_offer_training = await self._executor.execute_with_learning(
                    task, persona, computer, memories=memories
                )
            except Exception as e:
                task_result = TaskResult(success=False, error=str(e))
                should_offer_training = False

            # 6. Finalize
            if task_result and task_result.success:
                await self._backlog.mark_completed(task.id, task_result.model_dump())

                # Playbook write-back (LLM summarizes steps into reusable instructions)
                if (
                    self._playbook_service
                    and task.source_name
                    and getattr(self._settings.playbooks, "write_back", True)
                ):
                    try:
                        update = await self._playbook_service.write_back(
                            task.source_name,
                            task,
                            task_result.step_log or [],
                            dry_run=self._dry_run,
                        )
                        if update.dry_run and update.preview:
                            report.playbook_preview = update.preview
                    except Exception:
                        pass

                # Auto-generate site playbook from step log
                if self._playbook_service and task.source_name:
                    try:
                        from soren.services.auto_playbook import auto_generate_site_playbook

                        flow_type = "execution"
                        auto_flow = await auto_generate_site_playbook(
                            llm=self._llm,
                            step_log=task_result.step_log or [],
                            source_name=task.source_name,
                            source_url=task.source_url or "",
                            flow_type=flow_type,
                            task_type=task.task_type,
                        )
                        # Only save if no human-trained playbook exists
                        existing_site = self._playbook_service.load_site(task.source_name)
                        existing_flow = None
                        if existing_site and existing_site.flows.execution:
                            existing_flow = existing_site.flows.execution.get(task.task_type or "default")
                        if not existing_flow or existing_flow.verified_by != "human":
                            self._playbook_service.save_flow(
                                task.source_name,
                                flow_type,
                                auto_flow,
                                task_type=task.task_type,
                            )
                    except Exception:
                        pass

                # Auto-generate host playbook for new domains (Task 020)
                if self._playbook_service and task_result.step_log:
                    try:
                        host_domain = self._extract_host_domain(task_result.step_log)
                        auto_learn = getattr(self._settings.playbooks, "auto_learn_host", True)
                        if host_domain and auto_learn:
                            existing_host = self._playbook_service.load_host(host_domain)
                            if existing_host is None:
                                from soren.services.auto_playbook import auto_generate_host_playbook

                                host_pb = await auto_generate_host_playbook(
                                    llm=self._llm,
                                    step_log=task_result.step_log,
                                    host_domain=host_domain,
                                )
                                self._playbook_service.save_host(host_pb)
                    except Exception:
                        pass

                # Update confidence on success
                if self._playbook_service and task.source_name:
                    try:
                        self._playbook_service.update_confidence(
                            task.source_name, "execution", success=True, task_type=task.task_type
                        )
                    except Exception:
                        pass

                # Update host confidence on success (Task 020)
                if self._playbook_service and task_result.step_log:
                    host_domain = self._extract_host_domain(task_result.step_log)
                    if host_domain:
                        try:
                            self._playbook_service.update_host_confidence(host_domain, success=True)
                        except Exception:
                            pass

                # Store memory after successful execution (cycle-tier for reuse)
                try:
                    memory_key = f"{task.source_name or 'default'}:{task.task_type or 'task'}:{task.persona_id or ''}"
                    summary = (
                        f"Completed {task.task_type or 'task'} on {task.source_name or 'unknown'}; "
                        f"{task_result.steps_taken or 0} steps."
                    )
                    await self._memory_service.store(
                        key=memory_key,
                        value={"summary": summary, "task_id": task.id, "steps": task_result.steps_taken},
                        entry_type="cycle",
                        importance=0.6,
                    )
                    report.memory_entries_created = 1
                except Exception:
                    pass
            elif task_result and task_executed:
                reason = classify_failure(task_result.error or "unknown")
                await self._backlog.mark_failed(
                    task.id,
                    reason,
                    getattr(self._settings.scheduler, "retry_backoff_minutes", None),
                )
                # Update confidence on failure
                if self._playbook_service and task.source_name:
                    try:
                        self._playbook_service.update_confidence(
                            task.source_name, "execution", success=False, task_type=task.task_type
                        )
                    except Exception:
                        pass

                # Update host confidence on failure (Task 020)
                if self._playbook_service and task_result and task_result.step_log:
                    host_domain = self._extract_host_domain(task_result.step_log)
                    if host_domain:
                        try:
                            self._playbook_service.update_host_confidence(host_domain, success=False)
                        except Exception:
                            pass

            # Log stuck condition and mark report
            if should_offer_training:
                report.stuck_detected = True

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

        return report

    _TAB_STEP_TYPES = {"tab_switch", "new_tab", "open_tab"}

    @staticmethod
    def _extract_host_domain(step_log: list[dict[str, Any]]) -> str | None:
        """Extract host domain from tab-switch step log entries."""
        for entry in step_log:
            if entry.get("action_type") not in SorenMetaAgent._TAB_STEP_TYPES:
                continue
            data = entry.get("action_data", {})
            if not isinstance(data, dict):
                continue
            url = data.get("url") or data.get("tab_url") or ""
            if url and "://" in url:
                try:
                    hostname = urlparse(url).hostname
                    if hostname:
                        return hostname
                except Exception:
                    continue
        return None

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
