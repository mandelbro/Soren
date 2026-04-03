"""Shared test fixtures and factories for service tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from soren.models.persona import Persona, Traits
from soren.models.reset import SessionPrepResult
from soren.models.session import TaskResult
from soren.models.task import Task
from soren.services.meta_agent import SorenMetaAgent


def make_task(
    task_id: str = "task-1",
    source_name: str = "TestSource",
    task_type: str = "survey",
    source_url: str = "https://example.com",
) -> Task:
    """Create a Task for testing with sensible defaults."""
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


def make_persona() -> Persona:
    """Create a Persona for testing with sensible defaults."""
    return Persona(id="p1", name="Test", traits=Traits(), enabled=True)


def build_meta_agent(
    *,
    task_result: TaskResult | None = None,
    should_offer_training: bool = False,
    playbook_service: MagicMock | None = None,
    intervention_callbacks: object | None = None,
    training_callbacks: object | None = None,
    stuck_auto_skip_timeout: float = 0.01,
    report_dir: object | None = None,
) -> SorenMetaAgent:
    """Build a SorenMetaAgent with all dependencies mocked.

    Shared factory to avoid Rule-of-Three duplication across service tests.
    """
    settings = MagicMock()
    settings.playbooks.write_back = True
    settings.scheduler.retry_backoff_minutes = 5
    settings.scheduler.stuck_auto_skip_timeout = stuck_auto_skip_timeout

    db = MagicMock()
    llm = MagicMock()
    llm.get_telemetry.return_value = {}

    vm = MagicMock()
    vm.vm_name = "test-vm"
    vm.ensure_ready = AsyncMock()
    vm.connect_computer = AsyncMock(return_value=MagicMock())
    vm.prepare_session_environment = AsyncMock(
        return_value=SessionPrepResult(
            success=True,
            duration_seconds=0.1,
            attempted_new_space=True,
            frontmost_app="Safari",
            closed_apps=[],
            remaining_disallowed_apps=[],
            warnings=[],
        )
    )
    vm.reset_browser_state = AsyncMock()
    vm.notify_cycle_complete.return_value = False

    backlog = MagicMock()
    backlog.get_eligible_tasks = AsyncMock(return_value=[make_task()])
    backlog.mark_in_progress = AsyncMock()
    backlog.mark_completed = AsyncMock()
    backlog.mark_failed = AsyncMock()
    backlog.get_summary = AsyncMock(return_value=MagicMock())
    backlog.list_tasks = AsyncMock(return_value=[])

    discovery = MagicMock()

    executor = MagicMock()
    result = task_result or TaskResult(success=True, steps_taken=3, step_log=[{"action_type": "click"}])
    executor.execute_with_learning = AsyncMock(return_value=(result, should_offer_training))

    persona = make_persona()
    persona_loader = MagicMock(return_value=[persona])

    if playbook_service is None:
        playbook_service = MagicMock()
        playbook_service.write_back = AsyncMock(return_value=MagicMock(dry_run=False, preview=None))
        playbook_service.load_site.return_value = None
        playbook_service.save_flow.return_value = MagicMock()
        playbook_service.update_confidence = MagicMock()
        playbook_service.update_host_confidence = MagicMock()

    kwargs: dict = {}
    if report_dir is not None:
        kwargs["report_dir"] = report_dir

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
        **kwargs,
    )
    # Mock internal services to avoid real DB/memory interactions
    agent._memory_service = MagicMock()
    agent._memory_service.recall = AsyncMock(return_value=[])
    agent._memory_service.store = AsyncMock()
    agent._memory_service.prune = AsyncMock(return_value=0)
    agent._memory_service.clear_session = MagicMock()
    agent._scorer = MagicMock()
    agent._scorer.score.return_value = 1.0

    return agent
