"""StuckDetector — monitors CUA agent execution for stuck conditions (Task 010).

Detects three stuck conditions per discovery document Section 5:
1. Same screenshot repeated N times (byte equality + histogram similarity fallback)
2. Action loop (repeating pattern in recent actions)
3. Budget exhaustion (>80% of step budget consumed with no completion signal)
"""

from __future__ import annotations

from soren.verification.baseline import compare_with_baseline

HISTOGRAM_SIMILARITY_THRESHOLD = 0.98


class StuckDetector:
    """Monitors CUA agent step outputs for stuck conditions."""

    def __init__(
        self,
        step_budget: int,
        same_screen_threshold: int = 3,
        loop_detection_window: int = 6,
        budget_exhaustion_pct: float = 0.8,
    ) -> None:
        self._step_budget = step_budget
        self._same_screen_threshold = same_screen_threshold
        self._loop_detection_window = loop_detection_window
        self._budget_exhaustion_pct = budget_exhaustion_pct

        self._screenshots: list[bytes | None] = []
        self._actions: list[str] = []
        self._completion_seen = False
        self._max_history = loop_detection_window * 2

    def record_step(self, screenshot: bytes | None, action: str) -> None:
        """Record a step's screenshot and action, bounding memory."""
        self._screenshots.append(screenshot)
        self._actions.append(action)

        # Bound memory to prevent unbounded growth
        if len(self._screenshots) > self._max_history:
            self._screenshots = self._screenshots[-self._max_history :]
        if len(self._actions) > self._max_history:
            self._actions = self._actions[-self._max_history :]

    def record_completion_signal(self) -> None:
        """Mark that a completion signal was seen (suppresses budget exhaustion check)."""
        self._completion_seen = True

    def is_stuck(self) -> tuple[bool, str]:
        """Check all stuck conditions in priority order.

        Returns:
            (True, reason_string) on first matching condition, or (False, "").
        """
        # Check 1: Same screenshot
        result = self._check_same_screenshot()
        if result:
            return True, result

        # Check 2: Action loop
        result = self._check_action_loop()
        if result:
            return True, result

        # Check 3: Budget exhaustion
        result = self._check_budget_exhaustion()
        if result:
            return True, result

        return False, ""

    def _check_same_screenshot(self) -> str | None:
        """Check if last N screenshots are identical or near-identical."""
        threshold = self._same_screen_threshold
        screenshots = [s for s in self._screenshots if s is not None]
        if len(screenshots) < threshold:
            return None

        recent = screenshots[-threshold:]

        # Byte equality check
        if all(s == recent[0] for s in recent[1:]):
            return f"Same screenshot detected {threshold} consecutive times"

        # Histogram similarity fallback for cursor-blink false negatives
        reference = recent[0]
        all_similar = True
        for s in recent[1:]:
            similarity = compare_with_baseline(s, reference)
            if similarity < HISTOGRAM_SIMILARITY_THRESHOLD:
                all_similar = False
                break

        if all_similar:
            return (
                f"Similar screenshots detected {threshold} consecutive times"
                f" (histogram > {HISTOGRAM_SIMILARITY_THRESHOLD})"
            )

        return None

    def _check_action_loop(self) -> str | None:
        """Check if recent actions form a repeating pattern."""
        window = self._loop_detection_window
        needed = window * 2
        if len(self._actions) < needed:
            return None

        recent = self._actions[-needed:]
        first_half = recent[:window]
        second_half = recent[window:]

        if first_half == second_half:
            return f"Action loop detected: {first_half} repeated"

        return None

    def _check_budget_exhaustion(self) -> str | None:
        """Check if step budget is nearly exhausted with no completion signal."""
        if self._completion_seen:
            return None

        steps_taken = len(self._actions)
        threshold = int(self._budget_exhaustion_pct * self._step_budget)
        if steps_taken >= threshold:
            pct = self._budget_exhaustion_pct
            return f"Budget exhaustion: {steps_taken}/{self._step_budget} steps used ({pct:.0%})"

        return None
