"""Tests for stuck intervention prompt during cycle run.

Verifies the InterventionCallbacks protocol and dispatch logic:
- Attended mode prompts user when stuck
- "train" response triggers training session
- "skip" response skips source and logs stuck_skipped
- "abort" response aborts cycle gracefully
- Unattended mode (no callbacks) does not prompt
- CLIInterventionCallbacks with mocked Rich prompt
"""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.session import TaskResult
from tests.services.conftest import build_meta_agent


class _StubInterventionCallbacks:
    """Test stub implementing InterventionCallbacks protocol."""

    def __init__(self, response: Literal["train", "skip", "abort"] = "skip") -> None:
        self._response = response
        self.on_stuck_calls: list[dict[str, Any]] = []

    async def on_stuck(
        self,
        source_name: str,
        stuck_reason: str,
        cycle_context: dict[str, Any],
    ) -> Literal["train", "skip", "abort"]:
        self.on_stuck_calls.append(
            {"source_name": source_name, "stuck_reason": stuck_reason, "cycle_context": cycle_context}
        )
        return self._response


class TestInterventionPromptShownWhenStuck:
    """Intervention callback is invoked when should_offer_training=True and callbacks provided."""

    @pytest.mark.asyncio
    async def test_on_stuck_called_when_stuck_and_callbacks_provided(self) -> None:
        callbacks = _StubInterventionCallbacks(response="skip")
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: action loop detected"),
            intervention_callbacks=callbacks,
        )

        await agent.run_cycle()

        assert len(callbacks.on_stuck_calls) == 1
        call = callbacks.on_stuck_calls[0]
        assert call["source_name"] == "TestSource"
        assert "stuck" in call["stuck_reason"].lower() or len(call["stuck_reason"]) > 0


class TestTrainResponseTriggersTraining:
    """'train' response triggers training session invocation."""

    @pytest.mark.asyncio
    async def test_train_response_invokes_training_session(self) -> None:
        callbacks = _StubInterventionCallbacks(response="train")
        mock_training_callbacks = MagicMock()
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: action loop detected"),
            intervention_callbacks=callbacks,
            training_callbacks=mock_training_callbacks,
        )

        with patch("soren.services.training_session.TrainingSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.run = AsyncMock(return_value=None)
            mock_session_cls.return_value = mock_session

            await agent.run_cycle()

            mock_session_cls.assert_called_once()
            mock_session.run.assert_called_once()
            call_kwargs = mock_session_cls.call_args[1]
            assert call_kwargs["source_name"] == "TestSource"
            assert call_kwargs["flow_type"] == "execution"


class TestSkipResponseLogsStuckSkipped:
    """'skip' response skips source and logs stuck_skipped in report."""

    @pytest.mark.asyncio
    async def test_skip_response_logs_stuck_source(self) -> None:
        callbacks = _StubInterventionCallbacks(response="skip")
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: action loop detected"),
            intervention_callbacks=callbacks,
        )

        report = await agent.run_cycle()

        assert report.stuck_detected is True
        assert len(report.stuck_sources) == 1
        stuck = report.stuck_sources[0]
        assert stuck["source_name"] == "TestSource"
        assert stuck["auto_skipped"] is False


class TestAbortResponseAbortsCycle:
    """'abort' response aborts cycle gracefully and logs stuck source."""

    @pytest.mark.asyncio
    async def test_abort_response_returns_report_early(self) -> None:
        callbacks = _StubInterventionCallbacks(response="abort")
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: action loop detected"),
            intervention_callbacks=callbacks,
        )

        report = await agent.run_cycle()

        assert report.stuck_detected is True
        assert report.completed_at != ""
        assert report.task_result is not None
        # Abort should still log the stuck source for report consistency
        assert len(report.stuck_sources) == 1
        assert report.stuck_sources[0]["source_name"] == "TestSource"


class TestTrainResponseLogsStuckSource:
    """'train' response logs stuck source in report for consistency."""

    @pytest.mark.asyncio
    async def test_train_response_logs_stuck_source(self) -> None:
        callbacks = _StubInterventionCallbacks(response="train")
        mock_training_callbacks = MagicMock()
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: action loop detected"),
            intervention_callbacks=callbacks,
            training_callbacks=mock_training_callbacks,
        )

        with patch("soren.services.training_session.TrainingSession") as mock_cls:
            mock_cls.return_value = MagicMock(run=AsyncMock(return_value=None))
            report = await agent.run_cycle()

        assert report.stuck_detected is True
        assert len(report.stuck_sources) == 1
        assert report.stuck_sources[0]["source_name"] == "TestSource"


class TestUnattendedModeDoesNotPrompt:
    """Unattended mode (no callbacks) does not invoke on_stuck."""

    @pytest.mark.asyncio
    async def test_no_callbacks_means_no_prompt(self) -> None:
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: action loop detected"),
            intervention_callbacks=None,
        )

        with patch("soren.services.stuck_intervention.asyncio.sleep", new_callable=AsyncMock):
            report = await agent.run_cycle()

        assert report.stuck_detected is True


class TestNoInterventionWhenNotStuck:
    """Intervention callbacks NOT invoked when should_offer_training=False."""

    @pytest.mark.asyncio
    async def test_callbacks_not_called_when_not_stuck(self) -> None:
        callbacks = _StubInterventionCallbacks(response="skip")
        agent = build_meta_agent(
            should_offer_training=False,
            task_result=TaskResult(success=True, steps_taken=3),
            intervention_callbacks=callbacks,
        )

        await agent.run_cycle()

        assert len(callbacks.on_stuck_calls) == 0


class TestCLIInterventionCallbacks:
    """CLIInterventionCallbacks dispatches Rich prompt and returns correct action."""

    @pytest.mark.asyncio
    async def test_skip_choice_returns_skip(self) -> None:
        from soren.cli.cycle import CLIInterventionCallbacks

        callbacks = CLIInterventionCallbacks()
        with patch("soren.cli.cycle.Prompt.ask", return_value="s"):
            result = await callbacks.on_stuck("SomeSource", "Action loop", {"steps_taken": 12})
        assert result == "skip"

    @pytest.mark.asyncio
    async def test_train_choice_returns_train(self) -> None:
        from soren.cli.cycle import CLIInterventionCallbacks

        callbacks = CLIInterventionCallbacks()
        with patch("soren.cli.cycle.Prompt.ask", return_value="t"):
            result = await callbacks.on_stuck("SomeSource", "No progress", {})
        assert result == "train"

    @pytest.mark.asyncio
    async def test_abort_choice_returns_abort(self) -> None:
        from soren.cli.cycle import CLIInterventionCallbacks

        callbacks = CLIInterventionCallbacks()
        with patch("soren.cli.cycle.Prompt.ask", return_value="a"):
            result = await callbacks.on_stuck("SomeSource", "Timeout", {})
        assert result == "abort"
