"""Characterization of TaskExecutor's agent loops (execute / execute_with_learning).

Pinned before merging the two duplicated loops into one, so the merge can't
silently change step counting, completion, budget, tab handling, or errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.persona import Persona
from soren.models.task import Task
from soren.services.config_loader import SorenSettings
from soren.services.service_registry import ServiceRegistry
from soren.services.task_executor import TaskExecutor

pytestmark = pytest.mark.usefixtures("fake_cua_sdk")

METHODS = ["execute", "execute_with_learning"]
CLICK = {"output": [{"type": "computer_call", "action": {"type": "click", "x": 1, "y": 1}}]}
DONE = {"output": [{"role": "assistant", "content": "Task complete."}]}


def _task(url: str = "https://platform.example/home") -> Task:
    return Task(id="t1", dedup_key="k", title="T", description="Do it", source_name="src", source_url=url)


def _executor(tmp_path: Path, vm: Any = None, step_budget: int | None = None) -> TaskExecutor:
    return TaskExecutor(
        SorenSettings(),
        MagicMock(),
        vm or MagicMock(),
        step_budget=step_budget,
        screenshot_dir=tmp_path / "shots",
        service_registry=ServiceRegistry(tmp_path / "services"),
    )


def _never_stuck() -> MagicMock:
    detector = MagicMock()
    detector.is_stuck.return_value = (False, "")
    return detector


async def _run(executor: TaskExecutor, method: str, yields: list[dict], captured: list | None = None, detector=None):
    """Run with a mocked agent. The StuckDetector is replaced (default: never stuck) so loop mechanics are isolated."""

    async def run(messages):
        if captured is not None:
            captured.append(messages)
        for item in yields:
            yield item

    with (
        patch("cua_agent.ComputerAgent") as agent_cls,
        patch("soren.services.task_executor.StuckDetector", return_value=detector or _never_stuck()),
    ):
        agent_cls.return_value.run = run
        out = await getattr(executor, method)(_task(), Persona(name="P"), MagicMock())
    return out if isinstance(out, tuple) else (out, None)


@pytest.mark.parametrize("method", METHODS)
class TestSharedLoopBehavior:
    async def test_budget_wins_over_completion_on_same_step(self, tmp_path: Path, method: str) -> None:
        result, _ = await _run(_executor(tmp_path, step_budget=2), method, [CLICK, DONE])
        assert (result.success, result.steps_taken, result.error) == (False, 2, "Step budget (2) exceeded")

    async def test_completion_before_budget(self, tmp_path: Path, method: str) -> None:
        result, _ = await _run(_executor(tmp_path, step_budget=5), method, [CLICK, DONE, CLICK])
        assert (result.success, result.steps_taken, result.error) == (True, 2, None)

    async def test_assistant_reply_without_keyword_counts_as_success(self, tmp_path: Path, method: str) -> None:
        reply = {"output": [{"role": "assistant", "content": "I clicked the button."}]}
        result, _ = await _run(_executor(tmp_path), method, [CLICK, reply])
        assert (result.success, result.steps_taken) == (True, 2)

    async def test_no_steps_is_failure(self, tmp_path: Path, method: str) -> None:
        result, _ = await _run(_executor(tmp_path), method, [])
        assert (result.success, result.steps_taken, result.error) == (False, 0, None)

    async def test_agent_exception_becomes_error(self, tmp_path: Path, method: str) -> None:
        async def boom(messages):
            yield CLICK
            raise RuntimeError("vlm exploded")

        with patch("cua_agent.ComputerAgent") as agent_cls:
            agent_cls.return_value.run = boom
            out = await getattr(_executor(tmp_path), method)(_task(), Persona(name="P"), MagicMock())
        result = out[0] if isinstance(out, tuple) else out
        assert (result.success, result.steps_taken, result.error) == (False, 1, "vlm exploded")

    async def test_step_log_records_actions_and_typed_messages(self, tmp_path: Path, method: str) -> None:
        typed_done = {"output": [{"type": "message", "role": "assistant", "content": "Task complete."}]}
        result, _ = await _run(_executor(tmp_path), method, [CLICK, typed_done])
        assert [e["action_type"] for e in result.step_log] == ["click", "message"]

    async def test_untyped_assistant_reply_completes_but_is_not_logged(self, tmp_path: Path, method: str) -> None:
        """Pins existing behavior: completion keys on role, the step log keys on type == "message"."""
        result, _ = await _run(_executor(tmp_path), method, [CLICK, DONE])
        assert result.success is True
        assert [e["action_type"] for e in result.step_log] == ["click"]


class TestPlainExecuteHasNoTabLogic:
    async def test_never_touches_tabs(self, tmp_path: Path) -> None:
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(return_value=1)
        vm.wait_for_new_tab = AsyncMock(return_value=True)
        await _run(_executor(tmp_path, vm=vm), "execute", [CLICK, DONE])
        vm.get_tab_count.assert_not_awaited()
        vm.wait_for_new_tab.assert_not_awaited()


class TestLearningTabTransitions:
    def _vm(self) -> MagicMock:
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(side_effect=[1, 2, 2])  # before loop, after transition, ...
        vm.wait_for_new_tab = AsyncMock(side_effect=[True, False, False])
        vm.get_active_tab_url = AsyncMock(return_value="https://host.example/survey")
        vm.find_tab_by_domain = AsyncMock(return_value=1)
        vm.switch_to_tab = AsyncMock(return_value=True)
        return vm

    async def test_new_tab_logged_host_content_injected_and_platform_return(self, tmp_path: Path) -> None:
        vm = self._vm()
        executor = _executor(tmp_path, vm=vm)
        executor._playbook_service = MagicMock()
        executor._playbook_service.load_site.return_value = None
        executor._playbook_service.resolve.return_value = "Host tips"
        captured: list = []
        result, stuck = await _run(executor, "execute_with_learning", [CLICK, CLICK, DONE], captured)
        tab_entries = [e for e in result.step_log if e["action_type"] == "tab_switch"]
        assert tab_entries[0]["action_data"] == {"url": "https://host.example/survey", "host_domain": "host.example"}
        assert tab_entries[1]["action_data"] == {"domain": "platform.example", "tab_index": 1}
        assert any("DELEGATE_TO_HOST" in str(m.get("content")) for m in captured[0])
        vm.switch_to_tab.assert_awaited_once_with(1)
        assert (result.success, stuck) == (True, False)

    async def test_tab_count_unavailable_disables_tab_logic(self, tmp_path: Path) -> None:
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(side_effect=RuntimeError("not connected"))
        vm.wait_for_new_tab = AsyncMock()
        result, _ = await _run(_executor(tmp_path, vm=vm), "execute_with_learning", [CLICK, DONE])
        vm.wait_for_new_tab.assert_not_awaited()
        assert result.success is True

    async def test_stuck_returns_flag_and_reason(self, tmp_path: Path) -> None:
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(side_effect=RuntimeError("no tabs"))
        detector = MagicMock()
        detector.is_stuck.return_value = (True, "action loop")
        result, stuck = await _run(
            _executor(tmp_path, vm=vm), "execute_with_learning", [CLICK, CLICK], detector=detector
        )
        assert stuck is True
        assert (result.success, result.steps_taken, result.error) == (False, 1, "Stuck detected: action loop")
        detector.record_step.assert_called_once_with(None, "click")

    async def test_detector_budget_exhaustion_stops_before_step_budget(self, tmp_path: Path) -> None:
        """Pins existing behavior: the real StuckDetector flags 80% budget use as stuck."""
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(side_effect=RuntimeError("no tabs"))
        with patch("cua_agent.ComputerAgent") as agent_cls:

            async def run(messages):
                for item in [CLICK] * 6:
                    yield item

            agent_cls.return_value.run = run
            result, stuck = await _executor(tmp_path, vm=vm, step_budget=5).execute_with_learning(
                _task(), Persona(name="P"), MagicMock()
            )
        assert stuck is True
        assert result.steps_taken == 4  # int(0.8 * 5)
        assert result.error == "Stuck detected: Budget exhaustion: 4/5 steps used (80%)"


class TestScreenshotsAndStuckEdgeCases:
    async def test_step_screenshots_saved_and_listed(self, tmp_path: Path) -> None:
        import base64

        shot = {
            "output": [{"type": "computer_call_output", "output": {"image_url": base64.b64encode(b"PNG1").decode()}}]
        }
        executor = _executor(tmp_path)
        with patch.object(executor._post_verifier, "verify_completion", AsyncMock(return_value=True)) as verify:
            result, _ = await _run(executor, "execute", [CLICK, shot, DONE])
        [path] = result.screenshot_paths
        assert Path(path).read_bytes() == b"PNG1"
        assert Path(path).name.startswith("t1_step2_")
        assert verify.await_args.args[1] == b"PNG1"  # last screenshot goes to verification

    async def test_stuck_with_empty_reason_still_stops(self, tmp_path: Path) -> None:
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(side_effect=RuntimeError("no tabs"))
        detector = MagicMock()
        detector.is_stuck.return_value = (True, "")
        result, stuck = await _run(
            _executor(tmp_path, vm=vm), "execute_with_learning", [CLICK, CLICK], detector=detector
        )
        assert (stuck, result.steps_taken, result.error) == (True, 1, "Stuck detected: ")

    async def test_completion_signal_reaches_detector(self, tmp_path: Path) -> None:
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(side_effect=RuntimeError("no tabs"))
        detector = _never_stuck()
        result, _ = await _run(_executor(tmp_path, vm=vm), "execute_with_learning", [CLICK, DONE], detector=detector)
        assert result.success is True
        detector.record_completion_signal.assert_called_once()


@pytest.mark.parametrize("method", METHODS)
class TestUnexpectedActionShape:
    async def test_string_action_does_not_fail_the_task(self, tmp_path: Path, method: str) -> None:
        odd = {"output": [{"type": "computer_call", "action": "click(1, 2)"}]}
        result, _ = await _run(_executor(tmp_path), method, [odd, DONE])
        assert (result.success, result.error) == (True, None)
        assert result.step_log[0]["action_type"] == "unknown"
