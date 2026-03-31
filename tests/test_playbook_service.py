"""Tests for PlaybookService (WI 6.2)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from soren.models.task import Task
from soren.services.playbook_service import PlaybookInfo, PlaybookService


def _make_settings(playbooks_dir: Path):
    settings = MagicMock()
    settings.playbooks = MagicMock()
    settings.playbooks.base_dir = str(playbooks_dir)
    settings.llm = MagicMock()
    settings.llm.primary_model = "gemma3:12b"
    return settings


def _make_llm():
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="1. Navigate to login page.\n2. Enter credentials.\n3. Submit form.")
    return llm


@pytest.fixture
def playbook_dir(tmp_path):
    """Temporary directory for playbook files."""
    d = tmp_path / "playbooks"
    d.mkdir()
    return d


@pytest.fixture
def playbook_service(playbook_dir):
    """Create PlaybookService with temp dir and mocked LLM."""
    settings = _make_settings(playbook_dir)
    llm = _make_llm()
    return PlaybookService(settings, llm, playbooks_dir=playbook_dir)


def _make_task(
    task_id: str = "t1",
    source_name: str = "prolific",
    task_type: str = "survey",
) -> Task:
    return Task(
        id=task_id,
        dedup_key="k1",
        title="Complete survey",
        description="Fill out the survey",
        task_type=task_type,
        source_name=source_name,
    )


def test_load_returns_content_for_existing_playbook(playbook_service, playbook_dir):
    """load() returns content for existing playbook file."""
    path = playbook_dir / "prolific.md"
    path.write_text("# Prolific Playbook\n\n## Login\nUse the top-right button.")
    content = playbook_service.load("prolific")
    assert content is not None
    assert "Prolific Playbook" in content
    assert "## Login" in content


def test_load_returns_none_for_nonexistent_playbook(playbook_service):
    """load() returns None for non-existent playbook."""
    content = playbook_service.load("nonexistent")
    assert content is None


def test_get_relevant_sections_extracts_login_for_login_task(playbook_service):
    """get_relevant_sections() extracts Login section for login-related tasks."""
    content = """# Service

## Login
Click the login button. Enter email and password.

## Other
Ignore this.
"""
    result = playbook_service.get_relevant_sections(content, "survey")
    assert "## Login" in result
    assert "login button" in result.lower()
    assert "## Other" not in result or "Ignore" not in result


def test_get_relevant_sections_extracts_survey_for_survey_tasks(playbook_service):
    """get_relevant_sections() extracts Survey Completion section for survey tasks."""
    content = """# Service

## Survey Completion
Complete each question. Submit at the end.

## Login
Log in first.
"""
    result = playbook_service.get_relevant_sections(content, "survey")
    assert "Survey" in result or "survey" in result
    assert "Complete" in result or "Submit" in result


def test_get_relevant_sections_returns_full_content_when_no_match(playbook_service):
    """get_relevant_sections() returns full content when no section headers match."""
    content = "# Service\n\nSome generic content here."
    result = playbook_service.get_relevant_sections(content, "unknown_type")
    assert result.strip() == content.strip()


@pytest.mark.asyncio
async def test_write_back_generates_new_playbook_when_none_exists(playbook_service, playbook_dir):
    """write_back() generates new playbook file when none exists."""
    task = _make_task()
    step_log = [
        {"step": 1, "action_type": "click", "action_data": {"x": 10}, "vlm_reasoning": "Click login"},
    ]
    update = await playbook_service.write_back("prolific", task, step_log, dry_run=False)
    assert update.sections_added == 1
    assert update.sections_updated == 0
    assert update.dry_run is False
    path = playbook_dir / "prolific.md"
    assert path.exists()
    content = path.read_text()
    assert "prolific" in content.lower() or "Prolific" in content
    assert "soren-generated" in content


@pytest.mark.asyncio
async def test_write_back_appends_new_section_to_existing_playbook(playbook_service, playbook_dir):
    """write_back() appends new section to existing playbook."""
    path = playbook_dir / "prolific.md"
    path.write_text("# Prolific\n\n## Human Section\nDo not modify this.")
    task = _make_task(task_type="survey")
    update = await playbook_service.write_back("prolific", task, [], dry_run=False)
    content = path.read_text()
    assert "Human Section" in content
    assert "Do not modify this" in content
    assert "soren-generated" in content
    assert update.sections_added == 1


@pytest.mark.asyncio
async def test_write_back_updates_existing_generated_section(playbook_service, playbook_dir):
    """write_back() updates existing generated section (replaces between markers)."""
    path = playbook_dir / "prolific.md"
    path.write_text(
        "# Prolific\n\n## survey (auto-generated)\n\n"
        "<!-- soren-generated: 2025-01-01T00:00:00Z, task: old -->\n"
        "Old content here.\n"
    )
    task = _make_task(task_id="new-task")
    update = await playbook_service.write_back("prolific", task, [], dry_run=False)
    content = path.read_text()
    assert "Old content here" not in content
    assert "new-task" in content
    assert update.sections_updated == 1


@pytest.mark.asyncio
async def test_write_back_preserves_human_edited_content(playbook_service, playbook_dir):
    """write_back() preserves human-edited content outside markers."""
    path = playbook_dir / "prolific.md"
    human = "# Prolific\n\n## Manual Notes\nThese are human notes. Never touch."
    path.write_text(human)
    task = _make_task()
    await playbook_service.write_back("prolific", task, [], dry_run=False)
    content = path.read_text()
    assert "Manual Notes" in content
    assert "These are human notes" in content
    assert "Never touch" in content


@pytest.mark.asyncio
async def test_write_back_tags_generated_content_with_marker(playbook_service, playbook_dir):
    """write_back() tags generated content with <!-- soren-generated --> marker."""
    task = _make_task(task_id="abc-123")
    await playbook_service.write_back("prolific", task, [], dry_run=False)
    content = (playbook_dir / "prolific.md").read_text()
    assert "<!-- soren-generated:" in content
    assert "task: abc-123" in content


@pytest.mark.asyncio
async def test_write_back_dry_run_returns_preview_without_writing(playbook_service, playbook_dir):
    """write_back() with dry_run=True returns preview without writing file."""
    task = _make_task()
    update = await playbook_service.write_back("prolific", task, [], dry_run=True)
    assert update.dry_run is True
    assert update.preview is not None
    assert "prolific" in update.preview.lower() or "Prolific" in update.preview
    path = playbook_dir / "prolific.md"
    assert not path.exists()


def test_list_playbooks_returns_metadata(playbook_service, playbook_dir):
    """list_playbooks() returns metadata for all playbook files."""
    (playbook_dir / "prolific.md").write_text("# Prolific\n## Login\nStep 1")
    (playbook_dir / "mturk.md").write_text("# MTurk\n## A\n## B\n## C")
    infos = playbook_service.list_playbooks()
    assert len(infos) == 2
    names = {p.service_name for p in infos}
    assert "prolific" in names
    assert "mturk" in names
    for p in infos:
        assert isinstance(p, PlaybookInfo)
        assert p.file_size > 0
        assert p.section_count >= 1


def test_list_playbooks_empty_when_no_files(playbook_service):
    """list_playbooks() returns empty list when no playbook files exist."""
    infos = playbook_service.list_playbooks()
    assert infos == []


@pytest.mark.asyncio
async def test_llm_summarization_produces_instructions(playbook_service, playbook_dir):
    """LLM summarization prompt produces reusable step-by-step instructions."""
    task = _make_task()
    step_log = [
        {"action_type": "click", "action_data": {"selector": "button"}, "vlm_reasoning": "Click submit"},
        {"action_type": "type", "action_data": {"text": "hello"}, "vlm_reasoning": "Enter text"},
    ]
    await playbook_service.write_back("prolific", task, step_log, dry_run=False)
    content = (playbook_dir / "prolific.md").read_text()
    assert "Navigate" in content or "Enter" in content or "Submit" in content


def test_playbook_content_integrated_into_persona_context():
    """Playbook content integrated into PersonaContext.build() output."""
    from soren.models.persona import Persona
    from soren.services.persona_context import PersonaContext

    persona = Persona(name="Test", id="p1", task_sources=[], enabled=True)
    task = Task(id="t1", dedup_key="k1", title="Test", source_name="prolific", task_type="survey")
    playbook = "## Login\nClick the top-right button."
    result = PersonaContext.build(persona, task, playbook_content=playbook)
    assert "Playbook instructions" in result
    assert "Click the top-right button" in result
