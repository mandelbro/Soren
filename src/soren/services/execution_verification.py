"""PostExecutionVerifier — tiered verification and correction after the agent signals completion.

When the agent reports success and a screenshot is available, the final
screen is verified against the task (``ActionVerifier``). On failure,
correction strategies are tried (keypress, osascript activate, full osascript)
and the run is marked failed. Both steps are recorded in the step log.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from soren.models.task import Task
from soren.verification.baseline import BaselineManager
from soren.verification.correction import CorrectionStrategy, apply_correction
from soren.verification.verifier import ActionVerifier

if TYPE_CHECKING:
    from soren.services.config_loader import SorenSettings
    from soren.services.llm_service import LLMService


class PostExecutionVerifier:
    """Verifies completion claims and applies corrections on failure."""

    def __init__(self, settings: SorenSettings, llm: LLMService) -> None:
        self._settings = settings
        self._llm = llm

    async def run(
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

        verification_passed = await self.verify_completion(task, last_screenshot_bytes)
        step_log.append(
            {
                "step": steps_taken,
                "action_type": "verify",
                "action_data": {"passed": verification_passed},
                "vlm_reasoning": "Tiered verification after completion signal",
            }
        )
        if verification_passed is False:
            strategy = await self.attempt_corrections(computer)
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

    async def verify_completion(self, task: Task, screenshot: bytes) -> bool:
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

    async def attempt_corrections(self, computer: Any) -> CorrectionStrategy | None:
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
