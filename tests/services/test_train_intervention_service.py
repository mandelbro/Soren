"""The meta-agent's stuck 'train' intervention passes the task's resolved service to TrainingSession."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.service import ServiceConfigError, ServiceNotFoundError
from soren.models.session import TaskResult
from tests.service_helpers import make_resolved_service
from tests.services.conftest import build_meta_agent


class _Train:
    async def on_stuck(self, source_name, stuck_reason, cycle_context):
        return "train"


def _agent():
    training_callbacks = MagicMock()
    training_callbacks.show_error = AsyncMock()
    return build_meta_agent(
        should_offer_training=True,
        task_result=TaskResult(success=False, error="Stuck: action loop detected"),
        intervention_callbacks=_Train(),
        training_callbacks=training_callbacks,
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

    @pytest.mark.parametrize(
        "error", [ServiceConfigError("bad file"), ServiceNotFoundError("missing config/services/x.yaml")]
    )
    async def test_config_error_skips_training_and_tells_the_trainer(self, error: ServiceConfigError) -> None:
        """ADR-0002: a broken service config fails, never falls back (a wrong crop causes misclicks)."""
        agent = _agent()
        agent._executor.resolve_service_for = MagicMock(side_effect=error)
        session_cls = await _run(agent)
        session_cls.assert_not_called()
        agent._training_callbacks.show_error.assert_awaited_once()
        assert str(error) in agent._training_callbacks.show_error.await_args.args[0]

    async def test_task_without_service_file_trains_without_service(self) -> None:
        """A legacy source with no service file is not an error: train with the env-only crop as before."""
        agent = _agent()  # resolve_service_for returns None
        session_cls = await _run(agent)
        assert session_cls.call_args.kwargs["service"] is None
