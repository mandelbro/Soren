"""Tests for TaskExecutor.execute_with_learning() — stuck detection integration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from soren.models.persona import Persona, Traits
from soren.models.session import TaskResult
from soren.models.task import Task
from soren.services.task_executor import TaskExecutor


def _make_task(
    task_id: str = "task-1",
    description: str = "Open Safari and navigate to example.com",
    source_name: str = "TestSource",
    task_type: str = "survey",
) -> Task:
    return Task(
        id=task_id,
        dedup_key="test:1:persona",
        title="Test",
        description=description,
        task_type=task_type,
        source_name=source_name,
    )


def _make_persona() -> Persona:
    return Persona(name="Test", traits=Traits())


def _make_executor(
    tmp_path: Path,
    step_budget: int = 50,
    playbook_service: MagicMock | None = None,
) -> TaskExecutor:
    settings = MagicMock()
    llm = MagicMock()
    vm = MagicMock()
    return TaskExecutor(
        settings=settings,
        llm=llm,
        vm=vm,
        step_budget=step_budget,
        screenshot_dir=tmp_path / "screenshots",
        playbook_service=playbook_service,
    )


async def _async_gen(items):
    """Helper to create async generator from list."""
    for x in items:
        yield x


class TestExecuteWithLearningSuccess:
    """execute_with_learning returns (result, False) on success."""

    @pytest.mark.asyncio
    async def test_returns_result_and_false_on_success(self, tmp_path: Path) -> None:
        executor = _make_executor(tmp_path)

        async def completion_gen(*_args, **_kwargs):
            yield {"output": [{"role": "assistant", "content": "Task complete."}]}

        with patch("agent.ComputerAgent") as mock_cls:
            mock_agent = MagicMock()
            mock_agent.run = completion_gen
            mock_cls.return_value = mock_agent

            result, should_train = await executor.execute_with_learning(_make_task(), _make_persona(), MagicMock())

        assert isinstance(result, TaskResult)
        assert result.success is True
        assert should_train is False


class TestExecuteWithLearningStuck:
    """execute_with_learning returns (result, True) when stuck."""

    @pytest.mark.asyncio
    async def test_returns_result_and_true_when_stuck(self, tmp_path: Path) -> None:
        executor = _make_executor(tmp_path, step_budget=50)

        with (
            patch("agent.ComputerAgent") as mock_cls,
            patch("soren.services.task_executor.StuckDetector") as mock_detector_cls,
        ):
            detector_instance = MagicMock()
            detector_instance.record_step = MagicMock()
            detector_instance.is_stuck.return_value = (True, "Action loop detected")
            mock_detector_cls.return_value = detector_instance

            async def one_step(*_args, **_kwargs):
                yield {
                    "output": [
                        {
                            "type": "computer_call",
                            "call": {"action": {"type": "click"}},
                        }
                    ]
                }

            mock_agent = MagicMock()
            mock_agent.run = one_step
            mock_cls.return_value = mock_agent

            result, should_train = await executor.execute_with_learning(_make_task(), _make_persona(), MagicMock())

        assert isinstance(result, TaskResult)
        assert result.success is False
        assert should_train is True
        assert "Stuck detected" in (result.error or "")


class TestExecuteWithLearningNormalFailure:
    """execute_with_learning returns (result, False) on exception."""

    @pytest.mark.asyncio
    async def test_returns_result_and_false_on_normal_failure(self, tmp_path: Path) -> None:
        executor = _make_executor(tmp_path)

        async def error_gen(*_args, **_kwargs):
            raise RuntimeError("VLM crashed")
            yield  # noqa: B901 — makes this an async generator

        with patch("agent.ComputerAgent") as mock_cls:
            mock_agent = MagicMock()
            mock_agent.run = error_gen
            mock_cls.return_value = mock_agent

            result, should_train = await executor.execute_with_learning(_make_task(), _make_persona(), MagicMock())

        assert result.success is False
        assert should_train is False
        assert "VLM crashed" in (result.error or "")


class TestStuckDetectorReceivesStepData:
    """StuckDetector.record_step is called each iteration."""

    @pytest.mark.asyncio
    async def test_stuck_detector_receives_step_data(self, tmp_path: Path) -> None:
        executor = _make_executor(tmp_path, step_budget=50)

        with (
            patch("agent.ComputerAgent") as mock_cls,
            patch("soren.services.task_executor.StuckDetector") as mock_detector_cls,
        ):
            detector_instance = MagicMock()
            detector_instance.is_stuck.return_value = (False, "")
            mock_detector_cls.return_value = detector_instance

            async def three_steps(*_args, **_kwargs):
                yield {
                    "output": [
                        {"type": "computer_call", "call": {"action": {"type": "click"}}},
                        {"type": "computer_call_output", "output": {"image": "aGVsbG8="}},
                    ]
                }
                yield {
                    "output": [
                        {"type": "computer_call", "call": {"action": {"type": "type"}}},
                    ]
                }
                yield {"output": [{"role": "assistant", "content": "Task complete."}]}

            mock_agent = MagicMock()
            mock_agent.run = three_steps
            mock_cls.return_value = mock_agent

            await executor.execute_with_learning(_make_task(), _make_persona(), MagicMock())

        # record_step called for each of the 3 iterations
        assert detector_instance.record_step.call_count == 3
        # First call should have screenshot bytes (from computer_call_output)
        first_call_screenshot = detector_instance.record_step.call_args_list[0][0][0]
        assert first_call_screenshot is not None
        first_call_action = detector_instance.record_step.call_args_list[0][0][1]
        assert first_call_action == "click"
        # Second call has no screenshot
        second_call_screenshot = detector_instance.record_step.call_args_list[1][0][0]
        assert second_call_screenshot is None
        second_call_action = detector_instance.record_step.call_args_list[1][0][1]
        assert second_call_action == "type"


class TestPlaybookChainResolved:
    """Playbook content is resolved and included in CUA instructions."""

    @pytest.mark.asyncio
    async def test_playbook_chain_resolved_and_included(self, tmp_path: Path) -> None:
        playbook_svc = MagicMock()
        playbook_svc.resolve.return_value = "Step 1: Click login\nStep 2: Enter credentials"
        playbook_svc.load_site.return_value = None  # no FlowPlaybook
        executor = _make_executor(tmp_path, playbook_service=playbook_svc)

        async def completion_gen(*_args, **_kwargs):
            yield {"output": [{"role": "assistant", "content": "Done."}]}

        with patch("agent.ComputerAgent") as mock_cls:
            mock_agent = MagicMock()
            mock_agent.run = completion_gen
            mock_cls.return_value = mock_agent

            await executor.execute_with_learning(_make_task(), _make_persona(), MagicMock())

            # Verify playbook service was called
            playbook_svc.resolve.assert_called_once_with(
                source_name="TestSource",
                host_domain=None,
                task_type="survey",
                flow_type="execution",
            )
            # Verify playbook content appears exactly once in agent instructions
            instructions = mock_cls.call_args[1]["instructions"]
            assert "Click login" in instructions
            assert instructions.count("Click login") == 1


class TestExecuteUnchanged:
    """Existing execute() method still works unchanged."""

    @pytest.mark.asyncio
    async def test_execute_unchanged(self, tmp_path: Path) -> None:
        executor = _make_executor(tmp_path, step_budget=10)

        async def two_steps_then_done(*_args, **_kwargs):
            yield {"output": [{"type": "computer_call"}]}
            yield {"output": [{"role": "assistant", "content": "Task complete."}]}

        with patch("agent.ComputerAgent") as mock_cls:
            mock_agent = MagicMock()
            mock_agent.run = two_steps_then_done
            mock_cls.return_value = mock_agent

            result = await executor.execute(_make_task(), _make_persona(), MagicMock())

        assert isinstance(result, TaskResult)
        assert result.steps_taken == 2
        assert result.success is True
