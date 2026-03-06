"""Tests for GenericFormAdapter (WI 4.4)."""

from soren.adapters.base import DISCOVERY_READY, get_adapter
from soren.adapters.generic_form import GenericFormAdapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


def _source(url: str = "https://example.com/form") -> TaskSource:
    return TaskSource(name="contact_form", type="generic_form", url=url)


def _persona() -> Persona:
    return Persona(name="Researcher", id="researcher")


def test_form_adapter_implements_discovery_adapter_protocol():
    """GenericFormAdapter implements all three DiscoveryAdapter protocol methods."""
    adapter = GenericFormAdapter()
    source = _source()
    persona = _persona()
    assert adapter.navigation_prompt(source, persona)
    assert adapter.extraction_prompt(source)
    assert adapter.structuring_prompt(source, "raw extraction text")
    assert adapter.source_type == "generic_form"
    assert adapter.expected_fields == [
        "form_title",
        "form_type",
        "fields",
        "submit_button_text",
        "provider_task_id",
    ]


def test_form_navigation_prompt_handles_login_gated_forms():
    """Form navigation prompt handles login-gated forms."""
    adapter = GenericFormAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert "login" in prompt.lower()
    assert "credentials" in prompt.lower() or "password" in prompt.lower()
    assert DISCOVERY_READY in prompt


def test_form_extraction_prompt_identifies_field_types_and_required_status():
    """Form extraction prompt identifies field types and required status."""
    adapter = GenericFormAdapter()
    prompt = adapter.extraction_prompt(_source())
    assert "field_type" in prompt.lower() or "field type" in prompt.lower()
    assert "required" in prompt.lower()
    assert "label" in prompt.lower()


def test_form_structuring_prompt_produces_correct_json_schema():
    """Form structuring prompt produces correct JSON schema."""
    adapter = GenericFormAdapter()
    prompt = adapter.structuring_prompt(_source(), "Form found: yes")
    assert "json" in prompt.lower()
    assert "title" in prompt.lower()
    assert "fields" in prompt.lower()
    assert "form_type" in prompt.lower()
    assert "provider_task_id" in prompt.lower()
    assert "task_type" in prompt.lower()


def test_form_navigation_prompt_includes_discovery_ready_sentinel():
    """Form navigation prompt includes DISCOVERY_READY sentinel."""
    adapter = GenericFormAdapter()
    prompt = adapter.navigation_prompt(_source(), _persona())
    assert DISCOVERY_READY in prompt
    assert prompt.count(DISCOVERY_READY) >= 2


def test_form_adapter_registry_resolves_generic_form():
    """Adapter registry resolves generic_form type."""
    adapter = get_adapter("generic_form")
    assert adapter is not None
    assert isinstance(adapter, GenericFormAdapter)


def test_form_structuring_prompt_uses_source_url_for_dedup():
    """Form structuring prompt uses source URL for provider_task_id (dedup)."""
    adapter = GenericFormAdapter()
    source = TaskSource(name="x", type="generic_form", url="https://custom.example/form")
    prompt = adapter.structuring_prompt(source, "Form found: yes")
    assert "custom.example" in prompt or "https://" in prompt
    assert "provider_task_id" in prompt.lower()
