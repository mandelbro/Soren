"""Shared builders for TaskExecutor tests (task, personas, executor with mocks, async generator)."""

from pathlib import Path
from unittest.mock import MagicMock

from soren.models.persona import Persona, Traits
from soren.models.task import Task
from soren.models.task_source import TaskSource
from soren.services.task_executor import TaskExecutor


def make_task(
    id: str = "task-1",
    description: str = "Open Safari and navigate to example.com",
) -> Task:
    return Task(
        id=id,
        dedup_key="test:1:persona",
        title="Test",
        description=description,
        task_type="survey",
        source_name="TestSource",
    )


def make_persona() -> Persona:
    return Persona(name="Test", traits=Traits())


def make_persona_with_source(credential_ref: str = "") -> Persona:
    return Persona(
        name="Test",
        traits=Traits(),
        task_sources=[
            TaskSource(
                name="TestSource",
                type="survey_platform",
                url="https://example.com",
                credential_ref=credential_ref,
            )
        ],
    )


def make_executor(tmp_path: Path, step_budget: int = 50) -> TaskExecutor:
    settings = MagicMock()
    llm = MagicMock()
    vm = MagicMock()
    return TaskExecutor(
        settings=settings,
        llm=llm,
        vm=vm,
        step_budget=step_budget,
        screenshot_dir=tmp_path / "screenshots",
    )


async def async_gen(items):
    """Helper to create async generator from list."""
    for x in items:
        yield x
