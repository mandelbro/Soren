"""Tests for correction system."""

from unittest.mock import AsyncMock

import pytest

from soren.verification.correction import (
    CorrectionStrategy,
    apply_correction,
    escalate_corrections,
)


class TestCorrectionStrategies:
    """Correction strategy execution tests."""

    @pytest.mark.asyncio
    async def test_reprompt_returns_context_for_retry(self) -> None:
        """Level 1 REPROMPT returns success (caller uses context)."""
        mock_computer = AsyncMock()
        result = await apply_correction(CorrectionStrategy.REPROMPT, mock_computer, {"failure": "modal"})
        assert result is True
        mock_computer.run_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_keypress_sends_escape_and_enter(self) -> None:
        """Level 2 sends Escape + Enter keypresses."""
        mock_computer = AsyncMock()
        result = await apply_correction(CorrectionStrategy.KEYPRESS, mock_computer, {})
        assert result is True
        assert mock_computer.run_command.call_count >= 2
        calls = [str(c) for c in mock_computer.run_command.call_args_list]
        assert any("53" in c for c in calls)  # Escape key code
        assert any("36" in c for c in calls)  # Enter key code

    @pytest.mark.asyncio
    async def test_osascript_activate_sends_activation(self) -> None:
        """Level 3 sends osascript activation command."""
        mock_computer = AsyncMock()
        result = await apply_correction(CorrectionStrategy.OSASCRIPT_ACTIVATE, mock_computer, {})
        assert result is True
        mock_computer.run_command.assert_called()
        call_str = str(mock_computer.run_command.call_args)
        assert "Safari" in call_str
        assert "activate" in call_str

    @pytest.mark.asyncio
    async def test_full_osascript_sends_sequence(self) -> None:
        """Level 4 sends full osascript sequence."""
        mock_computer = AsyncMock()
        result = await apply_correction(
            CorrectionStrategy.FULL_OSASCRIPT,
            mock_computer,
            {"retype_text": "hello"},
        )
        assert result is True
        assert mock_computer.run_command.call_count >= 4

    @pytest.mark.asyncio
    async def test_escalation_stops_at_first_success(self) -> None:
        """Escalation stops at first successful correction."""
        mock_computer = AsyncMock()
        success, strategy = await escalate_corrections(mock_computer, {})
        assert success is True
        assert strategy == CorrectionStrategy.REPROMPT
        mock_computer.run_command.assert_not_called()
