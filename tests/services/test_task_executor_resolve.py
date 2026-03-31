"""Tests for TaskExecutor cascading playbook resolve integration (Task 009)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from soren.models.persona import Persona
from soren.models.task import Task
from soren.services.task_executor import TaskExecutor


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.vlm = MagicMock()
    settings.vlm.full_model_str.return_value = "mlx/mlx-community/UI-TARS-1.5-7B-6bit"
    settings.verification = MagicMock()
    settings.verification.model = "qwen3-vl:8b"
    settings.playbooks = MagicMock()
    settings.playbooks.cascade_enabled = True
    return settings


def _make_task() -> Task:
    return Task(
        id="t1",
        dedup_key="k1",
        title="Complete survey",
        description="Fill out the survey",
        source_name="prolific",
        task_type="survey",
    )


def _make_persona() -> Persona:
    return Persona(name="Test", id="p1", task_sources=[], enabled=True)


class TestExecutorUsesResolve:
    def test_calls_resolve_instead_of_load(self, tmp_path: Path) -> None:
        settings = _make_settings()
        playbook_svc = MagicMock()
        playbook_svc.resolve.return_value = "## Site-Specific Instructions\nDo the thing."

        executor = TaskExecutor(
            settings, MagicMock(), MagicMock(),
            playbook_service=playbook_svc,
            screenshot_dir=tmp_path / "screenshots",
        )

        task = _make_task()
        # Access internal method to test playbook loading behavior
        content = executor._load_playbook_content(task)
        assert content is not None
        assert "Site-Specific Instructions" in content
        playbook_svc.resolve.assert_called_once_with(
            source_name="prolific",
            host_domain=None,
            task_type="survey",
            flow_type="execution",
        )

    def test_passes_resolve_output_to_persona_context(self, tmp_path: Path) -> None:
        settings = _make_settings()
        playbook_svc = MagicMock()
        playbook_svc.resolve.return_value = "Resolved playbook content"

        executor = TaskExecutor(
            settings, MagicMock(), MagicMock(),
            playbook_service=playbook_svc,
            screenshot_dir=tmp_path / "screenshots",
        )

        task = _make_task()
        content = executor._load_playbook_content(task)
        assert content == "Resolved playbook content"

    def test_handles_none_playbook_service(self, tmp_path: Path) -> None:
        settings = _make_settings()

        executor = TaskExecutor(
            settings, MagicMock(), MagicMock(),
            playbook_service=None,
            screenshot_dir=tmp_path / "screenshots",
        )

        task = _make_task()
        content = executor._load_playbook_content(task)
        assert content is None

    def test_handles_empty_resolve_result(self, tmp_path: Path) -> None:
        settings = _make_settings()
        playbook_svc = MagicMock()
        playbook_svc.resolve.return_value = ""

        executor = TaskExecutor(
            settings, MagicMock(), MagicMock(),
            playbook_service=playbook_svc,
            screenshot_dir=tmp_path / "screenshots",
        )

        task = _make_task()
        content = executor._load_playbook_content(task)
        # Empty string from resolve should be treated as no content
        assert content is None or content == ""
