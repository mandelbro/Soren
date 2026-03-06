"""Four-level correction escalation for task failures."""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol


class CorrectionStrategy(str, Enum):
    """Correction strategy levels."""

    REPROMPT = "reprompt"
    KEYPRESS = "keypress"
    OSASCRIPT_ACTIVATE = "osascript_activate"
    FULL_OSASCRIPT = "full_osascript"


class ComputerContext(Protocol):
    """Protocol for computer/VM interface used by corrections."""

    async def run_command(self, cmd: str) -> Any:
        """Run command in VM (e.g. osascript)."""
        ...


# Key codes: 53 = Escape, 36 = Enter
OSASCRIPT_ESCAPE = 'tell application "System Events" to key code 53'
OSASCRIPT_ENTER = 'tell application "System Events" to key code 36'
OSASCRIPT_ACTIVATE_SAFARI = 'tell application "Safari" to activate'
OSASCRIPT_SELECT_ALL = 'tell application "System Events" to keystroke "a" using command down'
OSASCRIPT_DELETE = 'tell application "System Events" to key code 51'


async def apply_correction(
    strategy: CorrectionStrategy,
    computer: ComputerContext,
    context: dict[str, Any],
) -> bool:
    """Execute correction strategy. Returns True if applied successfully."""
    if strategy == CorrectionStrategy.REPROMPT:
        return True  # Caller uses context for retry; no VM action

    if strategy == CorrectionStrategy.KEYPRESS:
        try:
            await computer.run_command(f"osascript -e '{OSASCRIPT_ESCAPE}'")
            await computer.run_command(f"osascript -e '{OSASCRIPT_ENTER}'")
            return True
        except Exception:
            return False

    if strategy == CorrectionStrategy.OSASCRIPT_ACTIVATE:
        try:
            await computer.run_command(f"osascript -e '{OSASCRIPT_ACTIVATE_SAFARI}'")
            last_input = context.get("last_input", "")
            if last_input:
                escaped = last_input.replace("'", "'\"'\"'")
                await computer.run_command(
                    f'osascript -e \'tell application "System Events" to keystroke "{escaped}"\''
                )
            return True
        except Exception:
            return False

    if strategy == CorrectionStrategy.FULL_OSASCRIPT:
        try:
            await computer.run_command(f"osascript -e '{OSASCRIPT_ACTIVATE_SAFARI}'")
            await computer.run_command(f"osascript -e '{OSASCRIPT_SELECT_ALL}'")
            await computer.run_command(f"osascript -e '{OSASCRIPT_DELETE}'")
            new_text = context.get("retype_text", "")
            if new_text:
                escaped = new_text.replace("'", "'\"'\"'")
                await computer.run_command(
                    f'osascript -e \'tell application "System Events" to keystroke "{escaped}"\''
                )
            await computer.run_command(f"osascript -e '{OSASCRIPT_ENTER}'")
            return True
        except Exception:
            return False

    return False


async def escalate_corrections(
    computer: ComputerContext,
    context: dict[str, Any],
) -> tuple[bool, CorrectionStrategy | None]:
    """Try each correction level in order. Returns (success, strategy_used)."""
    for strategy in CorrectionStrategy:
        if await apply_correction(strategy, computer, context):
            return True, strategy
    return False, None
