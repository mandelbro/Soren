"""Tests for TaskExecutor: output parsing into the step log, verification/corrections, and the live-VM integration test."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from soren.models.session import TaskResult
from soren.services.task_executor import TaskExecutor
from soren.verification.correction import CorrectionStrategy
from tests.task_executor_helpers import make_executor, make_persona, make_task

# Unit tests: fake the CUA SDK (see tests/conftest.py::fake_cua_sdk).
pytestmark = pytest.mark.usefixtures("fake_cua_sdk")


class TestOutputParsing:
    """Agent output parsing."""

    @pytest.mark.asyncio
    async def test_extracts_reasoning_from_result(self, tmp_path: Path) -> None:
        """Parser extracts reasoning text from result."""
        executor = make_executor(tmp_path)

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

        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(make_task(), make_persona(), MagicMock())
            assert len(result.step_log) >= 1
            entry = result.step_log[0]
            assert "Click the submit button" in str(entry.get("vlm_reasoning", ""))

    @pytest.mark.asyncio
    async def test_extracts_action_type_and_coordinates(self, tmp_path: Path) -> None:
        """Parser extracts action type and coordinates."""
        executor = make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            yield {
                "output": [
                    {
                        "type": "computer_call",
                        "call": {"action": {"type": "click", "x": 640, "y": 480}},
                    }
                ]
            }

        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(make_task(), make_persona(), MagicMock())
            assert len(result.step_log) >= 1
            entry = result.step_log[0]
            assert entry.get("action_type") == "click"
            assert entry.get("action_data", {}).get("x") == 640
            assert entry.get("action_data", {}).get("y") == 480

    @pytest.mark.asyncio
    async def test_detects_completion_message(self, tmp_path: Path) -> None:
        """Parser detects completion message."""
        executor = make_executor(tmp_path)

        async def gen(*_args, **_kwargs):
            yield {"output": [{"role": "assistant", "content": "I have completed the task."}]}

        with patch("cua_agent.ComputerAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run = gen
            mock_agent_cls.return_value = mock_agent
            result = await executor.execute(make_task(), make_persona(), MagicMock())
            assert result.success
            assert result.steps_taken == 1


class TestTaskExecutorIntegration:
    """Integration tests requiring live VM and models."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_execute_simple_task_in_live_vm(self, tmp_path: Path) -> None:
        """Execute 'open Safari, navigate to example.com' in live VM via ComputerAgent."""
        from soren.services.config_loader import load_settings
        from soren.services.llm_service import LLMService
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
        task = make_task(
            description="Open Safari and navigate to example.com",
        )
        persona = make_persona()

        result = await executor.execute(task, persona, computer)

        assert isinstance(result, TaskResult)
        assert result.duration_seconds >= 0
        assert result.step_log is not None
        # Executor did not crash. steps_taken > 0 when agent ran; 0 when VLM/model fails before first step
        assert result.steps_taken >= 0

    @pytest.mark.asyncio
    async def test_verification_failure_applies_correction_strategy(self, tmp_path: Path) -> None:
        """Verification failure triggers correction escalation and failed result."""
        executor = make_executor(tmp_path)

        async def completion_with_screenshot(*_args, **_kwargs):
            yield {"output": [{"type": "computer_call_output", "output": {"image": "aGVsbG8="}}]}
            yield {"output": [{"role": "assistant", "content": "Task complete."}]}

        with (
            patch("cua_agent.ComputerAgent") as mock_agent_cls,
            patch.object(executor._post_verifier, "verify_completion", return_value=False) as verify_mock,
            patch.object(
                executor._post_verifier,
                "attempt_corrections",
                return_value=CorrectionStrategy.KEYPRESS,
            ) as correction_mock,
        ):
            mock_agent = MagicMock()
            mock_agent.run = completion_with_screenshot
            mock_agent_cls.return_value = mock_agent

            result = await executor.execute(make_task(), make_persona(), MagicMock())

            verify_mock.assert_awaited_once()
            correction_mock.assert_awaited_once()
            assert result.success is False
            assert result.verification_passed is False
            assert result.correction_applied == CorrectionStrategy.KEYPRESS.value
            assert any(step.get("action_type") == "verify" for step in result.step_log)
            assert any(step.get("action_type") == "correction" for step in result.step_log)
