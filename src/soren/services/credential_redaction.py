"""Keep decrypted credentials out of anything sent to a cloud model.

RFC §Local or Ollama Cloud Models: credentials may appear only in prompts to
locally running models (UI-TARS needs them to log in). Two layers enforce it:

- ``CredentialRedactor`` replaces credential values in execution data (step
  logs, reasoning) with ``{credential:<field>}`` where that data is recorded,
  so every later reader (write-back, auto-playbooks, reports, memory) is clean.
  Passwords match anywhere (a false alarm is safe); usernames match only as
  whole tokens, so a short one like ``sam`` leaves "same" alone.
- A process-wide registry of handed-off passwords lets ``LLMService`` refuse
  any cloud-bound prompt that still contains one (the backstop). Usernames are
  not registered: the RFC allows them on screen, so they can legitimately reach
  a cloud model through screenshots and text extracted from them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

# Shorter values would match ordinary words and garble every log line.
MIN_SECRET_LENGTH = 3

_REGISTERED: set[str] = set()

# Fields matched only as whole tokens (not inside a longer word or address), and kept out of the backstop.
_TOKEN_FIELDS = frozenset({"username"})


class CredentialLeakError(RuntimeError):
    """A cloud-bound prompt contained a known credential. Never carries the value itself."""


def _usable(value: object) -> bool:
    return isinstance(value, str) and len(value.strip()) >= MIN_SECRET_LENGTH


def _pattern(field: str, value: str) -> re.Pattern[str]:
    escaped = re.escape(value)
    if field in _TOKEN_FIELDS:
        # Not inside a longer word/address; a sentence-ending "." after it is fine.
        return re.compile(rf"(?<![\w@.]){escaped}(?!\.?[\w@])")
    return re.compile(escaped)


class CredentialRedactor:
    """Replaces credential values (``{field: value}``) with ``{credential:<field>}`` placeholders."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        usable = [(field, value) for field, value in secrets.items() if _usable(value)]
        # Longest first, so a password that contains the username is replaced whole.
        usable.sort(key=lambda item: len(item[1]), reverse=True)
        self._rules = [(_pattern(field, value), f"{{credential:{field}}}") for field, value in usable]
        self._backstop = [value for field, value in usable if field not in _TOKEN_FIELDS]

    @property
    def backstop_values(self) -> list[str]:
        """Values that must never reach a cloud model in any form (passwords), for ``register_secrets``."""
        return list(self._backstop)

    def redact(self, data: Any) -> Any:
        """A copy of ``data`` (str, dict, list, tuple, nested) with every credential value replaced."""
        if not self._rules:
            return data
        if isinstance(data, str):
            for pattern, placeholder in self._rules:
                data = pattern.sub(lambda _m, p=placeholder: p, data)
            return data
        if isinstance(data, dict):
            return {key: self.redact(item) for key, item in data.items()}
        if isinstance(data, list):
            return [self.redact(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.redact(item) for item in data)
        return data


def register_secrets(values: Iterable[str]) -> None:
    """Remember passwords handed to an agent so cloud-bound prompts can be checked for them."""
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
