"""Tests for PersonaContext cascading playbook integration (Task 009)."""

from __future__ import annotations

from soren.models.persona import Persona
from soren.models.task import Task
from soren.services.persona_context import PersonaContext


def _make_persona() -> Persona:
    return Persona(name="Test User", id="p1", task_sources=[], enabled=True)


def _make_task() -> Task:
    return Task(
        id="t1",
        dedup_key="k1",
        title="Complete survey",
        description="Fill out the survey",
        source_name="prolific",
        task_type="survey",
    )


class TestPlaybookSectionHeader:
    def test_uses_cascading_header(self) -> None:
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content="## Site-Specific Instructions\nFollow these first.",
        )
        assert "Playbook instructions (follow in order of specificity):" in result

    def test_cascading_content_position(self) -> None:
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content="Cascading content here",
            memories=["Previous memory"],
        )
        # Playbook should appear after task context, before memories
        playbook_pos = result.index("Playbook instructions")
        task_pos = result.index("Task context")
        memory_pos = result.index("Relevant memories")
        assert task_pos < playbook_pos < memory_pos

    def test_no_playbook_omits_section(self) -> None:
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
        )
        assert "Playbook" not in result
        assert "playbook" not in result.lower()
