"""Types shared by the training session and its callers: callbacks protocol, states, abort signal."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Protocol


class TrainingAbortRequested(Exception):
    """Raised inside the verify/retry loop when the human chooses abort."""


class TrainingCallbacks(Protocol):
    """I/O callbacks decoupled from CLI for testability."""

    async def show_screenshot(self, screenshot: bytes, vlm_description: str) -> None: ...
    async def get_instruction(self) -> str: ...
    async def confirm_step(self, screenshot: bytes, vlm_description: str) -> str: ...
    async def get_expected_after(self, vlm_suggestion: str) -> str: ...
    async def show_message(self, message: str) -> None: ...
    async def show_error(self, error: str) -> None: ...

    # --- Autonomous verify+retry callbacks ---

    async def get_instruction_with_expected(self) -> tuple[str, str]:
        """Collect instruction AND expected outcome before execution."""
        ...

    async def show_verification_result(
        self,
        attempt: int,
        max_attempts: int,
        passed: bool,
        confidence: float,
        reasoning: str | None,
    ) -> None:
        """Display auto-verification result."""
        ...

    async def handle_stuck(
        self,
        instruction: str,
        expected_after: str,
        attempts: int,
        last_reasoning: str | None,
    ) -> str:
        """Human fallback when all retries exhausted.

        Returns one of: 'skip', 'accept', 'abort', or 'retry:<new_instruction>'.
        """
        ...

    async def confirm_service_update(self, path: Path, changes: list[str], warnings: list[str]) -> bool:
        """Ask whether to write the staged service changes to ``path`` (ADR-0002 Phase 4)."""
        ...


class TrainingState(str, Enum):
    """States for the training session state machine."""

    INIT = "init"
    PREREQ_REPLAY = "prereq_replay"
    NAVIGATE = "navigate"
    STEP_LOOP = "step_loop"
    FINALIZING = "finalizing"
    SAVING = "saving"
    DONE = "done"
    ERROR = "error"
