"""Tests for discovery_hint injection into adapter navigation prompts (Task 001)."""

from soren.adapters.email_adapter import EmailAdapter
from soren.adapters.generic_form import GenericFormAdapter
from soren.adapters.survey_platform import SurveyPlatformAdapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


def _persona() -> Persona:
    return Persona(name="Researcher", id="researcher")


def _source(
    source_type: str = "survey_platform",
    hint: str | None = None,
) -> TaskSource:
    return TaskSource(
        name="test-source",
        type=source_type,
        url="https://example.com",
        discovery_hint=hint,
    )


HINT_TEXT = "Look for available surveys showing reward amount and estimated time"


# --- SurveyPlatformAdapter ---


class TestSurveyPlatformHint:
    def test_prompt_without_hint(self):
        adapter = SurveyPlatformAdapter()
        prompt = adapter.navigation_prompt(_source(hint=None), _persona())
        assert "SITE HINT" not in prompt
        assert "DASHBOARD" in prompt

    def test_prompt_with_hint(self):
        adapter = SurveyPlatformAdapter()
        prompt = adapter.navigation_prompt(_source(hint=HINT_TEXT), _persona())
        assert "SITE HINT:" in prompt
        assert HINT_TEXT in prompt

    def test_prompt_with_empty_hint(self):
        adapter = SurveyPlatformAdapter()
        prompt = adapter.navigation_prompt(_source(hint=""), _persona())
        assert "SITE HINT" not in prompt

    def test_hint_appears_after_cookie_banners(self):
        adapter = SurveyPlatformAdapter()
        prompt = adapter.navigation_prompt(_source(hint=HINT_TEXT), _persona())
        cookie_pos = prompt.index("COOKIE BANNERS")
        hint_pos = prompt.index("SITE HINT")
        dashboard_pos = prompt.index("DASHBOARD")
        assert cookie_pos < hint_pos < dashboard_pos


# --- EmailAdapter ---


class TestEmailAdapterHint:
    def test_prompt_without_hint(self):
        adapter = EmailAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="email", hint=None), _persona()
        )
        assert "SITE HINT" not in prompt
        assert "INBOX" in prompt

    def test_prompt_with_hint(self):
        adapter = EmailAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="email", hint=HINT_TEXT), _persona()
        )
        assert "SITE HINT:" in prompt
        assert HINT_TEXT in prompt

    def test_prompt_with_empty_hint(self):
        adapter = EmailAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="email", hint=""), _persona()
        )
        assert "SITE HINT" not in prompt

    def test_hint_appears_after_cookie_banners(self):
        adapter = EmailAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="email", hint=HINT_TEXT), _persona()
        )
        cookie_pos = prompt.index("COOKIE BANNERS")
        hint_pos = prompt.index("SITE HINT")
        inbox_pos = prompt.index("3. INBOX")
        assert cookie_pos < hint_pos < inbox_pos


# --- GenericFormAdapter ---


class TestGenericFormHint:
    def test_prompt_without_hint(self):
        adapter = GenericFormAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="generic_form", hint=None), _persona()
        )
        assert "SITE HINT" not in prompt
        assert "FORM" in prompt

    def test_prompt_with_hint(self):
        adapter = GenericFormAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="generic_form", hint=HINT_TEXT), _persona()
        )
        assert "SITE HINT:" in prompt
        assert HINT_TEXT in prompt

    def test_prompt_with_empty_hint(self):
        adapter = GenericFormAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="generic_form", hint=""), _persona()
        )
        assert "SITE HINT" not in prompt

    def test_hint_appears_after_cookie_banners(self):
        adapter = GenericFormAdapter()
        prompt = adapter.navigation_prompt(
            _source(source_type="generic_form", hint=HINT_TEXT), _persona()
        )
        cookie_pos = prompt.index("COOKIE BANNERS")
        hint_pos = prompt.index("SITE HINT")
        form_pos = prompt.index("3. FORM")
        assert cookie_pos < hint_pos < form_pos
