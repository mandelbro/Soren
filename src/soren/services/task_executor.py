"""TaskExecutor — drives CUA ComputerAgent through task execution with step budget and reporting."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from soren.models.persona import Persona
from soren.models.session import TaskResult
from soren.models.task import Task
from soren.services.credentials_service import CredentialStore
from soren.services.persona_context import PersonaContext
from soren.services.stuck_detector import StuckDetector
from soren.verification.baseline import BaselineManager
from soren.verification.correction import CorrectionStrategy, apply_correction
from soren.verification.verifier import ActionVerifier

if TYPE_CHECKING:
    from soren.services.config_loader import SorenSettings
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.vm_manager import VMManager

logger = logging.getLogger(__name__)

DEFAULT_STEP_BUDGET = 50
DEFAULT_VLM_MODEL = "mlx/mlx-community/UI-TARS-1.5-7B-6bit"


class TaskExecutor:
    """Wraps CUA ComputerAgent with step budget, logging, and TaskResult."""

    def __init__(
        self,
        settings: SorenSettings,
        llm: LLMService,
        vm: VMManager,
        *,
        step_budget: int = DEFAULT_STEP_BUDGET,
        vlm_model: str | None = None,
        screenshot_dir: Path | None = None,
        playbook_service: PlaybookService | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._vm = vm
        self._step_budget = step_budget
        self._playbook_service = playbook_service
        vlm_config = getattr(settings, "vlm", None)
        self._vlm_model = vlm_model or (vlm_config.full_model_str() if vlm_config else DEFAULT_VLM_MODEL)
        self._screenshot_dir = screenshot_dir or Path("data/screenshots")
        self._credential_store = credential_store

    async def execute(
        self,
        task: Task,
        persona: Persona,
        computer: Any,
        memories: list[str] | None = None,
    ) -> TaskResult:
        """Execute task via ComputerAgent. Returns TaskResult with success, steps, duration."""
        from soren.patches import apply_all

        apply_all()
        from agent import ComputerAgent

        started = time.perf_counter()
        step_log: list[dict[str, Any]] = []
        screenshots_captured: list[str] = []
        last_screenshot_bytes: bytes | None = None
        steps_taken, error_msg, success = 0, None, False
        verification_passed: bool | None = None
        correction_applied: str | None = None
        playbook_content = self._load_playbook_content(task)
        max_tokens_raw = getattr(self._settings.playbooks, "max_instruction_tokens", None)
        max_tokens = max_tokens_raw if isinstance(max_tokens_raw, int) else None
        persona_context = PersonaContext.build(
            persona,
            task,
            playbook_content=playbook_content,
            memories=self._compose_memories(task, persona, memories),
            step_budget=self._step_budget,
            max_instruction_tokens=max_tokens,
        )
        messages = [{"role": "user", "content": task.description or f"Complete: {task.title}"}]
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        try:
            agent = ComputerAgent(
                model=self._vlm_model,
                tools=[computer],
                instructions=persona_context,
                screenshot_delay=0.5,
                only_n_most_recent_images=5,
            )

            last_output: list = []
            async for result in agent.run(messages):
                steps_taken += 1
                output = result.get("output", [])
                last_output = output
                for item in output:
                    step_entry = self._parse_output_item(item, task.id, steps_taken)
                    if step_entry:
                        step_log.append(step_entry)
                    screenshot_path = self._extract_screenshot(item, task.id, steps_taken)
                    if screenshot_path:
                        screenshots_captured.append(screenshot_path)
                    screenshot_bytes = self._extract_screenshot_bytes(item)
                    if screenshot_bytes:
                        last_screenshot_bytes = screenshot_bytes

                if steps_taken >= self._step_budget:
                    error_msg = f"Step budget ({self._step_budget}) exceeded"
                    break

                if self._is_completion_message(output):
                    success = True
                    break

            if not success and not error_msg and steps_taken > 0:
                success = self._has_assistant_final(last_output)

            success, error_msg, verification_passed, correction_applied = await self._run_post_execution_verification(
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

        duration = time.perf_counter() - started
        return TaskResult(
            success=success,
            steps_taken=steps_taken,
            step_log=step_log,
            screenshot_paths=screenshots_captured,
            duration_seconds=duration,
            error=error_msg,
            verification_passed=verification_passed,
            correction_applied=correction_applied,
        )

    async def execute_with_learning(
        self,
        task: Task,
        persona: Persona,
        computer: Any,
        memories: list[str] | None = None,
    ) -> tuple[TaskResult, bool]:
        """Execute with stuck detection and tab-aware flow. Returns (result, should_offer_training)."""
        from soren.patches import apply_all

        apply_all()
        from agent import ComputerAgent

        flow = self._get_flow_playbook(task)
        effective_budget = min(flow.max_steps, self._step_budget) if flow else self._step_budget
        detector = StuckDetector(step_budget=effective_budget)
        playbook_content = self._load_playbook_content(task)
        max_tokens_raw = getattr(self._settings.playbooks, "max_instruction_tokens", None)
        max_tokens = max_tokens_raw if isinstance(max_tokens_raw, int) else None
        persona_context = PersonaContext.build(
            persona,
            task,
            playbook_content=playbook_content,
            memories=self._compose_memories(task, persona, memories),
            step_budget=effective_budget,
            max_instruction_tokens=max_tokens,
        )
        messages = [{"role": "user", "content": task.description or f"Complete: {task.title}"}]
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        step_log: list[dict[str, Any]] = []
        screenshots_captured: list[str] = []
        last_screenshot_bytes: bytes | None = None
        steps_taken, error_msg, success, stuck = 0, None, False, False
        verification_passed: bool | None = None
        correction_applied: str | None = None

        # Tab-aware state
        platform_domain = self._detect_host_domain(task.source_url or "")
        pre_tab_count: int | None = None

        try:
            # Record initial tab count for tab transition detection
            try:
                pre_tab_count = await self._vm.get_tab_count()
            except Exception:
                pre_tab_count = None

            agent = ComputerAgent(
                model=self._vlm_model,
                tools=[computer],
                instructions=persona_context,
                screenshot_delay=0.5,
                only_n_most_recent_images=5,
            )

            last_output: list = []
            async for result in agent.run(messages):
                steps_taken += 1
                output = result.get("output", [])
                last_output = output

                step_screenshot, step_action = None, "unknown"
                for item in output:
                    step_entry = self._parse_output_item(item, task.id, steps_taken)
                    if step_entry:
                        step_log.append(step_entry)
                        step_action = step_entry.get("action_type", "unknown")
                    screenshot_path = self._extract_screenshot(item, task.id, steps_taken)
                    if screenshot_path:
                        screenshots_captured.append(screenshot_path)
                    screenshot_bytes = self._extract_screenshot_bytes(item)
                    if screenshot_bytes:
                        last_screenshot_bytes = screenshot_bytes
                        step_screenshot = screenshot_bytes

                # Tab transition detection after each step
                old_tab_count = pre_tab_count
                pre_tab_count = await self._handle_tab_transition(
                    task, messages, step_log, steps_taken, pre_tab_count, platform_domain
                )
                # If a tab transition was detected (count changed), attempt platform return
                # after the agent processes the host content in subsequent steps
                if pre_tab_count is not None and old_tab_count is not None and pre_tab_count > old_tab_count:
                    await self._handle_platform_return(platform_domain, step_log, steps_taken)

                detector.record_step(step_screenshot, step_action)
                if steps_taken >= effective_budget:
                    error_msg = f"Step budget ({effective_budget}) exceeded"
                    break

                if self._is_completion_message(output):
                    detector.record_completion_signal()
                    success = True
                    break

                is_stuck, stuck_reason = detector.is_stuck()
                if is_stuck:
                    stuck = True
                    error_msg = f"Stuck detected: {stuck_reason}"
                    break

            if not success and not error_msg and steps_taken > 0:
                success = self._has_assistant_final(last_output)

            success, error_msg, verification_passed, correction_applied = await self._run_post_execution_verification(
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

        duration = time.perf_counter() - started
        task_result = TaskResult(
            success=success,
            steps_taken=steps_taken,
            step_log=step_log,
            screenshot_paths=screenshots_captured,
            duration_seconds=duration,
            error=error_msg,
            verification_passed=verification_passed,
            correction_applied=correction_applied,
        )

        return task_result, stuck

    async def _handle_tab_transition(
        self,
        task: Task,
        messages: list[dict[str, Any]],
        step_log: list[dict[str, Any]],
        steps_taken: int,
        pre_tab_count: int | None,
        platform_domain: str | None,
    ) -> int | None:
        """Detect and handle tab transitions during execution.

        Returns the updated tab count (to prevent repeated triggers), or the
        original pre_tab_count if no transition was detected.
        """
        if pre_tab_count is None:
            return None

        try:
            new_tab_detected = await self._vm.wait_for_new_tab(pre_tab_count, timeout_s=1.0)
            if not new_tab_detected:
                return pre_tab_count

            # New tab opened — get URL and extract host domain
            tab_url = await self._vm.get_active_tab_url()
            host_domain = self._detect_host_domain(tab_url)
            if not host_domain or host_domain == platform_domain:
                # Same domain or can't parse — update count but skip delegation
                try:
                    return await self._vm.get_tab_count()
                except Exception:
                    return pre_tab_count

            # Log the tab transition
            step_log.append(
                {
                    "step": steps_taken,
                    "action_type": "tab_switch",
                    "action_data": {"url": tab_url, "host_domain": host_domain},
                    "vlm_reasoning": f"DELEGATE_TO_HOST: new tab detected at {host_domain}",
                }
            )

            # Resolve host-specific playbook content and inject into agent context
            host_content = self._load_playbook_content(task, host_domain=host_domain)
            if host_content:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"DELEGATE_TO_HOST: You are now on {host_domain}. "
                            f"Follow these host-specific instructions:\n\n{host_content}"
                        ),
                    }
                )
                logger.info("Injected host playbook content for %s", host_domain)

            # Update tab count to prevent repeated triggers
            return await self._vm.get_tab_count()

        except Exception:
            logger.debug("Tab transition handling failed", exc_info=True)
            return pre_tab_count

    async def _handle_platform_return(
        self,
        platform_domain: str | None,
        step_log: list[dict[str, Any]],
        steps_taken: int,
    ) -> None:
        """Switch back to the platform tab after delegated host execution."""
        if not platform_domain:
            return
        try:
            tab_idx = await self._vm.find_tab_by_domain(platform_domain)
            if tab_idx is not None:
                switched = await self._vm.switch_to_tab(tab_idx)
                if switched:
                    step_log.append(
                        {
                            "step": steps_taken,
                            "action_type": "tab_switch",
                            "action_data": {"domain": platform_domain, "tab_index": tab_idx},
                            "vlm_reasoning": f"returns_to_platform: switched back to {platform_domain}",
                        }
                    )
            else:
                logger.warning("Platform tab for %s not found; staying on current tab", platform_domain)
        except Exception:
            logger.debug("Platform return failed", exc_info=True)

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

    def _detect_host_domain(self, url: str) -> str | None:
        """Extract domain from a URL for host playbook resolution."""
        if not url:
            return None
        try:
            parsed = urlparse(url)
            return parsed.hostname or None
        except Exception:
            return None

    def _remaining_budget(self, steps_taken: int) -> int:
        """Calculate remaining step budget for delegated execution."""
        return max(1, self._step_budget - steps_taken)

    def _compose_memories(
        self,
        task: Task,
        persona: Persona,
        memories: list[str] | None,
    ) -> list[str]:
        """Merge recalled memories with explicit credential handoff instructions."""
        combined = list(memories or [])
        credential_handoff = self._build_credential_handoff(task, persona)
        if credential_handoff:
            combined.append(credential_handoff)
        return combined

    def _build_credential_handoff(self, task: Task, persona: Persona) -> str | None:
        """Build execution-time login instructions from source credential_ref."""
        source_ref = ""
        for source in persona.task_sources:
            if source.name == task.source_name:
                source_ref = source.credential_ref.strip()
                break
        if not source_ref:
            return None

        if self._credential_store is None:
            try:
                self._credential_store = CredentialStore(
                    Path("config/credentials.enc"),
                    key_path=Path("data/credentials.key"),
                )
            except Exception:
                return (
                    "Credential handoff:\n"
                    f"- credential_ref: {source_ref}\n"
                    "- Encrypted credential store failed to initialize.\n"
                    "- If login is required, stop and report login_failure."
                )

        try:
            cred = self._credential_store.get(source_ref)
        except (KeyError, ValueError):
            return (
                "Credential handoff:\n"
                f"- credential_ref: {source_ref}\n"
                "- Encrypted credential was not available at runtime.\n"
                "- If login is required, stop and report login_failure."
            )

        return (
            "Credential handoff (sensitive; do not echo in outputs):\n"
            f"- credential_ref: {source_ref}\n"
            f"- username: {cred.username}\n"
            f"- password: {cred.password}\n"
            "- Use only for required login actions, then continue task execution."
        )

    def _parse_output_item(self, item: dict[str, Any], task_id: str, step: int) -> dict[str, Any] | None:
        """Extract reasoning, action type, coordinates from agent output item."""
        if not isinstance(item, dict):
            return None
        kind = item.get("type") or item.get("role")
        if kind == "computer_call":
            call = item.get("call", item)
            action = call.get("action", {}) if isinstance(call, dict) else {}
            action_type = action.get("type", "unknown")
            action_data = action if isinstance(action, dict) else {}
            return {
                "step": step,
                "action_type": action_type,
                "action_data": action_data,
                "vlm_reasoning": item.get("summary", item.get("reasoning")),
            }
        if kind == "message" and item.get("role") == "assistant":
            content = item.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "output_text"
                ]
                content = " ".join(text_parts)
            return {
                "step": step,
                "action_type": "message",
                "action_data": {"content": str(content)},
                "vlm_reasoning": str(content)[:500],
            }
        return None

    def _extract_screenshot(self, item: dict[str, Any], task_id: str, step: int) -> str | None:
        """Save screenshot from computer_call_output if present. Returns path or None."""
        if not isinstance(item, dict):
            return None
        if item.get("type") != "computer_call_output":
            return None
        output = item.get("output", {})
        if not isinstance(output, dict):
            return None
        img_data = output.get("image_url") or output.get("image") or output.get("screenshot")
        if not img_data:
            return None
        import base64

        if isinstance(img_data, str) and img_data.startswith("data:"):
            img_data = img_data.split(",", 1)[-1]
        try:
            raw = base64.b64decode(img_data)
        except Exception:
            return None
        path = self._screenshot_dir / f"{task_id}_step{step}_{uuid.uuid4().hex[:8]}.png"
        path.write_bytes(raw)
        return str(path)

    def _extract_screenshot_bytes(self, item: dict[str, Any]) -> bytes | None:
        """Extract screenshot bytes from computer_call_output if present."""
        if not isinstance(item, dict) or item.get("type") != "computer_call_output":
            return None
        output = item.get("output", {})
        if not isinstance(output, dict):
            return None
        img_data = output.get("image_url") or output.get("image") or output.get("screenshot")
        if not img_data:
            return None
        import base64

        if isinstance(img_data, str) and img_data.startswith("data:"):
            img_data = img_data.split(",", 1)[-1]
        try:
            return base64.b64decode(img_data)
        except Exception:
            return None

    async def _run_post_execution_verification(
        self,
        task: Task,
        computer: Any,
        success: bool,
        error_msg: str | None,
        last_screenshot_bytes: bytes | None,
        steps_taken: int,
        step_log: list[dict[str, Any]],
    ) -> tuple[bool, str | None, bool | None, str | None]:
        """Run verification and correction after agent loop. Returns (success, error, verified, correction)."""
        verification_passed: bool | None = None
        correction_applied: str | None = None
        if not (success and last_screenshot_bytes):
            return success, error_msg, verification_passed, correction_applied

        verification_passed = await self._verify_completion(task, last_screenshot_bytes)
        step_log.append(
            {
                "step": steps_taken,
                "action_type": "verify",
                "action_data": {"passed": verification_passed},
                "vlm_reasoning": "Tiered verification after completion signal",
            }
        )
        if verification_passed is False:
            strategy = await self._attempt_corrections(computer)
            if strategy:
                correction_applied = strategy.value
                step_log.append(
                    {
                        "step": steps_taken,
                        "action_type": "correction",
                        "action_data": {"strategy": strategy.value},
                        "vlm_reasoning": "Applied correction after verification failure",
                    }
                )
            success = False
            if not error_msg:
                error_msg = "Verification failed after completion signal"
        return success, error_msg, verification_passed, correction_applied

    async def _verify_completion(self, task: Task, screenshot: bytes) -> bool:
        """Run tiered verification for completion confirmation."""
        verification_cfg = getattr(self._settings, "verification", None)
        verification_model = verification_cfg.model if verification_cfg else "qwen3-vl:8b"
        verifier = ActionVerifier(
            baseline_manager=BaselineManager(Path("data/baselines")),
            llm_service=self._llm,
            verification_model=verification_model,
        )
        expected_state = task.title or task.description or "Task completed successfully"
        baseline_name = task.source_name or None
        result = await verifier.verify(
            screenshot=screenshot,
            expected_state=expected_state,
            baseline_name=baseline_name,
        )
        return result.passed

    async def _attempt_corrections(self, computer: Any) -> CorrectionStrategy | None:
        """Apply correction strategies after verification failure."""
        interface = getattr(computer, "interface", None)
        if interface is None:
            return None

        for strategy in (
            CorrectionStrategy.KEYPRESS,
            CorrectionStrategy.OSASCRIPT_ACTIVATE,
            CorrectionStrategy.FULL_OSASCRIPT,
        ):
            ok = await apply_correction(strategy, interface, {"failure": "verification_failed"})
            if ok:
                return strategy
        return None

    def _is_completion_message(self, output: list) -> bool:
        """Detect if output indicates task completion."""
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("role") == "assistant":
                content = item.get("content", "")
                if isinstance(content, str):
                    lower = content.lower()
                    if "done" in lower or "complete" in lower or "finished" in lower:
                        return True
                if isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict):
                            text = p.get("text", "")
                            if "done" in text.lower() or "complete" in text.lower():
                                return True
        return False

    def _has_assistant_final(self, output: list) -> bool:
        """Check if last output has assistant message."""
        for item in reversed(output):
            if isinstance(item, dict) and item.get("role") == "assistant":
                return True
        return False
