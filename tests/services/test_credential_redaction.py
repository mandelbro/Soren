"""Credentials never reach a cloud model's prompt (RFC §Local or Ollama Cloud Models).

Two layers: the redactor removes credential values from execution data where it
is recorded, and LLMService refuses any cloud-bound prompt that still contains a
known credential (the backstop).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.llm import LLMConfig
from soren.services.config_loader import SorenSettings
from soren.services.credential_redaction import CredentialLeakError, CredentialRedactor, register_secrets
from soren.services.llm_service import LLMService

PASSWORD = "hunter2-Secret!"
USERNAME = "analyst@example.com"


class TestCredentialRedactor:
    def test_typed_password_in_action_data_is_replaced_with_placeholder(self) -> None:
        redactor = CredentialRedactor({"username": USERNAME, "password": PASSWORD})
        entry = {"step": 3, "action_type": "type", "action_data": {"type": "type", "text": PASSWORD}}

        redacted = redactor.redact(entry)

        assert redacted["action_data"]["text"] == "{credential:password}"
        assert redacted["step"] == 3

    def test_secret_inside_reasoning_text_is_replaced(self) -> None:
        redactor = CredentialRedactor({"username": USERNAME, "password": PASSWORD})

        redacted = redactor.redact(f"I'll log in as {USERNAME} and type {PASSWORD} into the field")

        assert redacted == "I'll log in as {credential:username} and type {credential:password} into the field"

    def test_nested_lists_and_dicts_are_redacted(self) -> None:
        redactor = CredentialRedactor({"password": PASSWORD})

        redacted = redactor.redact({"a": [{"b": f"x{PASSWORD}y"}, (PASSWORD,)]})

        assert PASSWORD not in repr(redacted)

    def test_longer_secret_wins_when_one_contains_the_other(self) -> None:
        """A password that contains the username must not be left half-redacted."""
        redactor = CredentialRedactor({"username": "analyst", "password": "analyst2026!"})

        assert redactor.redact("typed analyst2026!") == "typed {credential:password}"

    def test_input_is_not_mutated(self) -> None:
        redactor = CredentialRedactor({"password": PASSWORD})
        entry = {"action_data": {"text": PASSWORD}}

        redactor.redact(entry)

        assert entry["action_data"]["text"] == PASSWORD

    @pytest.mark.parametrize("value", ["", "  ", "ab"])
    def test_blank_or_tiny_values_are_ignored(self, value: str) -> None:
        """Values under 3 characters would garble every log line; they are not treated as secrets."""
        redactor = CredentialRedactor({"password": value})

        assert redactor.redact("about a bad abacus") == "about a bad abacus"

    def test_non_string_scalars_pass_through(self) -> None:
        redactor = CredentialRedactor({"password": PASSWORD})

        assert redactor.redact({"x": 10, "y": None, "z": True}) == {"x": 10, "y": None, "z": True}


class TestCloudPromptBackstop:
    def _llm(self) -> LLMService:
        return LLMService(SorenSettings(llm=LLMConfig(primary_model="gemma4:31b-cloud")))

    async def test_cloud_model_refuses_prompt_containing_a_registered_secret(self) -> None:
        register_secrets([PASSWORD])
        llm = self._llm()
        with patch.object(llm._client, "generate", new_callable=AsyncMock) as send:
            with pytest.raises(CredentialLeakError) as excinfo:
                await llm.generate(f"Summarize: typed {PASSWORD}", model="gemma4:31b-cloud")

        send.assert_not_called()
        assert PASSWORD not in str(excinfo.value)  # the error must not leak the secret either

    async def test_structured_cloud_call_is_guarded_too(self) -> None:
        register_secrets([PASSWORD])
        llm = self._llm()
        with patch.object(llm._client, "generate", new_callable=AsyncMock) as send:
            with pytest.raises(CredentialLeakError):
                await llm.generate_structured(f"typed {PASSWORD}", MagicMock(), model="qwen3-vl:235b-cloud")

        send.assert_not_called()

    async def test_local_model_may_receive_credentials(self) -> None:
        """Local models are allowed to see credentials (UI-TARS needs them to log in)."""
        register_secrets([PASSWORD])
        llm = self._llm()
        with patch.object(llm._client, "generate", new_callable=AsyncMock) as send:
            send.return_value = MagicMock(response="ok")
            assert await llm.generate(f"typed {PASSWORD}", model="gemma3:12b") == "ok"

    async def test_cloud_model_accepts_prompt_without_secrets(self) -> None:
        register_secrets([PASSWORD])
        llm = self._llm()
        with patch.object(llm._client, "generate", new_callable=AsyncMock) as send:
            send.return_value = MagicMock(response="ok")
            assert await llm.generate("typed {credential:password}", model="gemma4:31b-cloud") == "ok"
