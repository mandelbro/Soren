"""The meta-agent's stuck 'train' intervention passes the task's resolved service to TrainingSession."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from soren.models.service import ServiceConfigError
from soren.models.session import TaskResult
from tests.service_helpers import make_resolved_service
from tests.services.conftest import build_meta_agent


class _Train:
    async def on_stuck(self, source_name, stuck_reason, cycle_context):
        return "train"


def _agent():
    return build_meta_agent(
        should_offer_training=True,
        task_result=TaskResult(success=False, error="Stuck: action loop detected"),
        intervention_callbacks=_Train(),
        training_callbacks=MagicMock(),
    )


async def _run(agent) -> MagicMock:
    with patch("soren.services.training_session.TrainingSession") as session_cls:
        session_cls.return_value.run = AsyncMock(return_value=None)
        await agent.run_cycle()
    return session_cls


class TestTrainInterventionService:
    async def test_resolved_service_passed(self, tmp_path: Path) -> None:
        agent = _agent()
        svc = make_resolved_service(tmp_path, "TestSource", config={"chrome_height_px": 100})
        agent._executor.resolve_service_for = MagicMock(return_value=svc)
        session_cls = await _run(agent)
        assert session_cls.call_args.kwargs["service"] is svc
        task_arg, persona_arg = agent._executor.resolve_service_for.call_args.args
        assert task_arg.source_name == "TestSource" and persona_arg.id == "p1"

    async def test_config_error_still_trains_without_service(self) -> None:
        agent = _agent()
        agent._executor.resolve_service_for = MagicMock(side_effect=ServiceConfigError("bad file"))
        session_cls = await _run(agent)
        session_cls.assert_called_once()
        assert session_cls.call_args.kwargs["service"] is None

    async def test_non_service_result_ignored(self) -> None:
        """A mocked/foreign executor returning something else must not reach TrainingSession."""
        agent = _agent()  # executor is a MagicMock: resolve_service_for returns a MagicMock
        session_cls = await _run(agent)
        assert session_cls.call_args.kwargs["service"] is None
