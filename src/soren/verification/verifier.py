"""Two-tier verification: OpenCV baseline + Qwen3-VL semantic."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel

from soren.verification.baseline import BaselineManager, compare_with_baseline

if TYPE_CHECKING:
    from soren.services.llm_service import LLMService


class VerificationResult(BaseModel):
    """Result of verification check."""

    passed: bool
    confidence: float
    tier_used: int
    reasoning: str | None = None
    duration_ms: float = 0.0


DEFAULT_THRESHOLD = 0.85
VERIFICATION_PROMPT = """Analyze this screenshot. Does it show the expected state?

Expected: {expected_state}

Respond with exactly one of:
PASS - if the screenshot matches the expected state
FAIL - if it does not match

Optional reasoning on the next line after PASS or FAIL."""


class ActionVerifier:
    """Two-tier verification: Tier 1 baseline, Tier 2 Qwen3-VL."""

    def __init__(
        self,
        baseline_manager: BaselineManager,
        llm_service: LLMService | None = None,
        verification_model: str = "qwen3-vl:8b",
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._baseline = baseline_manager
        self._llm = llm_service
        self._model = verification_model
        self._threshold = threshold

    async def verify(
        self,
        screenshot: bytes,
        expected_state: str,
        baseline_name: str | None = None,
    ) -> VerificationResult:
        """Verify screenshot against expected state. Tier 1 first, escalate to Tier 2 if needed."""
        start = time.perf_counter()

        # Tier 1: baseline comparison
        if baseline_name:
            baseline_bytes = self._baseline.load(baseline_name)
            if baseline_bytes:
                similarity = compare_with_baseline(screenshot, baseline_bytes)
                duration_ms = (time.perf_counter() - start) * 1000
                if similarity >= self._threshold:
                    return VerificationResult(
                        passed=True,
                        confidence=similarity,
                        tier_used=1,
                        reasoning=None,
                        duration_ms=duration_ms,
                    )
                # Below threshold: escalate to Tier 2

        # Tier 2: semantic verification via VLM
        if self._llm:
            prompt = VERIFICATION_PROMPT.format(expected_state=expected_state)
            try:
                response = await self._llm.generate(
                    prompt=prompt,
                    model=self._model,
                    images=[screenshot],
                )
                passed, reasoning = _parse_vlm_response(response)
                duration_ms = (time.perf_counter() - start) * 1000
                return VerificationResult(
                    passed=passed,
                    confidence=0.9 if passed else 0.3,
                    tier_used=2,
                    reasoning=reasoning,
                    duration_ms=duration_ms,
                )
            except Exception:
                duration_ms = (time.perf_counter() - start) * 1000
                return VerificationResult(
                    passed=False,
                    confidence=0.0,
                    tier_used=2,
                    reasoning="Tier 2 VLM call failed",
                    duration_ms=duration_ms,
                )

        # No baseline match and no LLM: fail
        duration_ms = (time.perf_counter() - start) * 1000
        return VerificationResult(
            passed=False,
            confidence=0.0,
            tier_used=1,
            reasoning="No baseline match and no VLM available",
            duration_ms=duration_ms,
        )


def _parse_vlm_response(response: str) -> tuple[bool, str | None]:
    """Parse PASS/FAIL from VLM response."""
    response = response.strip().upper()
    pass_match = re.search(r"\bPASS\b", response)
    fail_match = re.search(r"\bFAIL\b", response)
    if pass_match and not fail_match:
        return True, response if len(response) > 4 else None
    if fail_match:
        return False, response if len(response) > 4 else None
    return False, response or None
