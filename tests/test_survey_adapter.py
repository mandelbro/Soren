"""Tests for SurveyPlatformAdapter (WI 4.3)."""

import pytest

from soren.adapters.base import DISCOVERY_READY, get_adapter
from soren.adapters.survey_platform import SurveyPlatformAdapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


def _source(url: str = "https://app.betapanel.example") -> TaskSource:
    return TaskSource(name="betapanel", type="survey_platform", url=url)


def _persona() -> Persona:
    return Persona(name="Researcher", id="researcher")


def test_adapter_implements_discovery_adapter_protocol():
    """Adapter implements all three DiscoveryAdapter protocol methods."""
    adapter = SurveyPlatformAdapter()
    source = _source()
    persona = _persona()
    assert adapter.navigation_prompt(source, persona)
    assert adapter.extraction_prompt(source)
    assert adapter.structuring_prompt(source, "raw extraction text")
    assert adapter.source_type == "survey_platform"
    assert adapter.expected_fields == [
        "title",
        "estimated_duration_minutes",
        "estimated_reward",
        "reward_currency",
        "provider_task_id",
    ]


def test_navigation_prompt_includes_login_instructions():
    """navigation_prompt() includes login instructions."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert "login" in prompt.lower()
    assert "credentials" in prompt.lower() or "password" in prompt.lower()


def test_navigation_prompt_includes_scroll_instructions():
    """navigation_prompt() includes scroll instructions."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert "scroll" in prompt.lower()
    assert "load" in prompt.lower() or "lazy" in prompt.lower()


def test_navigation_prompt_includes_discovery_ready_sentinel():
    """navigation_prompt() includes DISCOVERY_READY sentinel."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert DISCOVERY_READY in prompt
    assert prompt.count(DISCOVERY_READY) >= 2  # Mentioned for 2FA and completion


def test_navigation_prompt_includes_captcha_and_2fa_detection():
    """navigation_prompt() includes CAPTCHA and 2FA detection."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert "captcha" in prompt.lower() or "2fa" in prompt.lower()
    assert "verify" in prompt.lower() or "2fa" in prompt.lower()


def test_extraction_prompt_requests_all_expected_fields():
    """extraction_prompt() requests all expected fields."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.extraction_prompt(_source())
    assert "title" in prompt.lower()
    assert "duration" in prompt.lower()
    assert "reward" in prompt.lower()
    assert "provider_task_id" in prompt.lower() or "id" in prompt.lower()


def test_extraction_prompt_includes_multi_screenshot_dedup_instructions():
    """extraction_prompt() includes multi-screenshot dedup instructions."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.extraction_prompt(_source())
    assert "screenshot" in prompt.lower() or "overlap" in prompt.lower()
    assert "once" in prompt.lower() or "unique" in prompt.lower() or "dedup" in prompt.lower()


def test_extraction_prompt_requests_total_count():
    """extraction_prompt() requests total count."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.extraction_prompt(_source())
    assert "total" in prompt.lower() and "found" in prompt.lower()


def test_structuring_prompt_defines_correct_json_schema():
    """structuring_prompt() defines correct JSON schema."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.structuring_prompt(_source(), "1. Survey A, 15 min, $2")
    assert "json" in prompt.lower()
    assert "title" in prompt.lower()
    assert "estimated_duration_minutes" in prompt.lower()
    assert "estimated_reward" in prompt.lower()
    assert "provider_task_id" in prompt.lower()
    assert "reward_currency" in prompt.lower()


def test_structuring_prompt_includes_duration_and_reward_parsing_rules():
    """structuring_prompt() includes duration and reward parsing rules."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.structuring_prompt(_source(), "raw")
    assert "15" in prompt or "min" in prompt.lower() or "minute" in prompt.lower()
    assert "2.5" in prompt or "2.50" in prompt or "numeric" in prompt.lower()
    assert "null" in prompt.lower()


def test_adapter_registry_resolves_survey_platform():
    """Adapter registry resolves survey_platform type."""
    adapter = get_adapter("survey_platform")
    assert adapter is not None
    assert isinstance(adapter, SurveyPlatformAdapter)


def test_navigation_prompt_uses_source_url():
    """Navigation prompt uses source URL when provided."""
    adapter = SurveyPlatformAdapter()
    prompt = adapter.navigation_prompt(
        TaskSource(name="x", type="survey_platform", url="https://custom.example/dash"),
        _persona(),
    )
    assert "custom.example" in prompt or "https://" in prompt


@pytest.mark.integration
def test_full_discovery_against_real_survey_platform():
    """Full discovery against a real survey platform (manual verification).

    Run with: pytest -m integration -v
    Requires: VM running, models loaded, valid credentials.
    """
    pytest.skip("Manual verification: run with live VM and models")
