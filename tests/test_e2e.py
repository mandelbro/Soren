"""End-to-end tests for the full Soren cycle.

Requires: Lume running, VM started, Ollama with gemma3:12b, MLX with UI-TARS.
Run with: just test-e2e
"""

import socket
from pathlib import Path

import pytest

from soren.models.session import CycleReport


def _lume_reachable() -> bool:
    """Quick check if Lume API is reachable (avoids slow httpx timeout)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(("localhost", 7777))
        return True
    except OSError:
        return False


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_cycle_discovery_fallback_execution_report(tmp_path: Path) -> None:
    """Full cycle: empty backlog -> discovery -> task execution -> report generation."""
    if not _lume_reachable():
        pytest.skip("Lume not reachable; start Lume for E2E test (lume serve)")

    from soren.services.config_loader import load_settings
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.meta_agent import SorenMetaAgent
    from soren.services.persona_loader import get_personas_dir, load_all
    from soren.services.task_backlog import TaskBacklog
    from soren.services.task_discovery import TaskDiscovery
    from soren.services.task_executor import TaskExecutor
    from soren.services.vm_manager import VMManager

    settings = load_settings()
    vm = VMManager(settings.vm)

    if not await vm.is_running():
        pytest.skip("VM not running; start VM for E2E test (just vm-status)")

    db_path = tmp_path / "soren_e2e.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = DatabaseManager(db_path)
    await db.initialize()

    backlog = TaskBacklog(
        db,
        retry_backoff_minutes=getattr(settings.scheduler, "retry_backoff_minutes", None),
    )
    llm = LLMService(settings)
    discovery = TaskDiscovery(settings, llm, vm, backlog)
    executor = TaskExecutor(
        settings,
        llm,
        vm,
        step_budget=10,
        screenshot_dir=tmp_path / "screenshots",
    )

    def load_personas():
        return load_all(get_personas_dir())

    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    agent = SorenMetaAgent(
        settings=settings,
        db=db,
        llm=llm,
        vm=vm,
        backlog=backlog,
        discovery=discovery,
        executor=executor,
        persona_loader=load_personas,
        report_dir=report_dir,
    )

    report = await agent.run_cycle()

    await db.close()

    assert isinstance(report, CycleReport)
    assert report.cycle_id
    assert report.started_at
    assert report.completed_at
    assert report.total_duration_seconds >= 0
    assert report.backlog_snapshot is not None

    report_file = report_dir / f"{report.cycle_id}.json"
    if report_file.exists():
        assert report_file.read_text()
    latest = report_dir / "latest.json"
    if latest.exists():
        assert latest.is_symlink() or latest.exists()
