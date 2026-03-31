"""Tests for auto-playbook generation from successful execution logs (Task 012)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from soren.services.auto_playbook import (
    _parse_steps_from_llm,
    auto_generate_host_playbook,
    auto_generate_site_playbook,
)

SAMPLE_STEP_LOG = [
    {"step": 1, "action_type": "click", "action_data": {"target": "login_button"}, "vlm_reasoning": "Clicking login"},
    {"step": 2, "action_type": "type", "action_data": {"text": "user@example.com"}, "vlm_reasoning": "Entering email"},
    {"step": 3, "action_type": "click", "action_data": {"target": "submit"}, "vlm_reasoning": "Submitting form"},
]

YAML_LLM_RESPONSE = """\
- seq: 1
  instruction: Click the login button in the top-right corner
  expected_after: Login modal is displayed
  field: credential:login
  triggers_new_tab: false
- seq: 2
  instruction: Enter email address in the email field
  expected_after: Email field is populated
  field: credential:email
  triggers_new_tab: false
- seq: 3
  instruction: Click submit to complete login
  expected_after: Dashboard is visible
  field: null
  triggers_new_tab: false
"""

HOST_YAML_RESPONSE = """\
navigation:
  sidebar: Left sidebar with expandable sections
  top_bar: Fixed navigation bar with search
quirks:
  - Cookie consent banner appears on first visit
  - Survey list uses infinite scroll instead of pagination
"""


def _make_llm(response: str = "") -> AsyncMock:
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value=response)
    return llm


class TestAutoGenerateSitePlaybook:
    async def test_produces_flow_playbook(self) -> None:
        llm = _make_llm(YAML_LLM_RESPONSE)

        result = await auto_generate_site_playbook(
            llm=llm,
            step_log=SAMPLE_STEP_LOG,
            source_name="prolific",
            source_url="https://prolific.com",
            flow_type="login",
        )

        assert len(result.steps) == 3
        assert result.steps[0].instruction == "Click the login button in the top-right corner"
        assert result.steps[1].expected_after == "Email field is populated"
        llm.generate.assert_awaited_once()

    async def test_sets_auto_metadata(self) -> None:
        llm = _make_llm(YAML_LLM_RESPONSE)

        result = await auto_generate_site_playbook(
            llm=llm,
            step_log=SAMPLE_STEP_LOG,
            source_name="prolific",
            source_url="https://prolific.com",
            flow_type="login",
        )

        assert result.verified_by == "auto"
        assert result.confidence == 0.0
        assert result.max_steps == len(result.steps) + 5

    async def test_passes_model_to_llm(self) -> None:
        llm = _make_llm(YAML_LLM_RESPONSE)

        await auto_generate_site_playbook(
            llm=llm,
            step_log=SAMPLE_STEP_LOG,
            source_name="prolific",
            source_url="https://prolific.com",
            flow_type="execution",
            model="gemma3:27b",
        )

        _, kwargs = llm.generate.call_args
        assert kwargs["model"] == "gemma3:27b"

    async def test_returns_empty_steps_on_llm_failure(self) -> None:
        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("Ollama down"))

        result = await auto_generate_site_playbook(
            llm=llm,
            step_log=SAMPLE_STEP_LOG,
            source_name="prolific",
            source_url="https://prolific.com",
            flow_type="login",
        )

        assert result.steps == []
        assert result.verified_by == "auto"
        assert result.max_steps == 20


class TestAutoGenerateHostPlaybook:
    async def test_produces_host_playbook(self) -> None:
        llm = _make_llm(HOST_YAML_RESPONSE)

        result = await auto_generate_host_playbook(
            llm=llm,
            step_log=SAMPLE_STEP_LOG,
            host_domain="qualtrics.com",
        )

        assert result.host_domain == "qualtrics.com"
        assert result.learned_from == "auto"
        assert result.confidence == 0.0
        assert "sidebar" in result.navigation
        assert len(result.quirks) == 2
        assert "Cookie consent" in result.quirks[0]

    async def test_handles_llm_failure_gracefully(self) -> None:
        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("Ollama down"))

        result = await auto_generate_host_playbook(
            llm=llm,
            step_log=SAMPLE_STEP_LOG,
            host_domain="qualtrics.com",
        )

        assert result.host_domain == "qualtrics.com"
        assert result.navigation == {}
        assert result.quirks == []


class TestParseStepsFromLLM:
    def test_handles_yaml(self) -> None:
        steps = _parse_steps_from_llm(YAML_LLM_RESPONSE)

        assert len(steps) == 3
        assert steps[0].seq == 1
        assert steps[0].instruction == "Click the login button in the top-right corner"
        assert steps[1].expected_after == "Email field is populated"
        assert steps[2].triggers_new_tab is False

    def test_handles_numbered_list(self) -> None:
        text = "1. Navigate to the login page\n2. Click the Sign In button\n3. Enter your password"
        steps = _parse_steps_from_llm(text)

        assert len(steps) == 3
        assert steps[0].seq == 1
        assert steps[0].instruction == "Navigate to the login page"
        assert steps[1].instruction == "Click the Sign In button"
        assert steps[2].instruction == "Enter your password"

    def test_returns_empty_on_garbage(self) -> None:
        garbage = "This is not valid YAML or a numbered list at all!!!"
        steps = _parse_steps_from_llm(garbage)

        assert steps == []

    def test_returns_empty_on_empty_string(self) -> None:
        steps = _parse_steps_from_llm("")
        assert steps == []


class TestCredentialFieldDetection:
    def test_login_instruction_sets_field(self) -> None:
        yaml_response = """\
- seq: 1
  instruction: Click the login button
  expected_after: Login form visible
"""
        steps = _parse_steps_from_llm(yaml_response)
        assert len(steps) == 1
        assert steps[0].field == "credential:login"

    def test_password_instruction_sets_field(self) -> None:
        yaml_response = """\
- seq: 1
  instruction: Enter your password in the password field
  expected_after: Password entered
"""
        steps = _parse_steps_from_llm(yaml_response)
        assert steps[0].field == "credential:password"

    def test_email_instruction_sets_field(self) -> None:
        yaml_response = """\
- seq: 1
  instruction: Type your email address
  expected_after: Email field populated
"""
        steps = _parse_steps_from_llm(yaml_response)
        assert steps[0].field == "credential:email"

    def test_non_credential_instruction_no_field(self) -> None:
        yaml_response = """\
- seq: 1
  instruction: Click the Start Survey button
  expected_after: Survey form displayed
"""
        steps = _parse_steps_from_llm(yaml_response)
        assert steps[0].field is None

    def test_explicit_field_not_overridden(self) -> None:
        yaml_response = """\
- seq: 1
  instruction: Enter your password
  expected_after: Password entered
  field: credential:master_password
"""
        steps = _parse_steps_from_llm(yaml_response)
        assert steps[0].field == "credential:master_password"

    def test_numbered_list_credential_detection(self) -> None:
        text = "1. Enter your username\n2. Click the submit button"
        steps = _parse_steps_from_llm(text)

        assert steps[0].field == "credential:username"
        assert steps[1].field is None
