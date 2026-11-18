"""LearningMonitor: per-step tab handling and stuck detection for execute_with_learning."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from soren.services.learning_monitor import LearningMonitor


def _monitor(tab_counts=(1,), handled=(1,)):
    vm = MagicMock()
    vm.get_tab_count = AsyncMock(side_effect=list(tab_counts))
    tabs = MagicMock()
    tabs.handle_transition = AsyncMock(side_effect=list(handled))
    tabs.return_to_platform = AsyncMock()
    detector = MagicMock()
    return LearningMonitor(vm, tabs, detector, "platform.example"), vm, tabs, detector


class TestLearningMonitor:
    async def test_start_records_tab_count_and_handles_errors(self) -> None:
        monitor, vm, tabs, _ = _monitor()
        await monitor.start()
        await monitor.after_step([], [], 1, None, "click")
        assert tabs.handle_transition.await_args.args[3] == 1  # passes the recorded count
        broken, vm2, tabs2, _ = _monitor()
        vm2.get_tab_count = AsyncMock(side_effect=RuntimeError("no tabs"))
        await broken.start()
        await broken.after_step([], [], 1, None, "click")
        assert tabs2.handle_transition.await_args.args[3] is None

    async def test_new_tab_triggers_platform_return(self) -> None:
        monitor, _, tabs, _ = _monitor(tab_counts=(1,), handled=(2,))
        await monitor.start()
        await monitor.after_step([], ["log"], 4, None, "click")
        tabs.return_to_platform.assert_awaited_once_with("platform.example", ["log"], 4)

    async def test_no_return_without_new_tab(self) -> None:
        monitor, _, tabs, _ = _monitor(tab_counts=(2,), handled=(2,))
        await monitor.start()
        await monitor.after_step([], [], 1, None, "click")
        tabs.return_to_platform.assert_not_awaited()

    async def test_records_step_completion_and_stuck_passthrough(self) -> None:
        monitor, _, _, detector = _monitor()
        detector.is_stuck.return_value = (True, "")
        await monitor.start()
        await monitor.after_step([], [], 1, b"png", "type")
        detector.record_step.assert_called_once_with(b"png", "type")
        monitor.record_completion()
        detector.record_completion_signal.assert_called_once()
        assert monitor.is_stuck() == (True, "")
