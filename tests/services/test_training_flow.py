"""TrainingSession flow: state machine, slash commands, saving, and lazy verifier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.services.training_session import TrainingSession, TrainingState
from tests.services.training_helpers import (
    PATCH_APPLY_ALL,
    PATCH_CUA_AGENT,
    mock_callbacks,
    mock_llm,
    mock_playbook_service,
    mock_verification_result,
    mock_verifier,
    mock_vm,
    patched_cua,
)

# Unit tests: fake CUA SDK + virtual clock (tests/conftest.py, tests/services/conftest.py).
pytestmark = pytest.mark.usefixtures("fake_cua_sdk", "training_virtual_clock")


class TestThreeStepsVerifiedAndSaved:
    """Full loop: three instructions verified, playbook saved with all steps."""

    async def test_records_three_verified_steps(self) -> None:
        instructions = iter(
            [
                ("Click login", "Login modal visible"),
                ("Enter email", "Email in field"),
                ("Click submit", "Dashboard visible"),
                ("/done", ""),
            ]
        )
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
        )

        verifier = mock_verifier(
            [
                mock_verification_result(passed=True),
                mock_verification_result(passed=True),
                mock_verification_result(passed=True),
            ]
        )
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 3
        assert result.verified_by == "human"
        assert result.confidence == 0.5
        assert session.state == TrainingState.DONE


class TestSlashCommandsStillWork:
    """Slash commands (/done, /abort, /undo, /steps, /help) work identically."""

    async def test_done_immediately_saves(self) -> None:
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )
        ps = mock_playbook_service()

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=ps,
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 0
        ps.save_flow.assert_called_once()

    async def test_abort_returns_none(self) -> None:
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/abort", "")),
        )
        ps = mock_playbook_service()

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=ps,
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            result = await session.run()

        assert result is None
        ps.save_flow.assert_not_called()

    async def test_undo_after_verified_step(self) -> None:
        instructions = iter(
            [
                ("Click A", "Result A"),
                ("Click B", "Result B"),
                ("/undo", ""),
                ("/done", ""),
            ]
        )
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
        )

        verifier = mock_verifier(
            [
                mock_verification_result(passed=True),
                mock_verification_result(passed=True),
            ]
        )
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 1
        assert result.steps[0].instruction == "Click A"

    async def test_help_shows_text(self) -> None:
        instructions = iter([("/help", ""), ("/done", "")])
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            await session.run()

        help_calls = [c for c in cb.show_message.call_args_list if "/done" in str(c)]
        assert len(help_calls) >= 1


class TestStateTransitions:
    async def test_login_flow_reaches_done(self) -> None:
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="test",
            flow_type="login",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            await session.run()

        assert session.state == TrainingState.DONE

    async def test_discovery_flow_includes_prereq(self) -> None:
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )
        ps = mock_playbook_service()
        ps.load_site = MagicMock(return_value=None)

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=ps,
            llm=mock_llm(),
            callbacks=cb,
            source_name="test",
            flow_type="discovery",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            result = await session.run()

        assert result is not None
        assert session.state == TrainingState.DONE
        no_login_msgs = [c for c in cb.show_message.call_args_list if "No login playbook" in str(c)]
        assert len(no_login_msgs) >= 1


class TestVerifierLazyConstruction:
    """ActionVerifier should NOT be created when only slash commands are used."""

    async def test_no_verifier_on_immediate_done(self) -> None:
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="test",
            flow_type="login",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            await session.run()

        assert session._verifier is None


class TestStepExecutionSeam:
    """All step execution (step loop and login replay) goes through TrainingSession._execute_single_step."""

    async def test_step_loop_executes_through_session_delegator(self) -> None:
        instructions = iter([("Click login", "Modal visible"), ("/done", "")])
        cb = mock_callbacks(get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)))
        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
        )
        delegate = AsyncMock(return_value=(b"png", "A modal"))
        with (
            patch.object(session, "_execute_single_step", delegate),
            patch.object(session, "_get_verifier", return_value=mock_verifier()),
        ):
            await session.run()
        delegate.assert_awaited_once()
        assert delegate.await_args.args[0] == "Click login"
