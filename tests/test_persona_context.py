"""Tests for PersonaContext builder."""

from soren.models.persona import Demographics, Persona, Traits
from soren.models.task import Task
from soren.services.persona_context import PersonaContext


def _make_persona(
    name: str = "Test Persona",
    conscientiousness: float = 0.5,
    extraversion: float = 0.5,
    **kwargs: object,
) -> Persona:
    """Create a Persona with optional overrides."""
    traits = Traits(
        conscientiousness=conscientiousness,
        openness=0.5,
        agreeableness=0.5,
        extraversion=extraversion,
        neuroticism=0.5,
    )
    return Persona(name=name, traits=traits, **kwargs)


def _make_task(
    description: str = "Complete the survey",
    task_type: str = "survey",
    source_name: str = "Delta Panel",
) -> Task:
    """Create a Task with optional overrides."""
    return Task(
        id="task-1",
        dedup_key="sj:123:persona-1",
        title="Survey",
        description=description,
        task_type=task_type,
        source_name=source_name,
    )


class TestPersonaContextBuild:
    """Tests for PersonaContext.build()."""

    def test_build_produces_non_empty_instructions(self) -> None:
        """Build produces non-empty instruction string for valid persona + task."""
        persona = _make_persona()
        task = _make_task()
        result = PersonaContext.build(persona, task)
        assert result
        assert len(result) > 50

    def test_demographic_identity_includes_age_occupation_education(self) -> None:
        """Demographic identity statement includes age, occupation, education."""
        persona = Persona(
            name="Alex",
            demographics=Demographics(
                age="28-35",
                occupation="software_engineer",
                education="masters",
            ),
        )
        task = _make_task()
        result = PersonaContext.build(persona, task)
        assert "28-35" in result
        assert "software_engineer" in result
        assert "masters" in result
        assert "Alex" in result

    def test_high_conscientiousness_includes_thoroughness_directive(self) -> None:
        """High conscientiousness persona includes thoroughness directive."""
        persona = _make_persona(conscientiousness=0.9)
        task = _make_task()
        result = PersonaContext.build(persona, task)
        assert "thorough" in result.lower()
        assert "detail-oriented" in result.lower()
        assert "double-check" in result.lower()

    def test_low_extraversion_includes_reserved_directive(self) -> None:
        """Low extraversion persona includes reserved/thoughtful directive."""
        persona = _make_persona(extraversion=0.2)
        task = _make_task()
        result = PersonaContext.build(persona, task)
        assert "reserved" in result.lower() or "thoughtful" in result.lower()
        assert "concise" in result.lower()

    def test_playbook_content_appended_when_provided(self) -> None:
        """Playbook content appended when provided."""
        persona = _make_persona()
        task = _make_task()
        playbook = "Navigate to dashboard. Click the first survey."
        result = PersonaContext.build(persona, task, playbook_content=playbook)
        assert "Playbook instructions" in result
        assert "Navigate to dashboard" in result
        assert "Click the first survey" in result

    def test_playbook_content_omitted_when_none(self) -> None:
        """Playbook content omitted when None."""
        persona = _make_persona()
        task = _make_task()
        result = PersonaContext.build(persona, task, playbook_content=None)
        assert "Service playbook" not in result

    def test_memory_content_appended_when_provided(self) -> None:
        """Memory recall content appended when provided."""
        persona = _make_persona()
        task = _make_task()
        memories = ["Last time, the login button was in the top-right."]
        result = PersonaContext.build(persona, task, memories=memories)
        assert "Relevant memories" in result
        assert "login button" in result

    def test_memory_content_omitted_when_none(self) -> None:
        """Memory content omitted when None."""
        persona = _make_persona()
        task = _make_task()
        result = PersonaContext.build(persona, task, memories=None)
        assert "Relevant memories" not in result

    def test_step_budget_notice_included_with_correct_number(self) -> None:
        """Step budget notice included with correct budget number."""
        persona = _make_persona()
        task = _make_task()
        result = PersonaContext.build(persona, task, step_budget=25)
        assert "25" in result
        assert "maximum" in result.lower()
        assert "steps" in result.lower()

    def test_task_description_included_in_context(self) -> None:
        """Task description included in context."""
        persona = _make_persona()
        task = _make_task(description="Reply to the email from John")
        result = PersonaContext.build(persona, task)
        assert "Reply to the email from John" in result
        assert "Task" in result

    def test_different_personas_produce_different_instructions(self) -> None:
        """Different personas produce meaningfully different instructions."""
        persona1 = _make_persona(name="Fast", conscientiousness=0.2, extraversion=0.9)
        persona2 = _make_persona(name="Careful", conscientiousness=0.9, extraversion=0.2)
        task = _make_task()
        result1 = PersonaContext.build(persona1, task)
        result2 = PersonaContext.build(persona2, task)
        assert result1 != result2
        assert "Fast" in result1
        assert "Careful" in result2
        assert "thorough" in result2.lower()
        assert "efficient" in result1.lower()
