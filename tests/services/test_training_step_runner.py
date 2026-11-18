"""TrainingStepRunner unit tests (one training step through a fresh CUA agent)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.services.training_step_runner import TrainingStepRunner
from tests.services.training_helpers import (
    PATCH_APPLY_ALL,
    PATCH_CUA_AGENT,
    make_cua_completion,
    make_cua_plan,
    mock_llm,
)

pytestmark = pytest.mark.usefixtures("fake_cua_sdk", "training_virtual_clock")


def _agent(yields: list[dict]) -> MagicMock:
    agent = MagicMock()

    async def run(messages):
        for item in yields:
            yield item

    agent.run = run
    return agent


class TestLastClick:
    async def test_last_click_recorded_then_reset_by_next_step(self) -> None:
        runner = TrainingStepRunner(mock_llm(), live_service=lambda: None)
        computer = MagicMock()
        computer.interface.screenshot_full = AsyncMock(return_value=b"png")
        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT) as agent_cls:
            agent_cls.return_value = _agent([make_cua_plan(), make_cua_completion()])
            await runner.run_step("Click login", computer)
            assert runner.last_click_xy == (100, 200)
            agent_cls.return_value = _agent([make_cua_completion()])  # no click this time
            await runner.run_step("Wait", computer)
        assert runner.last_click_xy is None  # a retry must never cite an earlier attempt's click
