"""Tests for TaskExecutor: agent construction, step budget, results, and error handling."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from soren.models.session import TaskResult
from soren.services.task_executor import TaskExecutor
from tests.task_executor_helpers import async_gen, make_executor, make_persona, make_persona_with_source, make_task

# Unit tests: fake the CUA SDK (see tests/conftest.py::fake_cua_sdk).
pytestmark = pytest.mark.usefixtures("fake_cua_sdk")


class TestTaskExecutorConstruction:
    """ComputerAgent construction and API usage."""

    @pytest.mark.asyncio
    async def test_constructs_agent_with_tools_list(self, tmp_path: Path) -> None:
        """TaskExecutor constructs ComputerAgent with tools=[computer] (not computer=)."""
        executor = make_executor(tmp_path)
        mock_computer = MagicMock()
        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = lambda *a, **k: async_gen([])
            mock_agent_cls.return_value = mock_agent
            await executor.execute(make_task(), make_persona(), mock_computer)
            mock_agent_cls.assert_called_once()
            call_kwargs = mock_agent_cls.call_args[1]
            assert "tools" in call_kwargs
            assert call_kwargs["tools"] == [mock_computer]

    @pytest.mark.asyncio
    async def test_constructs_agent_with_instructions(self, tmp_path: Path) -> None:
        """TaskExecutor constructs ComputerAgent with instructions= parameter."""
        executor = make_executor(tmp_path)
        mock_computer = MagicMock()
        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = lambda *a, **k: async_gen([])
            mock_agent_cls.return_value = mock_agent
            await executor.execute(make_task(), make_persona(), mock_computer)
            call_kwargs = mock_agent_cls.call_args[1]
            assert "instructions" in call_kwargs
            assert len(call_kwargs["instructions"]) > 0

    @pytest.mark.asyncio
    async def test_sends_message_list_to_run(self, tmp_path: Path) -> None:
        """TaskExecutor sends message-list input to agent.run()."""
        executor = make_executor(tmp_path)
        mock_computer = MagicMock()
        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            run_calls: list = []

            def _run(*a, **k):
                run_calls.append((a, k))
                return async_gen([])

            mock_agent.run = _run
            mock_agent_cls.return_value = mock_agent
            task = make_task(description="Do something")
            await executor.execute(task, make_persona(), mock_computer)
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
        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = lambda *a, **k: async_gen([])
            mock_agent_cls.return_value = mock_agent
            await executor.execute(make_task(), make_persona(), mock_computer)
            call_kwargs = mock_agent_cls.call_args[1]
            assert call_kwargs["model"] == "mlx/custom/UI-TARS-7B"

    @pytest.mark.asyncio
    async def test_includes_credential_handoff_in_instructions(self, tmp_path: Path) -> None:
        """TaskExecutor appends source credential details into execution instructions."""
        settings = MagicMock()
        llm = MagicMock()
        vm = MagicMock()
        store = MagicMock()
        store.get.return_value = MagicMock(username="user@example.com", password="secret123")
        executor = TaskExecutor(
            settings=settings,
            llm=llm,
            vm=vm,
            step_budget=50,
            screenshot_dir=tmp_path / "screenshots",
            credential_store=store,
        )
        mock_computer = MagicMock()
        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = lambda *a, **k: async_gen([])
            mock_agent_cls.return_value = mock_agent
            await executor.execute(make_task(), make_persona_with_source("acmepanel"), mock_computer)
            instructions = mock_agent_cls.call_args[1]["instructions"]
            assert "credential_ref: acmepanel" in instructions
            assert "username: user@example.com" in instructions
            assert "password: secret123" in instructions


class TestStepBudget:
    """Step budget enforcement."""

    @pytest.mark.asyncio
    async def test_step_budget_terminates_at_limit(self, tmp_path: Path) -> None:
        """Step budget terminates execution at limit."""
        executor = make_executor(tmp_path, step_budget=3)
        mock_computer = MagicMock()

        async def many_steps(*_args, **_kwargs):
            for _ in range(10):
                yield {"output": [{"type": "computer_call", "call": {"action": {"type": "click"}}}]}

        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = many_steps
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(make_task(), make_persona(), mock_computer)
            assert result.steps_taken == 3
            assert not result.success
            assert "Step budget" in (result.error or "")

    @pytest.mark.asyncio
    async def test_step_budget_allows_completion_before_limit(self, tmp_path: Path) -> None:
        """Step budget allows completion before limit."""
        executor = make_executor(tmp_path, step_budget=10)

        async def two_steps_then_done(*_args, **_kwargs):
            yield {"output": [{"type": "computer_call"}]}
            yield {"output": [{"role": "assistant", "content": "Task complete."}]}

        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = two_steps_then_done
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(make_task(), make_persona(), MagicMock())
            assert result.steps_taken == 2
            assert result.success


class TestTaskResult:
    """TaskResult recording."""

    @pytest.mark.asyncio
    async def test_records_step_count_and_duration(self, tmp_path: Path) -> None:
        """TaskResult records correct step count and duration."""
        executor = make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            yield {"output": [{"type": "computer_call"}]}
            yield {"output": [{"type": "computer_call"}]}

        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(make_task(), make_persona(), MagicMock())
            assert result.steps_taken == 2
            assert result.duration_seconds >= 0
            assert isinstance(result, TaskResult)


class TestErrorHandling:
    """Error handling."""

    @pytest.mark.asyncio
    async def test_handles_agent_error_without_crashing(self, tmp_path: Path) -> None:
        """TaskExecutor handles agent error without crashing, returns failed TaskResult."""
        executor = make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            raise RuntimeError("VLM crashed")
            yield  # never reached; makes this an async generator for async for

        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(make_task(), make_persona(), MagicMock())
            assert not result.success
            assert result.error is not None
            assert "VLM" in result.error or "crashed" in result.error
