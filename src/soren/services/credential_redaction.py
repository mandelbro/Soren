"""Keep decrypted credentials out of anything sent to a cloud model.

RFC §Local or Ollama Cloud Models: credentials may appear only in prompts to
locally running models (UI-TARS needs them to log in). Two layers enforce it:

- ``CredentialRedactor`` replaces credential values in execution data (step
  logs, reasoning) with ``{credential:<field>}`` where that data is recorded,
  so every later reader (write-back, auto-playbooks, reports, memory) is clean.
- A process-wide registry of handed-off values lets ``LLMService`` refuse any
  cloud-bound prompt that still contains one (the backstop).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Shorter values would match ordinary words and garble every log line.
MIN_SECRET_LENGTH = 3

_REGISTERED: set[str] = set()


class CredentialLeakError(RuntimeError):
    """A cloud-bound prompt contained a known credential. Never carries the value itself."""


def _usable(value: object) -> bool:
    return isinstance(value, str) and len(value.strip()) >= MIN_SECRET_LENGTH


class CredentialRedactor:
    """Replaces credential values (``{field: value}``) with ``{credential:<field>}`` placeholders."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        pairs = [(value, f"{{credential:{field}}}") for field, value in secrets.items() if _usable(value)]
        # Longest first, so a password that contains the username is replaced whole.
        self._pairs = sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)

    @property
    def values(self) -> list[str]:
        return [value for value, _ in self._pairs]

    def redact(self, data: Any) -> Any:
        """A copy of ``data`` (str, dict, list, tuple, nested) with every credential value replaced."""
        if not self._pairs:
            return data
        if isinstance(data, str):
            for value, placeholder in self._pairs:
                data = data.replace(value, placeholder)
            return data
        if isinstance(data, dict):
            return {key: self.redact(item) for key, item in data.items()}
        if isinstance(data, list):
            return [self.redact(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.redact(item) for item in data)
        return data


def register_secrets(values: Iterable[str]) -> None:
    """Remember credential values handed to an agent so cloud-bound prompts can be checked for them."""
    _REGISTERED.update(value for value in values if _usable(value))


def contains_registered_secret(text: str) -> bool:
    return any(value in text for value in _REGISTERED)


def ensure_safe_for_cloud(prompt: str, model: str) -> None:
    """Raise CredentialLeakError if ``prompt`` contains a registered credential. Call only for cloud models."""
    if contains_registered_secret(prompt):
        raise CredentialLeakError(
            f"Refusing to send a prompt containing a credential to cloud model {model!r}; "
            "credentials must be redacted from execution data before it reaches a cloud model."
        )
