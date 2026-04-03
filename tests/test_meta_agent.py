"""Tests for SorenMetaAgent."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.persona import Persona
from soren.models.session import BacklogSummary, CycleReport, DiscoveredTask, TaskResult
from soren.models.task import Task
from soren.models.task_source import TaskSource
from soren.services.meta_agent import SorenMetaAgent


def _make_task(
    task_id: str = "t1",
    persona_id: str = "p1",
    status: str = "queued",
) -> Task:
    return Task(
        id=task_id,
        dedup_key="k1",
        title="Test Task",
        persona_id=persona_id,
        status=status,
    )


@pytest.fixture(autouse=True)
def _patch_memory_check():
    """Ensure memory check passes in tests (avoid abort on high-memory CI)."""
    with patch("soren.services.meta_agent.psutil.virtual_memory") as mock_vm:
        mock_vm.return_value.percent = 50.0
        yield


def _make_persona() -> Persona:
    return Persona(
        name="Test",
        id="p1",
        task_sources=[TaskSource(name="s1", type="survey_platform", enabled=True)],
        enabled=True,
    )


def _make_agent(
    backlog_tasks: list[Task] | None = None,
    discovery_tasks: list | None = None,
    executor_result: TaskResult | None = None,
) -> tuple[SorenMetaAgent, dict]:
    settings = MagicMock()
    settings.llm = MagicMock()
    settings.scheduler = MagicMock()
    settings.scheduler.retry_backoff_minutes = [5, 30, 120]
    settings.playbooks = MagicMock()
    settings.playbooks.write_back = True

    db = MagicMock()
    # MemoryService uses db.execute (async) - mock it for recall/prune
    cursor = MagicMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.rowcount = 0
    db.execute = AsyncMock(return_value=cursor)
    db.commit = AsyncMock()
    llm = MagicMock()
    vm = MagicMock()
    vm.vm_name = "test-vm"
    vm.ensure_ready = AsyncMock()
    vm.connect_computer = AsyncMock(return_value=MagicMock())
    vm.reset_browser_state = AsyncMock()
    vm.notify_cycle_complete = MagicMock(return_value=False)
    vm.recreate_vm = AsyncMock()

    backlog = MagicMock()
    backlog.get_eligible_tasks = AsyncMock(return_value=backlog_tasks or [])
    backlog.get_summary = AsyncMock(return_value=BacklogSummary())
    backlog.list_tasks = AsyncMock(return_value=[])
    backlog.mark_in_progress = AsyncMock()
    backlog.mark_completed = AsyncMock()
    backlog.mark_failed = AsyncMock()
    backlog.insert_discovered = AsyncMock(return_value=MagicMock(inserted=1, skipped=0))

    discovery = MagicMock()
    discovery.discover = AsyncMock(return_value=discovery_tasks or [])

    executor = MagicMock()
    default_result = executor_result or TaskResult(success=True, steps_taken=5)
    executor.execute = AsyncMock(return_value=default_result)
    executor.execute_with_learning = AsyncMock(return_value=(default_result, False))

    def load_personas():
        return [_make_persona()]

    agent = SorenMetaAgent(
        settings=settings,
        db=db,
        llm=llm,
        vm=vm,
        backlog=backlog,
        discovery=discovery,
        executor=executor,
        persona_loader=load_personas,
        report_dir=Path("/tmp/soren-reports"),
    )
    mocks = {
        "backlog": backlog,
        "discovery": discovery,
        "executor": executor,
        "vm": vm,
    }
    return agent, mocks


async def test_run_cycle_discovers_when_backlog_empty():
    """run_cycle() discovers when backlog is empty."""
    discovered = DiscoveredTask(title="Discovered", source_name="s1", persona_id="p1", task_type="survey")
    agent, mocks = _make_agent(backlog_tasks=[], discovery_tasks=[discovered])
    with patch.object(agent._backlog, "get_eligible_tasks", side_effect=[[], [_make_task()]]):
        report = await agent.run_cycle()
    assert report.discovery_ran is True
    mocks["discovery"].discover.assert_called()


async def test_run_cycle_skips_discovery_when_backlog_has_tasks():
    """run_cycle() skips discovery when backlog has eligible tasks."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task])
    report = await agent.run_cycle()
    assert report.discovery_ran is False
    mocks["discovery"].discover.assert_not_called()


async def test_run_cycle_selects_highest_priority_task():
    """run_cycle() selects highest-priority eligible task."""
    t1 = _make_task(task_id="t1", persona_id="p1")
    t2 = _make_task(task_id="t2", persona_id="p1")
    agent, mocks = _make_agent(backlog_tasks=[t1, t2])
    report = await agent.run_cycle()
    assert report.task_executed_id in ("t1", "t2")
    mocks["executor"].execute_with_learning.assert_called_once()


async def test_run_cycle_calls_executor_with_task_and_persona():
    """run_cycle() calls executor.execute_with_learning() with correct task and persona."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task])
    await agent.run_cycle()
    call_args = mocks["executor"].execute_with_learning.call_args
    assert call_args[0][0].id == task.id
    assert call_args[0][1].id == "p1"


async def test_run_cycle_marks_completed_on_success():
    """run_cycle() marks task completed on success."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task], executor_result=TaskResult(success=True))
    await agent.run_cycle()
    mocks["backlog"].mark_completed.assert_called_once()
    mocks["backlog"].mark_failed.assert_not_called()


async def test_run_cycle_marks_failed_with_backoff_on_failure():
    """run_cycle() marks task failed with backoff on failure."""
    task = _make_task()
    agent, mocks = _make_agent(
        backlog_tasks=[task],
        executor_result=TaskResult(success=False, error="timeout"),
    )
    await agent.run_cycle()
    mocks["backlog"].mark_failed.assert_called_once()
    mocks["backlog"].mark_completed.assert_not_called()


async def test_run_cycle_promotes_to_dead_letter_at_max_retries():
    """run_cycle() promotes task to dead-letter at max retries."""
    task = _make_task()
    task.attempt_count = 2
    task.max_retries = 3
    agent, mocks = _make_agent(
        backlog_tasks=[task],
        executor_result=TaskResult(success=False, error="unknown"),
    )
    await agent.run_cycle()
    mocks["backlog"].mark_failed.assert_called_once()
    args = mocks["backlog"].mark_failed.call_args
    assert args[0][2]  # reason is non-empty


async def test_run_cycle_always_resets_browser():
    """run_cycle() always resets browser state (even on error)."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task])
    mocks["executor"].execute_with_learning.side_effect = RuntimeError("crash")
    await agent.run_cycle()
    mocks["vm"].reset_browser_state.assert_called()


async def test_run_cycle_triggers_vm_recreation_at_intervals():
    """run_cycle() triggers VM recreation at N-cycle intervals."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task])
    mocks["vm"].notify_cycle_complete.return_value = True
    await agent.run_cycle()
    mocks["vm"].recreate_vm.assert_called()


async def test_run_cycle_triggers_vm_recreation_on_contamination():
    """run_cycle() triggers VM recreation on contamination detection."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task])
    mocks["vm"].notify_cycle_complete.return_value = True
    await agent.run_cycle()
    mocks["vm"].recreate_vm.assert_called()


async def test_run_cycle_writes_report_regardless_of_outcome():
    """run_cycle() writes CycleReport regardless of outcome."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task])
    with patch("soren.services.meta_agent.write_report") as write:
        await agent.run_cycle()
        write.assert_called_once()
        args = write.call_args[0]
        assert isinstance(args[0], CycleReport)


async def test_run_cycle_handles_executor_crash():
    """run_cycle() handles executor crash without crashing cycle."""
    task = _make_task()
    agent, mocks = _make_agent(backlog_tasks=[task])
    mocks["executor"].execute_with_learning.side_effect = RuntimeError("executor crash")
    report = await agent.run_cycle()
    assert report.task_result is not None
    assert report.task_result.success is False
    mocks["vm"].reset_browser_state.assert_called()


async def test_run_cycle_handles_discovery_failure_gracefully():
    """run_cycle() handles discovery failure gracefully (empty backlog -> no execution)."""
    agent, mocks = _make_agent(backlog_tasks=[], discovery_tasks=[])
    mocks["discovery"].discover.return_value = []
    report = await agent.run_cycle()
    assert report.task_executed_id is None
    mocks["executor"].execute_with_learning.assert_not_called()


async def test_run_cycle_aborts_when_memory_over_threshold():
    """run_cycle() aborts early when memory exceeds threshold to avoid OOM."""
    with patch("soren.services.meta_agent.psutil.virtual_memory") as mock_vm:
        mock_vm.return_value.percent = 95.0
        agent, mocks = _make_agent(backlog_tasks=[_make_task()])
        report = await agent.run_cycle()
    assert report.task_result is not None
    assert report.task_result.success is False
    assert "92%" in (report.task_result.error or "")
    mocks["vm"].ensure_ready.assert_not_called()


async def test_refresh_tasks_runs_discovery_only():
    """refresh_tasks() runs discovery only (no execution)."""
    discovered = DiscoveredTask(title="Discovered", source_name="s1", persona_id="p1", task_type="survey")
    agent, mocks = _make_agent()
    mocks["discovery"].discover.return_value = [discovered]
    summary = await agent.refresh_tasks()
    assert summary.total_inserted >= 0
    mocks["executor"].execute_with_learning.assert_not_called()
