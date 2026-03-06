"""Tests for report writer."""

from pathlib import Path

from soren.models.session import BacklogSummary, CycleReport, TaskResult
from soren.services.report_writer import write_report


def test_report_writer_produces_valid_json(tmp_path: Path) -> None:
    """Report writer produces valid JSON file."""
    report = CycleReport(
        cycle_id="cycle-123",
        cycle_type="execute",
        total_duration_seconds=10.5,
        task_result=TaskResult(success=True, steps_taken=5, duration_seconds=8.0),
    )
    path = write_report(report, tmp_path)
    assert path.exists()
    content = path.read_text()
    assert "cycle-123" in content
    assert "cycle_type" in content
    import json

    parsed = json.loads(content)
    assert parsed["cycle_id"] == "cycle-123"
    assert parsed["task_result"]["success"] is True


def test_report_writer_creates_latest_symlink(tmp_path: Path) -> None:
    """Report writer creates latest.json symlink."""
    report = CycleReport(cycle_id="cycle-456", cycle_type="execute")
    path = write_report(report, tmp_path)
    latest = tmp_path / "latest.json"
    assert latest.exists()
    assert latest.is_symlink()
    assert latest.resolve() == path.resolve()


def test_report_writer_includes_all_cycle_report_fields(tmp_path: Path) -> None:
    """Report writer includes all CycleReport fields."""
    report = CycleReport(
        cycle_id="cycle-789",
        cycle_type="execute",
        started_at="2026-01-01T00:00:00",
        completed_at="2026-01-01T00:01:00",
        total_duration_seconds=60.0,
        personas_used=["researcher"],
        discovery_ran=False,
        task_executed_id="task-1",
        task_result=TaskResult(success=True, steps_taken=10),
        backlog_snapshot=BacklogSummary(total=5, queued=3),
        total_agent_steps=10,
        total_screenshots_captured=5,
        model_used="gemma3:12b",
    )
    path = write_report(report, tmp_path)
    content = path.read_text()
    assert "cycle-789" in content
    assert "personas_used" in content
    assert "task_result" in content
    assert "backlog_snapshot" in content
    assert "model_used" in content
