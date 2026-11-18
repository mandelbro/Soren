"""CycleLearning — what a cycle learns from an executed task (playbooks, confidence, memory).

Used by ``SorenMetaAgent.run_cycle`` after execution. On success: LLM playbook
write-back, auto-generated site playbook (never overwriting a human-trained
flow), a host playbook for newly seen hosts, confidence updates, and a cycle
memory entry. On failure: confidence decrements (floored for unattended stuck
runs; skipped for config errors). Every step is best-effort: a learning
failure never fails the cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from soren.models.session import CycleReport, TaskResult
    from soren.models.task import Task
    from soren.services.config_loader import SorenSettings
    from soren.services.llm_service import LLMService
    from soren.services.memory_service import MemoryService
    from soren.services.playbook_service import PlaybookService

_TAB_STEP_TYPES = {"tab_switch", "new_tab", "open_tab"}


def extract_host_domain(step_log: list[dict[str, Any]]) -> str | None:
    """Extract host domain from tab-switch step log entries."""
    for entry in step_log:
        if entry.get("action_type") not in _TAB_STEP_TYPES:
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


class CycleLearning:
    """Post-execution learning for one cycle. Build it per use from the agent's current collaborators."""

    def __init__(
        self,
        settings: SorenSettings,
        llm: LLMService,
        playbook_service: PlaybookService | None,
        memory_service: MemoryService,
        *,
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._playbook_service = playbook_service
        self._memory_service = memory_service
        self._dry_run = dry_run

    async def after_success(self, task: Task, task_result: TaskResult, report: CycleReport) -> None:
        """Learn from a successful run (updates ``report`` with preview and memory counts)."""
        # Playbook write-back (LLM summarizes steps into reusable instructions)
        if self._playbook_service and task.source_name and getattr(self._settings.playbooks, "write_back", True):
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

        # Auto-generate host playbook for new domains
        if self._playbook_service and task_result.step_log:
            try:
                host_domain = extract_host_domain(task_result.step_log)
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

        # Update host confidence on success
        if self._playbook_service and task_result.step_log:
            host_domain = extract_host_domain(task_result.step_log)
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

    def after_failure(self, task: Task, task_result: TaskResult, reason: str, confidence_floor: float) -> None:
        """Lower playbook/host confidence after a failed run (not for config errors)."""
        # Update confidence on failure (with floor for unattended auto-skip).
        # Config errors are not the playbook's fault; leave its confidence alone.
        if self._playbook_service and task.source_name and reason != "config_error":
            try:
                self._playbook_service.update_confidence(
                    task.source_name,
                    "execution",
                    success=False,
                    task_type=task.task_type,
                    min_confidence=confidence_floor,
                )
            except Exception:
                pass

        # Update host confidence on failure
        if self._playbook_service and task_result and task_result.step_log:
            host_domain = extract_host_domain(task_result.step_log)
            if host_domain:
                try:
                    self._playbook_service.update_host_confidence(host_domain, success=False)
                except Exception:
                    pass
