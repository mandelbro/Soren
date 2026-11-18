"""LearningMonitor — the extra per-step checks TaskExecutor runs in execute_with_learning.

Plain ``execute`` and ``execute_with_learning`` share one agent loop; the
learning variant adds this monitor, which:

- records the tab count before the loop and, after each step, handles new-tab
  transitions (host playbook delegation) and the return to the platform tab;
- feeds each step's screenshot and action to the ``StuckDetector``, records the
  completion signal, and reports whether the agent is stuck.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from soren.services.stuck_detector import StuckDetector
    from soren.services.task_tabs import TabTransitionHandler
    from soren.services.vm_manager import VMManager


class LearningMonitor:
    """Tab transitions and stuck detection for one execute_with_learning run."""

    def __init__(
        self,
        vm: VMManager,
        tabs: TabTransitionHandler,
        detector: StuckDetector,
        platform_domain: str | None,
    ) -> None:
        self._vm = vm
        self._tabs = tabs
        self._detector = detector
        self._platform_domain = platform_domain
        self._tab_count: int | None = None

    async def start(self) -> None:
        """Record the initial tab count; tab handling stays off if it can't be read."""
        try:
            self._tab_count = await self._vm.get_tab_count()
        except Exception:
            self._tab_count = None

    async def after_step(
        self,
        messages: list[dict[str, Any]],
        step_log: list[dict[str, Any]],
        steps_taken: int,
        step_screenshot: bytes | None,
        step_action: str,
    ) -> None:
        """Handle tab transitions caused by this step, then record it for stuck detection."""
        previous = self._tab_count
        self._tab_count = await self._tabs.handle_transition(
            messages, step_log, steps_taken, self._tab_count, self._platform_domain
        )
        # A new tab opened: attempt the platform return after the host content was injected.
        if self._tab_count is not None and previous is not None and self._tab_count > previous:
            await self._tabs.return_to_platform(self._platform_domain, step_log, steps_taken)
        self._detector.record_step(step_screenshot, step_action)

    def record_completion(self) -> None:
        self._detector.record_completion_signal()

    def is_stuck(self) -> tuple[bool, str]:
        return self._detector.is_stuck()
