"""Tests for EmailAdapter (WI 4.4)."""

from soren.adapters.base import DISCOVERY_READY, get_adapter
from soren.adapters.email_adapter import EmailAdapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


def _source(url: str = "https://mail.google.com") -> TaskSource:
    return TaskSource(name="gmail", type="email", url=url)


def _persona() -> Persona:
    return Persona(name="Researcher", id="researcher")


def test_email_adapter_implements_discovery_adapter_protocol():
    """EmailAdapter implements all three DiscoveryAdapter protocol methods."""
    adapter = EmailAdapter()
    source = _source()
    persona = _persona()
    assert adapter.navigation_prompt(source, persona)
    assert adapter.extraction_prompt(source)
    assert adapter.structuring_prompt(source, "raw extraction text")
    assert adapter.source_type == "email"
    assert adapter.expected_fields == [
        "sender",
        "subject",
        "preview",
        "timestamp",
        "provider_task_id",
        "requires_response",
    ]


def test_email_navigation_prompt_includes_inbox_and_unread_filtering():
    """Email navigation prompt includes inbox navigation and unread filtering."""
    adapter = EmailAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert "inbox" in prompt.lower()
    assert "unread" in prompt.lower()
    assert "filter" in prompt.lower() or "sort" in prompt.lower()


def test_email_extraction_prompt_filters_out_promotional_emails():
    """Email extraction prompt filters out promotional emails."""
    adapter = EmailAdapter()
    prompt = adapter.extraction_prompt(_source())
    assert "promotional" in prompt.lower() or "newsletter" in prompt.lower()
    assert "skip" in prompt.lower() or "filter" in prompt.lower()


def test_email_extraction_prompt_requests_requires_response_assessment():
    """Email extraction prompt requests requires_response assessment."""
    adapter = EmailAdapter()
    prompt = adapter.extraction_prompt(_source())
    assert "requires_response" in prompt.lower()
    assert "true" in prompt.lower() or "false" in prompt.lower()


def test_email_structuring_prompt_produces_correct_json_schema():
    """Email structuring prompt produces correct JSON schema."""
    adapter = EmailAdapter()
    prompt = adapter.structuring_prompt(_source(), "1. sender: X, subject: Y")
    assert "json" in prompt.lower()
    assert "title" in prompt.lower()
    assert "snippet" in prompt.lower()
    assert "provider_task_id" in prompt.lower()
    assert "sender" in prompt.lower()
    assert "timestamp" in prompt.lower()
    assert "task_type" in prompt.lower()


def test_email_navigation_prompt_includes_discovery_ready_sentinel():
    """Email navigation prompt includes DISCOVERY_READY sentinel."""
    adapter = EmailAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert DISCOVERY_READY in prompt
    assert prompt.count(DISCOVERY_READY) >= 2


def test_email_navigation_prompt_includes_captcha_and_2fa_detection():
    """Email navigation prompt includes CAPTCHA and 2FA detection."""
    adapter = EmailAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert "captcha" in prompt.lower() or "2fa" in prompt.lower()


def test_email_adapter_registry_resolves_email():
    """Adapter registry resolves email type."""
    adapter = get_adapter("email")
    assert adapter is not None
    assert isinstance(adapter, EmailAdapter)
