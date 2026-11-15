"""Tests for TaskDiscovery pipeline (WI 4.2)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.adapters.base import DISCOVERY_READY, get_adapter
from soren.adapters.survey_platform import SurveyPlatformAdapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


def test_discovery_adapter_protocol_enforces_prompt_methods():
    """DiscoveryAdapter protocol enforces all three prompt methods."""
    adapter = SurveyPlatformAdapter()
    source = TaskSource(name="test", type="survey_platform", url="https://x.co")
    persona = Persona(name="Test", id="test")
    assert adapter.navigation_prompt(source, persona)
    assert adapter.extraction_prompt(source)
    assert adapter.structuring_prompt(source, "raw")
    assert adapter.source_type == "survey_platform"
    assert "title" in adapter.expected_fields


def test_navigation_prompt_includes_discovery_ready():
    """Navigation prompt includes DISCOVERY_READY sentinel."""
    adapter = SurveyPlatformAdapter()
    source = TaskSource(name="t", type="survey_platform", url="https://x.co")
    persona = Persona(name="P", id="p")
    prompt = adapter.navigation_prompt(source, persona)
    assert DISCOVERY_READY in prompt


def test_extraction_prompt_requests_fields():
    """Extraction prompt requests survey fields."""
    adapter = SurveyPlatformAdapter()
    source = TaskSource(name="t", type="survey_platform", url="https://x.co")
    prompt = adapter.extraction_prompt(source)
    assert "title" in prompt.lower() or "survey" in prompt.lower()
    assert "duration" in prompt.lower() or "reward" in prompt.lower()


def test_structuring_prompt_defines_json():
    """Structurating prompt defines JSON output."""
    adapter = SurveyPlatformAdapter()
    source = TaskSource(name="t", type="survey_platform", url="https://x.co")
    prompt = adapter.structuring_prompt(source, "1. Survey A, 15 min, $2")
    assert "json" in prompt.lower() or "JSON" in prompt


def test_adapter_registry_resolves_survey_platform():
    """Adapter registry resolves correct adapter for source type."""
    adapter = get_adapter("survey_platform")
    assert adapter is not None
    assert adapter.source_type == "survey_platform"


def test_adapter_registry_returns_none_for_unknown():
    """Adapter registry returns None for unregistered type."""
    assert get_adapter("unknown_type") is None


@pytest.mark.asyncio
async def test_phase3_parse_discovered_tasks():
    """Phase 3 output parsed into list of DiscoveredTask dicts."""
    from soren.services.task_discovery import TaskDiscovery

    settings = MagicMock()
    settings.vlm = MagicMock()
    settings.vlm.model = "test"
    settings.verification = MagicMock()
    settings.verification.model = "qwen3-vl:8b"
    llm = AsyncMock()
    vm = AsyncMock()
    backlog = AsyncMock()

    discovery = TaskDiscovery(settings, llm, vm, backlog)
    source = TaskSource(name="betapanel", type="survey_platform", url="https://x.co")
    persona = Persona(name="R", id="researcher")

    text = '[{"title": "Survey 1", "estimated_duration_minutes": 15, "estimated_reward": 2.5}]'
    tasks = discovery._parse_discovered_tasks(text, source, persona)
    assert len(tasks) == 1
    assert tasks[0].title == "Survey 1"
    assert tasks[0].estimated_duration_minutes == 15
    assert tasks[0].estimated_reward == 2.5
    assert tasks[0].persona_id == "researcher"
    assert tasks[0].source_name == "betapanel"


@pytest.mark.asyncio
async def test_phase3_parse_handles_tasks_wrapper():
    """Phase 3 parses object with tasks key."""
    from soren.services.task_discovery import TaskDiscovery

    settings = MagicMock()
    discovery = TaskDiscovery(settings, AsyncMock(), AsyncMock(), AsyncMock())
    source = TaskSource(name="s", type="survey_platform", url="https://x.co")
    persona = Persona(name="P", id="p")

    text = '{"tasks": [{"title": "T1"}]}'
    tasks = discovery._parse_discovered_tasks(text, source, persona)
    assert len(tasks) == 1
    assert tasks[0].title == "T1"


@pytest.mark.asyncio
async def test_phase3_parse_handles_invalid_json():
    """Phase 3 returns empty list for invalid JSON."""
    from soren.services.task_discovery import TaskDiscovery

    settings = MagicMock()
    discovery = TaskDiscovery(settings, AsyncMock(), AsyncMock(), AsyncMock())
    source = TaskSource(name="s", type="survey_platform", url="https://x.co")
    persona = Persona(name="P", id="p")

    tasks = discovery._parse_discovered_tasks("not json", source, persona)
    assert tasks == []


@pytest.mark.asyncio
async def test_discover_returns_empty_when_no_adapter():
    """Discovery returns empty list when no adapter for source type."""
    from soren.services.task_discovery import TaskDiscovery

    settings = MagicMock()
    llm = AsyncMock()
    vm = AsyncMock()
    backlog = AsyncMock()
    discovery = TaskDiscovery(settings, llm, vm, backlog)

    source = TaskSource(name="x", type="unknown_type", url="https://x.co")
    persona = Persona(name="P", id="p")
    computer = MagicMock()

    tasks = await discovery.discover(persona, source, computer)
    assert tasks == []


@pytest.mark.asyncio
async def test_discover_handles_navigation_failure_gracefully():
    """Discovery handles navigation failure gracefully (returns empty, no crash)."""
    from soren.services.task_discovery import TaskDiscovery

    settings = MagicMock()
    settings.vlm = MagicMock()
    settings.vlm.model = "test"
    llm = AsyncMock()
    vm = AsyncMock()
    backlog = AsyncMock()
    discovery = TaskDiscovery(settings, llm, vm, backlog)

    source = TaskSource(name="p", type="survey_platform", url="https://x.co")
    persona = Persona(name="R", id="researcher")
    computer = MagicMock()

    with patch.object(discovery, "_phase1_navigate", side_effect=RuntimeError("nav failed")):
        tasks = await discovery.discover(persona, source, computer)
    assert tasks == []
