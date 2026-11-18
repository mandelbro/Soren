"""Characterization tests for the meta-agent's discovery loop (run_cycle step 3 and refresh_tasks).

Written before merging the two duplicated loops into one helper, to pin the
existing behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from soren.models.persona import Persona
from soren.models.session import InsertResult
from soren.models.task_source import TaskSource
from tests.services.conftest import build_meta_agent


def _personas() -> list[Persona]:
    return [
        Persona(
            name="A",
            id="a",
            task_sources=[
                TaskSource(name="ok1", type="survey_platform"),
                TaskSource(name="disabled_src", type="survey_platform", enabled=False),
                TaskSource(name="no_adapter", type="not_registered"),
                TaskSource(name="boom", type="survey_platform"),
                TaskSource(name="empty", type="survey_platform"),
            ],
        ),
        Persona(name="B", id="b", enabled=False, task_sources=[TaskSource(name="off_persona", type="email")]),
        Persona(name="C", id="c", task_sources=[TaskSource(name="ok2", type="email")]),
    ]


def _agent(personas: list[Persona]):
    agent = build_meta_agent()
    agent._persona_loader = MagicMock(return_value=personas)
    agent._backlog.get_eligible_tasks = AsyncMock(return_value=[])
    agent._backlog.insert_discovered = AsyncMock(return_value=InsertResult(inserted=2, skipped=1))

    async def discover(persona, source, computer):
        if source.name == "boom":
            raise RuntimeError("discovery crashed")
        if source.name == "empty":
            return []
        return [MagicMock()]

    agent._discovery.discover = AsyncMock(side_effect=discover)
    return agent


def _discovered_sources(agent) -> list[str]:
    return [c.args[1].name for c in agent._discovery.discover.await_args_list]


@pytest.mark.parametrize("mode", ["run_cycle", "refresh_tasks"])
class TestDiscoveryLoop:
    async def test_skips_disabled_and_unadapted_and_survives_errors(self, mode: str) -> None:
        agent = _agent(_personas())
        await getattr(agent, mode)()
        assert _discovered_sources(agent) == ["ok1", "boom", "empty", "ok2"]

    async def test_counts(self, mode: str) -> None:
        agent = _agent(_personas())
        if mode == "run_cycle":
            report = await agent.run_cycle()
            summary = report.discovery_summary
            assert report.discovery_ran is True
            counts = (summary["total_inserted"], summary["total_skipped"], summary["source_count"])
        else:
            s = await agent.refresh_tasks()
            counts = (s.total_inserted, s.total_skipped, s.source_count)
        assert counts == (4, 2, 2)  # ok1 + ok2 each insert 2 / skip 1


class TestPersonasProcessedQuirk:
    """Existing behavior: run_cycle counts enabled personas, refresh_tasks counts all loaded personas."""

    async def test_run_cycle_counts_enabled_personas(self) -> None:
        report = await _agent(_personas()).run_cycle()
        assert report.discovery_summary["personas_processed"] == 2

    async def test_refresh_tasks_counts_all_personas(self) -> None:
        summary = await _agent(_personas()).refresh_tasks()
        assert summary.personas_processed == 3

    async def test_refresh_tasks_persona_filter(self) -> None:
        agent = _agent(_personas())
        summary = await agent.refresh_tasks(persona_filter="c")
        assert _discovered_sources(agent) == ["ok2"]
        assert summary.personas_processed == 1
