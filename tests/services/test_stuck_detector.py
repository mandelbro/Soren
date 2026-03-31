"""Tests for StuckDetector service (Task 010)."""

from __future__ import annotations

from unittest.mock import patch

from soren.services.stuck_detector import StuckDetector


class TestSameScreenDetection:
    def test_triggers_after_n_identical_screenshots(self) -> None:
        detector = StuckDetector(step_budget=50, same_screen_threshold=3)
        screenshot = b"identical_screenshot_data"
        for _ in range(3):
            detector.record_step(screenshot, "click")

        stuck, reason = detector.is_stuck()
        assert stuck is True
        assert "same screenshot" in reason.lower()

    def test_does_not_trigger_with_fewer_than_threshold(self) -> None:
        detector = StuckDetector(step_budget=50, same_screen_threshold=3)
        screenshot = b"identical_screenshot_data"
        for _ in range(2):
            detector.record_step(screenshot, "click")

        stuck, _ = detector.is_stuck()
        assert stuck is False

    def test_does_not_trigger_with_different_screenshots(self) -> None:
        detector = StuckDetector(step_budget=50, same_screen_threshold=3)
        for i in range(5):
            detector.record_step(f"screenshot_{i}".encode(), "click")

        stuck, _ = detector.is_stuck()
        assert stuck is False

    def test_histogram_similarity_fallback(self) -> None:
        detector = StuckDetector(step_budget=50, same_screen_threshold=3)
        # Different byte content but high histogram similarity
        for i in range(3):
            detector.record_step(f"slightly_different_{i}".encode(), "click")

        with patch(
            "soren.services.stuck_detector.compare_with_baseline",
            return_value=0.99,
        ):
            stuck, reason = detector.is_stuck()
            assert stuck is True
            assert "similar" in reason.lower() or "screenshot" in reason.lower()


class TestActionLoopDetection:
    def test_triggers_with_repeating_pattern(self) -> None:
        detector = StuckDetector(step_budget=50, loop_detection_window=3)
        # Create a repeating pattern: [click, scroll, click, scroll, click, scroll]
        actions = ["click", "scroll", "type"] * 2
        for action in actions:
            detector.record_step(None, action)

        stuck, reason = detector.is_stuck()
        assert stuck is True
        assert "loop" in reason.lower()

    def test_does_not_trigger_with_non_repeating_actions(self) -> None:
        detector = StuckDetector(step_budget=50, loop_detection_window=3)
        for action in ["click", "scroll", "type", "click", "drag", "hover"]:
            detector.record_step(None, action)

        stuck, _ = detector.is_stuck()
        assert stuck is False

    def test_does_not_trigger_with_insufficient_actions(self) -> None:
        detector = StuckDetector(step_budget=50, loop_detection_window=6)
        detector.record_step(None, "click")
        detector.record_step(None, "scroll")

        stuck, _ = detector.is_stuck()
        assert stuck is False


class TestBudgetExhaustion:
    def test_triggers_at_threshold(self) -> None:
        detector = StuckDetector(step_budget=10, budget_exhaustion_pct=0.8)
        for i in range(8):
            detector.record_step(f"s{i}".encode(), f"action_{i}")

        stuck, reason = detector.is_stuck()
        assert stuck is True
        assert "budget" in reason.lower()

    def test_does_not_trigger_if_completion_signal_recorded(self) -> None:
        detector = StuckDetector(step_budget=10, budget_exhaustion_pct=0.8)
        for i in range(8):
            detector.record_step(f"s{i}".encode(), f"action_{i}")
        detector.record_completion_signal()

        stuck, _ = detector.is_stuck()
        assert stuck is False

    def test_does_not_trigger_below_threshold(self) -> None:
        detector = StuckDetector(step_budget=10, budget_exhaustion_pct=0.8)
        for i in range(7):
            detector.record_step(f"s{i}".encode(), f"action_{i}")

        stuck, _ = detector.is_stuck()
        assert stuck is False


class TestIsStuckPriority:
    def test_returns_first_matching_reason(self) -> None:
        detector = StuckDetector(step_budget=10, same_screen_threshold=3, budget_exhaustion_pct=0.8)
        # Both same-screenshot and budget exhaustion should trigger
        screenshot = b"same"
        for _ in range(8):
            detector.record_step(screenshot, "click")

        stuck, reason = detector.is_stuck()
        assert stuck is True
        # First check is same_screenshot
        assert "screenshot" in reason.lower()


class TestMemoryBounding:
    def test_record_step_bounds_memory(self) -> None:
        window = 4
        detector = StuckDetector(step_budget=100, loop_detection_window=window)
        # Record many more steps than the window
        for i in range(50):
            detector.record_step(f"s{i}".encode(), f"action_{i}")

        # Internal history should be bounded
        max_entries = window * 2
        assert len(detector._screenshots) <= max_entries
        assert len(detector._actions) <= max_entries
