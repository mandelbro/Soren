"""TaskExecutor — facade that drives the CUA ComputerAgent through one task.

Public API: ``execute`` (plain run) and ``execute_with_learning`` (adds tab
transitions and stuck detection), plus ``resolve_service_for``. Both share one
agent loop (``_run``); the collaborators live in their own modules:

- ``agent_output``: parsing the agent's output items and screenshots
- ``credential_handoff.CredentialHandoff``: persona credentials for the prompt
- ``learning_monitor.LearningMonitor`` + ``task_tabs.TabTransitionHandler``: learning extras
- ``execution_verification.PostExecutionVerifier``: verification and corrections
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from soren.models.persona import Persona
from soren.models.service import ServiceConfigError
from soren.models.session import TaskResult
from soren.models.task import Task
from soren.services.agent_output import (
    has_assistant_final,
    is_completion_message,
    parse_output_item,
    screenshot_from_item,
)
from soren.services.credential_handoff import CredentialHandoff
from soren.services.credential_redaction import CredentialRedactor, register_secrets
from soren.services.credentials_service import CredentialStore
from soren.services.execution_verification import PostExecutionVerifier
from soren.services.learning_monitor import LearningMonitor
from soren.services.persona_context import DEFAULT_STEP_BUDGET, PersonaContext
from soren.services.retry_policy import CONFIG_ERROR_PREFIX
from soren.services.service_context import use_service
from soren.services.service_prompt import render_service_guidance
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import ResolvedService, resolve_for_task
from soren.services.stuck_detector import StuckDetector
from soren.services.task_tabs import TabTransitionHandler, host_domain_of

if TYPE_CHECKING:
    from soren.services.config_loader import SorenSettings
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.vm_manager import VMManager

logger = logging.getLogger(__name__)

DEFAULT_VLM_MODEL = "mlx/mlx-community/UI-TARS-1.5-7B-6bit"
DEFAULT_SCREENSHOT_DELAY_S = 0.5

__all__ = ["CONFIG_ERROR_PREFIX", "DEFAULT_STEP_BUDGET", "DEFAULT_VLM_MODEL", "TaskExecutor"]


class TaskExecutor:
    """Facade: runs a task via the CUA ComputerAgent with step budget, logging, and a TaskResult."""

    def __init__(
        self,
        settings: SorenSettings,
        llm: LLMService,
        vm: VMManager,
        *,
        step_budget: int | None = None,
        vlm_model: str | None = None,
        screenshot_dir: Path | None = None,
        playbook_service: PlaybookService | None = None,
        credential_store: CredentialStore | None = None,
        service_registry: ServiceRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._vm = vm
        # An explicit budget wins over the service's step_budget (ADR-0002).
        self._explicit_step_budget = step_budget
        self._service_registry = service_registry or ServiceRegistry()
        self._playbook_service = playbook_service
        vlm_config = getattr(settings, "vlm", None)
        self._vlm_model = vlm_model or (vlm_config.full_model_str() if vlm_config else DEFAULT_VLM_MODEL)
        self._screenshot_dir = screenshot_dir or Path("data/screenshots")
        self._credentials = CredentialHandoff(credential_store)
        self._post_verifier = PostExecutionVerifier(settings, llm)

    # --- Service configuration (ADR-0002) ---

    def resolve_service_for(self, task: Task, persona: Persona) -> ResolvedService | None:
        """Resolve the service config for ``task``. Raises ServiceConfigError on invalid config."""
        return resolve_for_task(task, persona, registry=self._service_registry, settings=self._settings)

    def _budget_for(self, service: ResolvedService | None) -> int:
        if self._explicit_step_budget:
            return self._explicit_step_budget
        return service.config.step_budget if service else DEFAULT_STEP_BUDGET

    @staticmethod
    def _screenshot_delay_for(service: ResolvedService | None) -> float:
        return service.config.action_delay_ms / 1000 if service else DEFAULT_SCREENSHOT_DELAY_S

    @staticmethod
    def _config_error_result(exc: ServiceConfigError) -> TaskResult:
        logger.error("Service configuration error; task not started: %s", exc)
        return TaskResult(success=False, steps_taken=0, error=f"{CONFIG_ERROR_PREFIX} {exc}")

    async def _open_required_apps(self, service: ResolvedService | None) -> None:
        if not service or not service.config.required_apps:
            return
        try:
            await self._vm.open_required_apps(list(service.config.required_apps))
        except Exception as exc:
            logger.warning(
                "Could not open required apps %s for %s: %s", service.config.required_apps, service.name, exc
            )

    # --- Execution ---

    async def execute(
        self,
        task: Task,
        persona: Persona,
        computer: Any,
        memories: list[str] | None = None,
    ) -> TaskResult:
        """Execute task via ComputerAgent. Returns TaskResult with success, steps, duration.

        The task's service config is resolved first; an invalid config fails the
        task (``config_error``) without starting the agent.
        """
        try:
            service = self.resolve_service_for(task, persona)
        except ServiceConfigError as exc:
            return self._config_error_result(exc)
        with use_service(service):
            await self._open_required_apps(service)
            result, _ = await self._run(task, persona, computer, memories, service, learning=False)
            return result

    async def execute_with_learning(
        self,
        task: Task,
        persona: Persona,
        computer: Any,
        memories: list[str] | None = None,
    ) -> tuple[TaskResult, bool]:
        """Execute with stuck detection and tab-aware flow. Returns (result, should_offer_training).

        An invalid service config returns a failed ``config_error`` result and
        never offers training (a human can't train around a broken config file).
        """
        try:
            service = self.resolve_service_for(task, persona)
        except ServiceConfigError as exc:
            return self._config_error_result(exc), False
        with use_service(service):
            await self._open_required_apps(service)
            return await self._run(task, persona, computer, memories, service, learning=True)

    async def _run(
        self,
        task: Task,
        persona: Persona,
        computer: Any,
        memories: list[str] | None,
        service: ResolvedService | None,
        *,
        learning: bool,
    ) -> tuple[TaskResult, bool]:
        """The agent loop shared by ``execute`` and ``execute_with_learning``. Returns (result, stuck).

        With ``learning``, the budget is capped by the playbook flow's ``max_steps``
        and a LearningMonitor handles tab transitions and stuck detection.
        """
        from soren.patches import apply_all

        apply_all()
        from cua_agent import ComputerAgent

        budget = self._budget_for(service)
        monitor: LearningMonitor | None = None
        if learning:
            flow = self._get_flow_playbook(task)
            if flow:
                budget = min(flow.max_steps, budget)
            monitor = LearningMonitor(
                self._vm,
                self._tab_handler(task),
                StuckDetector(step_budget=budget),
                platform_domain=host_domain_of(task.source_url or ""),
            )

        playbook_content = self._load_playbook_content(task)
        max_tokens_raw = getattr(self._settings.playbooks, "max_instruction_tokens", None)
        max_tokens = max_tokens_raw if isinstance(max_tokens_raw, int) else None
        persona_context = PersonaContext.build(
            persona,
            task,
            playbook_content=playbook_content,
            memories=self._credentials.compose_memories(task, persona, memories),
            step_budget=budget,
            max_instruction_tokens=max_tokens,
            service_guidance=render_service_guidance(service),
        )
        # The local agent gets the credentials above; everything recorded from its run is redacted.
        redactor = CredentialRedactor(self._credentials.secrets(task, persona))
        register_secrets(redactor.values)
        messages = [{"role": "user", "content": task.description or f"Complete: {task.title}"}]
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        step_log: list[dict[str, Any]] = []
        screenshots_captured: list[str] = []
        last_screenshot_bytes: bytes | None = None
        steps_taken, error_msg, success, stuck = 0, None, False, False
        verification_passed: bool | None = None
        correction_applied: str | None = None

        try:
            if monitor:
                await monitor.start()
            agent = ComputerAgent(
                model=self._vlm_model,
                tools=[computer],
                instructions=persona_context,
                screenshot_delay=self._screenshot_delay_for(service),
                only_n_most_recent_images=5,
            )

            last_output: list = []
            async for result in agent.run(messages):
                steps_taken += 1
                output = result.get("output", [])
                last_output = output

                step_screenshot, step_action = None, "unknown"
                for item in output:
                    entry = redactor.redact(parse_output_item(item, steps_taken))
                    if entry:
                        step_log.append(entry)
                        step_action = entry.get("action_type", "unknown")
                    raw = screenshot_from_item(item)
                    if raw:
                        screenshots_captured.append(self._save_screenshot(raw, task.id, steps_taken))
                        last_screenshot_bytes = step_screenshot = raw

                if monitor:
                    await monitor.after_step(messages, step_log, steps_taken, step_screenshot, step_action)
                if steps_taken >= budget:
                    error_msg = f"Step budget ({budget}) exceeded"
                    break
                if is_completion_message(output):
                    if monitor:
                        monitor.record_completion()
                    success = True
                    break
                if monitor:
                    is_stuck, stuck_reason = monitor.is_stuck()
                    if is_stuck:
                        stuck = True
                        error_msg = f"Stuck detected: {stuck_reason}"
                        break

            if not success and not error_msg and steps_taken > 0:
                success = has_assistant_final(last_output)

            success, error_msg, verification_passed, correction_applied = await self._post_verifier.run(
                task,
                computer,
                success,
                error_msg,
                last_screenshot_bytes,
                steps_taken,
                step_log,
            )

        except Exception as e:
            error_msg = str(e)
            success = False

        result = TaskResult(
            success=success,
            steps_taken=steps_taken,
            step_log=step_log,
            screenshot_paths=screenshots_captured,
            duration_seconds=time.perf_counter() - started,
            error=error_msg,
            verification_passed=verification_passed,
            correction_applied=correction_applied,
        )
        return result, stuck

    def _tab_handler(self, task: Task) -> TabTransitionHandler:
        """Tab transition handler whose host content comes from this task's playbook cascade."""
        return TabTransitionHandler(self._vm, lambda domain: self._load_playbook_content(task, host_domain=domain))

    def _save_screenshot(self, raw: bytes, task_id: str, step: int) -> str:
        """Write a step screenshot under the screenshot dir and return its path."""
        path = self._screenshot_dir / f"{task_id}_step{step}_{uuid.uuid4().hex[:8]}.png"
        path.write_bytes(raw)
        return str(path)

    def _get_flow_playbook(self, task: Task) -> Any:
        """Load the FlowPlaybook for a task's source, or None if unavailable."""
        if not self._playbook_service or not task.source_name:
            return None
        site = self._playbook_service.load_site(task.source_name)
        if site is None or not site.flows.execution:
            return None
        return site.flows.execution.get(task.task_type or "default") or site.flows.execution.get("default")

    def _load_playbook_content(self, task: Task, host_domain: str | None = None) -> str | None:
        """Load cascading playbook content via resolve()."""
        if not self._playbook_service or not task.source_name:
            return None
        content = self._playbook_service.resolve(
            source_name=task.source_name,
            host_domain=host_domain,
            task_type=task.task_type or "survey",
            flow_type="execution",
        )
        return content if content else None
