"""Tests for stuck intervention prompt during cycle run (Task 021).

Verifies the InterventionCallbacks protocol and dispatch logic:
- Attended mode prompts user when stuck
- "train" response triggers training session
- "skip" response skips source and logs stuck_skipped
- "abort" response aborts cycle gracefully
- Unattended mode (no callbacks) does not prompt
"""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.persona import Persona, Traits
from soren.models.session import TaskResult
from soren.models.task import Task
from soren.services.meta_agent import InterventionCallbacks, SorenMetaAgent


def _make_task(
    task_id: str = "task-1",
    source_name: str = "TestSource",
    task_type: str = "survey",
    source_url: str = "https://example.com",
) -> Task:
    return Task(
        id=task_id,
        dedup_key="test:1:persona",
        title="Test Task",
        description="Complete the survey",
        task_type=task_type,
        source_name=source_name,
        source_url=source_url,
        persona_id="p1",
        created_at="2025-01-01T00:00:00Z",
    )


def _make_persona() -> Persona:
    return Persona(id="p1", name="Test", traits=Traits(), enabled=True)


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


def _build_agent(
    *,
    should_offer_training: bool = True,
    task_result: TaskResult | None = None,
    intervention_callbacks: InterventionCallbacks | None = None,
    training_callbacks: Any = None,
    playbook_service: MagicMock | None = None,
) -> SorenMetaAgent:
    """Build a SorenMetaAgent with all dependencies mocked."""
    settings = MagicMock()
    settings.playbooks.write_back = True
    settings.scheduler.retry_backoff_minutes = 5
    settings.scheduler.stuck_auto_skip_timeout = 0.01  # Fast for tests

    db = MagicMock()
    llm = MagicMock()
    llm.get_telemetry.return_value = {}

    vm = MagicMock()
    vm.vm_name = "test-vm"
    vm.ensure_ready = AsyncMock()
    vm.connect_computer = AsyncMock(return_value=MagicMock())
    vm.reset_browser_state = AsyncMock()
    vm.notify_cycle_complete.return_value = False

    backlog = MagicMock()
    backlog.get_eligible_tasks = AsyncMock(return_value=[_make_task()])
    backlog.mark_in_progress = AsyncMock()
    backlog.mark_completed = AsyncMock()
    backlog.mark_failed = AsyncMock()
    backlog.get_summary = AsyncMock(return_value=MagicMock())
    backlog.list_tasks = AsyncMock(return_value=[])

    discovery = MagicMock()

    executor = MagicMock()
    result = task_result or TaskResult(success=False, error="Stuck: action loop detected")
    executor.execute_with_learning = AsyncMock(return_value=(result, should_offer_training))

    persona = _make_persona()
    persona_loader = MagicMock(return_value=[persona])

    if playbook_service is None:
        playbook_service = MagicMock()
        playbook_service.write_back = AsyncMock(return_value=MagicMock(dry_run=False, preview=None))
        playbook_service.load_site.return_value = None
        playbook_service.save_flow.return_value = MagicMock()
        playbook_service.update_confidence = MagicMock()
        playbook_service.update_host_confidence = MagicMock()

    agent = SorenMetaAgent(
        settings=settings,
        db=db,
        llm=llm,
        vm=vm,
        backlog=backlog,
        discovery=discovery,
        executor=executor,
        persona_loader=persona_loader,
        playbook_service=playbook_service,
        intervention_callbacks=intervention_callbacks,
        training_callbacks=training_callbacks,
    )
    # Mock internal services
    agent._memory_service = MagicMock()
    agent._memory_service.recall = AsyncMock(return_value=[])
    agent._memory_service.store = AsyncMock()
    agent._memory_service.prune = AsyncMock(return_value=0)
    agent._memory_service.clear_session = MagicMock()
    agent._scorer = MagicMock()
    agent._scorer.score.return_value = 1.0

    return agent


class TestInterventionPromptShownWhenStuck:
    """Intervention callback is invoked when should_offer_training=True and callbacks provided."""

    @pytest.mark.asyncio
    async def test_on_stuck_called_when_stuck_and_callbacks_provided(self) -> None:
        callbacks = _StubInterventionCallbacks(response="skip")
        agent = _build_agent(should_offer_training=True, intervention_callbacks=callbacks)

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
        agent = _build_agent(
            should_offer_training=True,
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
            # Verify correct source_name passed
            call_kwargs = mock_session_cls.call_args[1]
            assert call_kwargs["source_name"] == "TestSource"
            assert call_kwargs["flow_type"] == "execution"


class TestSkipResponseLogsStuckSkipped:
    """'skip' response skips source and logs stuck_skipped in report."""

    @pytest.mark.asyncio
    async def test_skip_response_logs_stuck_source(self) -> None:
        callbacks = _StubInterventionCallbacks(response="skip")
        agent = _build_agent(should_offer_training=True, intervention_callbacks=callbacks)

        report = await agent.run_cycle()

        assert report.stuck_detected is True
        assert len(report.stuck_sources) == 1
        stuck = report.stuck_sources[0]
        assert stuck["source_name"] == "TestSource"
        assert stuck["auto_skipped"] is False


class TestAbortResponseAbortsCycle:
    """'abort' response aborts cycle gracefully."""

    @pytest.mark.asyncio
    async def test_abort_response_returns_report_early(self) -> None:
        callbacks = _StubInterventionCallbacks(response="abort")
        agent = _build_agent(should_offer_training=True, intervention_callbacks=callbacks)

        report = await agent.run_cycle()

        assert report.stuck_detected is True
        assert report.completed_at != ""
        # Task result should still be present
        assert report.task_result is not None


class TestUnattendedModeDoesNotPrompt:
    """Unattended mode (no callbacks) does not invoke on_stuck."""

    @pytest.mark.asyncio
    async def test_no_callbacks_means_no_prompt(self) -> None:
        agent = _build_agent(should_offer_training=True, intervention_callbacks=None)

        report = await agent.run_cycle()

        # Should still detect stuck condition
        assert report.stuck_detected is True
        # But no intervention prompt was shown (unattended auto-skip handles it)


class TestNoInterventionWhenNotStuck:
    """Intervention callbacks NOT invoked when should_offer_training=False."""

    @pytest.mark.asyncio
    async def test_callbacks_not_called_when_not_stuck(self) -> None:
        callbacks = _StubInterventionCallbacks(response="skip")
        agent = _build_agent(
            should_offer_training=False,
            task_result=TaskResult(success=True, steps_taken=3),
            intervention_callbacks=callbacks,
        )

        await agent.run_cycle()

        assert len(callbacks.on_stuck_calls) == 0
