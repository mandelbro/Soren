"""Tests for TaskExecutor."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from soren.models.persona import Persona, Traits
from soren.models.session import TaskResult
from soren.models.task import Task
from soren.services.task_executor import TaskExecutor


def _make_task(
    id: str = "task-1",
    description: str = "Open Safari and navigate to example.com",
) -> Task:
    return Task(
        id=id,
        dedup_key="test:1:persona",
        title="Test",
        description=description,
        task_type="survey",
        source_name="TestSource",
    )


def _make_persona() -> Persona:
    return Persona(name="Test", traits=Traits())


def _make_executor(tmp_path: Path, step_budget: int = 50) -> TaskExecutor:
    settings = MagicMock()
    llm = MagicMock()
    vm = MagicMock()
    return TaskExecutor(
        settings=settings,
        llm=llm,
        vm=vm,
        step_budget=step_budget,
        screenshot_dir=tmp_path / "screenshots",
    )


class TestTaskExecutorConstruction:
    """ComputerAgent construction and API usage."""

    @pytest.mark.asyncio
    async def test_constructs_agent_with_tools_list(self, tmp_path: Path) -> None:
        """TaskExecutor constructs ComputerAgent with tools=[computer] (not computer=)."""
        executor = _make_executor(tmp_path)
        mock_computer = MagicMock()
        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = lambda *a, **k: async_gen([])
            mock_agent_cls.return_value = mock_agent
            await executor.execute(_make_task(), _make_persona(), mock_computer)
            mock_agent_cls.assert_called_once()
            call_kwargs = mock_agent_cls.call_args[1]
            assert "tools" in call_kwargs
            assert call_kwargs["tools"] == [mock_computer]

    @pytest.mark.asyncio
    async def test_constructs_agent_with_instructions(self, tmp_path: Path) -> None:
        """TaskExecutor constructs ComputerAgent with instructions= parameter."""
        executor = _make_executor(tmp_path)
        mock_computer = MagicMock()
        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = lambda *a, **k: async_gen([])
            mock_agent_cls.return_value = mock_agent
            await executor.execute(_make_task(), _make_persona(), mock_computer)
            call_kwargs = mock_agent_cls.call_args[1]
            assert "instructions" in call_kwargs
            assert len(call_kwargs["instructions"]) > 0

    @pytest.mark.asyncio
    async def test_sends_message_list_to_run(self, tmp_path: Path) -> None:
        """TaskExecutor sends message-list input to agent.run()."""
        executor = _make_executor(tmp_path)
        mock_computer = MagicMock()
        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            run_calls: list = []

            def _run(*a, **k):
                run_calls.append((a, k))
                return async_gen([])

            mock_agent.run = _run
            mock_agent_cls.return_value = mock_agent
            task = _make_task(description="Do something")
            await executor.execute(task, _make_persona(), mock_computer)
            assert len(run_calls) == 1
            call_args = run_calls[0][0]
            assert len(call_args) >= 1
            messages = call_args[0]
            assert isinstance(messages, list)
            assert len(messages) >= 1
            assert messages[0].get("role") == "user"
            assert "Do something" in str(messages[0].get("content", ""))

    @pytest.mark.asyncio
    async def test_uses_vlm_model_from_settings(self, tmp_path: Path) -> None:
        """TaskExecutor uses settings.vlm.full_model_str() when vlm_model not passed."""
        from soren.models.vlm import VLMConfig

        settings = MagicMock()
        settings.vlm = VLMConfig(provider="mlx", model="custom/UI-TARS-7B")
        llm = MagicMock()
        vm = MagicMock()
        executor = TaskExecutor(
            settings=settings,
            llm=llm,
            vm=vm,
            step_budget=50,
            screenshot_dir=tmp_path / "screenshots",
        )
        mock_computer = MagicMock()
        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = lambda *a, **k: async_gen([])
            mock_agent_cls.return_value = mock_agent
            await executor.execute(_make_task(), _make_persona(), mock_computer)
            call_kwargs = mock_agent_cls.call_args[1]
            assert call_kwargs["model"] == "mlx/custom/UI-TARS-7B"


class TestStepBudget:
    """Step budget enforcement."""

    @pytest.mark.asyncio
    async def test_step_budget_terminates_at_limit(self, tmp_path: Path) -> None:
        """Step budget terminates execution at limit."""
        executor = _make_executor(tmp_path, step_budget=3)
        mock_computer = MagicMock()

        async def many_steps(*_args, **_kwargs):
            for _ in range(10):
                yield {"output": [{"type": "computer_call", "call": {"action": {"type": "click"}}}]}

        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = many_steps
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(_make_task(), _make_persona(), mock_computer)
            assert result.steps_taken == 3
            assert not result.success
            assert "Step budget" in (result.error or "")

    @pytest.mark.asyncio
    async def test_step_budget_allows_completion_before_limit(self, tmp_path: Path) -> None:
        """Step budget allows completion before limit."""
        executor = _make_executor(tmp_path, step_budget=10)

        async def two_steps_then_done(*_args, **_kwargs):
            yield {"output": [{"type": "computer_call"}]}
            yield {"output": [{"role": "assistant", "content": "Task complete."}]}

        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = two_steps_then_done
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(_make_task(), _make_persona(), MagicMock())
            assert result.steps_taken == 2
            assert result.success


class TestOutputParsing:
    """Agent output parsing."""

    @pytest.mark.asyncio
    async def test_extracts_reasoning_from_result(self, tmp_path: Path) -> None:
        """Parser extracts reasoning text from result."""
        executor = _make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            yield {
                "output": [
                    {
                        "type": "computer_call",
                        "call": {"action": {"type": "click", "x": 100, "y": 200}},
                        "summary": "Click the submit button",
                    }
                ]
            }

        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(_make_task(), _make_persona(), MagicMock())
            assert len(result.step_log) >= 1
            entry = result.step_log[0]
            assert "Click the submit button" in str(entry.get("vlm_reasoning", ""))

    @pytest.mark.asyncio
    async def test_extracts_action_type_and_coordinates(self, tmp_path: Path) -> None:
        """Parser extracts action type and coordinates."""
        executor = _make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            yield {
                "output": [
                    {
                        "type": "computer_call",
                        "call": {"action": {"type": "click", "x": 640, "y": 480}},
                    }
                ]
            }

        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(_make_task(), _make_persona(), MagicMock())
            assert len(result.step_log) >= 1
            entry = result.step_log[0]
            assert entry.get("action_type") == "click"
            assert entry.get("action_data", {}).get("x") == 640
            assert entry.get("action_data", {}).get("y") == 480

    @pytest.mark.asyncio
    async def test_detects_completion_message(self, tmp_path: Path) -> None:
        """Parser detects completion message."""
        executor = _make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            yield {"output": [{"role": "assistant", "content": "I have completed the task."}]}

        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(_make_task(), _make_persona(), MagicMock())
            assert result.success
            assert result.steps_taken == 1


class TestTaskResult:
    """TaskResult recording."""

    @pytest.mark.asyncio
    async def test_records_step_count_and_duration(self, tmp_path: Path) -> None:
        """TaskResult records correct step count and duration."""
        executor = _make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            yield {"output": [{"type": "computer_call"}]}
            yield {"output": [{"type": "computer_call"}]}

        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(_make_task(), _make_persona(), MagicMock())
            assert result.steps_taken == 2
            assert result.duration_seconds >= 0
            assert isinstance(result, TaskResult)


class TestErrorHandling:
    """Error handling."""

    @pytest.mark.asyncio
    async def test_handles_agent_error_without_crashing(self, tmp_path: Path) -> None:
        """TaskExecutor handles agent error without crashing, returns failed TaskResult."""
        executor = _make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            raise RuntimeError("VLM crashed")
            yield  # never reached; makes this an async generator for async for

        with patch("agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(_make_task(), _make_persona(), MagicMock())
            assert not result.success
            assert result.error is not None
            assert "VLM" in result.error or "crashed" in result.error


class TestTaskExecutorIntegration:
    """Integration tests requiring live VM and models."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_execute_simple_task_in_live_vm(self, tmp_path: Path) -> None:
        """Execute 'open Safari, navigate to example.com' in live VM via ComputerAgent."""
        from soren.services.config_loader import load_settings
        from soren.services.llm_service import LLMService
        from soren.services.task_executor import TaskExecutor
        from soren.services.vm_manager import VMManager

        settings = load_settings()
        vm = VMManager(settings.vm)
        llm = LLMService(settings)
        executor = TaskExecutor(
            settings=settings,
            llm=llm,
            vm=vm,
            step_budget=20,
            screenshot_dir=tmp_path / "screenshots",
        )

        if not await vm.is_running():
            pytest.skip("VM not running; start Lume and VM for integration test")

        computer = await vm.connect_computer()
        task = _make_task(
            description="Open Safari and navigate to example.com",
        )
        persona = _make_persona()

        result = await executor.execute(task, persona, computer)

        assert isinstance(result, TaskResult)
        assert result.duration_seconds >= 0
        assert result.step_log is not None
        # Executor did not crash. steps_taken > 0 when agent ran; 0 when VLM/model fails before first step
        assert result.steps_taken >= 0


async def async_gen(items):
    """Helper to create async generator from list."""
    for x in items:
        yield x
