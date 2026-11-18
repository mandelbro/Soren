"""CredentialHandoff — execution-time login details for the agent, from the persona's credential_ref.

Extracted from TaskExecutor (the discovery doc's "credential handoff sharing"
decision) so training can reuse it. Credentials stay persona-bound: the task's
source is looked up on the persona, and its ``credential_ref`` is resolved
from the encrypted store. The store is opened lazily on first use.
"""

from __future__ import annotations

from pathlib import Path

from soren.models.persona import Persona
from soren.models.task import Task
from soren.services.credentials_service import CredentialStore

DEFAULT_CREDENTIALS_PATH = Path("config/credentials.enc")
DEFAULT_KEY_PATH = Path("data/credentials.key")


class CredentialHandoff:
    """Builds the credential block appended to the agent's memories."""

    def __init__(self, store: CredentialStore | None = None) -> None:
        self._store = store

    def compose_memories(self, task: Task, persona: Persona, memories: list[str] | None) -> list[str]:
        """Merge recalled memories with explicit credential handoff instructions."""
        combined = list(memories or [])
        credential_handoff = self.build(task, persona)
        if credential_handoff:
            combined.append(credential_handoff)
        return combined

    def build(self, task: Task, persona: Persona) -> str | None:
        """Build execution-time login instructions from source credential_ref."""
        source_ref = ""
        for source in persona.task_sources:
            if source.name == task.source_name:
                source_ref = source.credential_ref.strip()
                break
        if not source_ref:
            return None

        if self._store is None:
            try:
                self._store = CredentialStore(DEFAULT_CREDENTIALS_PATH, key_path=DEFAULT_KEY_PATH)
            except Exception:
                return (
                    "Credential handoff:\n"
                    f"- credential_ref: {source_ref}\n"
                    "- Encrypted credential store failed to initialize.\n"
                    "- If login is required, stop and report login_failure."
                )

        try:
            cred = self._store.get(source_ref)
        except (KeyError, ValueError):
            return (
                "Credential handoff:\n"
                f"- credential_ref: {source_ref}\n"
                "- Encrypted credential was not available at runtime.\n"
                "- If login is required, stop and report login_failure."
            )

        return (
            "Credential handoff (sensitive; do not echo in outputs):\n"
            f"- credential_ref: {source_ref}\n"
            f"- username: {cred.username}\n"
            f"- password: {cred.password}\n"
            "- Use only for required login actions, then continue task execution."
        )
