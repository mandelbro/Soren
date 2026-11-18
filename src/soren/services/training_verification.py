"""VerifyRetryLoop — execute a training instruction, auto-verify it, retry with feedback, then ask the human.

Each attempt runs the instruction, verifies the resulting screen against the
trainer's expected outcome (VLM), and on failure retries with the verifier's
reasoning and the last clicked pixel folded into the instruction. After the
retries are exhausted the trainer decides: skip, accept, abort, or retry.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from soren.services.training_prompts import compose_retry_instruction
from soren.services.training_types import TrainingAbortRequested, TrainingCallbacks

if TYPE_CHECKING:
    from soren.verification.verifier import ActionVerifier, VerificationResult

logger = logging.getLogger(__name__)

# Default number of auto-verify retries before falling back to human.
DEFAULT_MAX_VERIFY_RETRIES = 3


class VerifyRetryLoop:
    """Verify+retry for one training session.

    Collaborators are passed as callables and resolved at call time, so the
    session stays the owner of step execution and the (lazily built) verifier.
    """

    def __init__(
        self,
        callbacks: TrainingCallbacks,
        execute_step: Callable[[str], Awaitable[tuple[bytes | None, str]]],
        get_verifier: Callable[[], ActionVerifier],
        last_click: Callable[[], tuple[int, int] | None],
        max_retries: int = DEFAULT_MAX_VERIFY_RETRIES,
    ) -> None:
        self._callbacks = callbacks
        self._execute_step = execute_step
        self._get_verifier = get_verifier
        self._last_click = last_click
        self._max_retries = max_retries

    async def execute_and_verify(
        self,
        instruction: str,
        expected_after: str,
    ) -> tuple[bytes, str, bool] | None:
        """Execute instruction, auto-verify against expected_after, retry on failure.

        On retry attempts (attempt >= 2), the instruction is augmented with the
        previous verifier's failure reasoning so UI-TARS has a corrective signal
        and stops picking the same (wrong) coordinates. Without this augmentation
        the retry is effectively a no-op against deterministic models.

        Returns ``(screenshot, description, verification_passed)`` on success/acceptance,
        or ``None`` if the step was skipped.
        Raises ``TrainingAbortRequested`` if the human chooses abort.
        """
        max_retries = self._max_retries
        last_reasoning: str | None = None
        last_click_xy: tuple[int, int] | None = None
        last_screenshot: bytes = b""
        last_description: str = ""

        for attempt in range(1, max_retries + 1):
            # 1. Execute via CUA (reuses existing retry-on-exception logic).
            # On retry, thread the verifier's most recent failure reasoning AND
            # the exact pixel the previous attempt clicked into the instruction
            # so UI-TARS has a concrete corrective signal.
            if attempt > 1 and last_reasoning:
                effective_instruction = compose_retry_instruction(
                    instruction, attempt, max_retries, last_reasoning, last_click_xy
                )
                logger.info(
                    "Retry attempt %d/%d — augmenting instruction with verifier feedback "
                    "(%d chars reasoning, last_click=%s)",
                    attempt,
                    max_retries,
                    len(last_reasoning),
                    last_click_xy,
                )
            else:
                effective_instruction = instruction
            screenshot, description = await self._try_execute_step(effective_instruction)
            # Capture the click coordinate this attempt emitted (if any) before
            # the next step resets it in the step runner.
            last_click_xy = self._last_click()
            if screenshot is None:
                return None  # CUA execution failed entirely
            last_screenshot, last_description = screenshot, description

            # 2. Auto-verify using ActionVerifier Tier 2 (VLM semantic check)
            verifier = self._get_verifier()
            vr: VerificationResult = await verifier.verify(
                screenshot=last_screenshot,
                expected_state=expected_after,
                baseline_name=None,  # No baselines during training
            )

            # 3. Show verification result (transparency)
            await self._callbacks.show_verification_result(
                attempt=attempt,
                max_attempts=max_retries,
                passed=vr.passed,
                confidence=vr.confidence,
                reasoning=vr.reasoning,
            )
            last_reasoning = vr.reasoning

            # 4. If passed, return immediately
            if vr.passed:
                return last_screenshot, last_description, True

            # 5. If not passed and more retries, auto-retry
            if attempt < max_retries:
                await self._callbacks.show_message(f"Verification failed. Auto-retrying ({attempt}/{max_retries})...")

        # All retries exhausted — fall back to human
        action = await self._callbacks.handle_stuck(
            instruction=instruction,
            expected_after=expected_after,
            attempts=max_retries,
            last_reasoning=last_reasoning,
        )

        return self._process_stuck_action(action, last_screenshot, last_description, expected_after)

    def _process_stuck_action(
        self,
        action: str,
        last_screenshot: bytes,
        last_description: str,
        expected_after: str,
    ) -> tuple[bytes, str, bool] | None:
        """Interpret the human's stuck-handler decision."""
        action = action.strip().lower()
        if action == "skip":
            return None
        if action == "accept":
            return last_screenshot, last_description, False
        if action == "abort":
            raise TrainingAbortRequested()
        # "retry:<new_instruction>" is not handled synchronously —
        # the caller should re-enter _execute_and_verify. But to keep
        # this simple, we return None (skip) and let the loop continue.
        # The retry-with-new-instruction path is handled at the _step_loop level.
        return None

    async def _try_execute_step(self, instruction: str) -> tuple[bytes | None, str]:
        """Execute a single CUA step with error recovery.

        Returns (screenshot, description) on success, (None, "") if user chose to skip.
        """
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                screenshot, description = await self._execute_step(instruction)
                return screenshot or b"", description
            except Exception as exc:
                await self._callbacks.show_error(f"CUA error (attempt {attempt}/{max_attempts}): {exc}")
                if attempt < max_attempts:
                    await self._callbacks.show_message("Type 'skip' to skip this step, or press Enter to retry.")
                    resp = await self._callbacks.get_instruction()
                    if resp.strip().lower() in ("skip", "s"):
                        return None, ""
                else:
                    await self._callbacks.show_error("Max retries reached. Skipping step.")
                    return None, ""
        return None, ""  # pragma: no cover
