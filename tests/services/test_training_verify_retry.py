"""Training verify+retry: auto-verification, retries, and the human fallback when stuck."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.services.training_session import TrainingSession, TrainingState
from tests.services.training_helpers import (
    PATCH_APPLY_ALL,
    PATCH_CUA_AGENT,
    make_cua_result,
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


class TestAutoVerifyPassRecordsStep:
    """Verification passes on first try — step is auto-recorded without human confirm."""

    async def test_pass_first_try_records_step(self) -> None:
        instructions = iter([("Click login", "Login modal visible"), ("/done", "")])
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

        verifier = mock_verifier([mock_verification_result(passed=True)])
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 1
        assert result.steps[0].instruction == "Click login"
        assert result.steps[0].expected_after == "Login modal visible"
        # confirm_step should NOT be called — verification is autonomous
        cb.confirm_step.assert_not_called()
        # Verification result was shown
        cb.show_verification_result.assert_called_once()
        assert session.state == TrainingState.DONE


class TestAutoVerifyFailRetries:
    """Verification fails on attempt 1, passes on attempt 2 — step recorded after retry."""

    async def test_retry_succeeds_second_attempt(self) -> None:
        instructions = iter([("Click login", "Login modal visible"), ("/done", "")])
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
            max_verify_retries=3,
        )

        verifier = mock_verifier(
            [
                mock_verification_result(passed=False, reasoning="Page not loaded yet"),
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
        # show_verification_result called twice (fail then pass)
        assert cb.show_verification_result.call_count == 2
        # Auto-retry message was shown
        cb.show_message.assert_any_call("Verification failed. Auto-retrying (1/3)...")


class TestRetriesExhaustedHumanSkip:
    """All 3 verification attempts fail, human chooses skip — step NOT recorded."""

    async def test_human_skip_after_exhausted_retries(self) -> None:
        instructions = iter([("Click login", "Login modal visible"), ("/done", "")])
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            handle_stuck=AsyncMock(return_value="skip"),
        )

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
            max_verify_retries=3,
        )

        fail_result = mock_verification_result(passed=False, reasoning="Wrong page")
        verifier = mock_verifier([fail_result, fail_result, fail_result])
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 0  # Step was skipped
        cb.handle_stuck.assert_called_once_with(
            instruction="Click login",
            expected_after="Login modal visible",
            attempts=3,
            last_reasoning="Wrong page",
        )


class TestRetriesExhaustedHumanAccept:
    """All retries fail, human chooses accept — step IS recorded as unverified."""

    async def test_human_accept_records_unverified(self) -> None:
        instructions = iter([("Click login", "Login modal visible"), ("/done", "")])
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            handle_stuck=AsyncMock(return_value="accept"),
        )

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
            max_verify_retries=2,
        )

        fail_result = mock_verification_result(passed=False)
        verifier = mock_verifier([fail_result, fail_result])
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 1
        assert result.steps[0].instruction == "Click login"
        # Message should indicate unverified
        cb.show_message.assert_any_call("Step 1 recorded (unverified): Click login")


class TestRetriesExhaustedHumanAbort:
    """All retries fail, human chooses abort — session aborts, nothing saved."""

    async def test_human_abort_returns_none(self) -> None:
        instructions = iter([("Click login", "Login modal visible")])
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            handle_stuck=AsyncMock(return_value="abort"),
        )
        ps = mock_playbook_service()

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=ps,
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
            max_verify_retries=1,
        )

        fail_result = mock_verification_result(passed=False)
        verifier = mock_verifier([fail_result])
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            result = await session.run()

        assert result is None
        ps.save_flow.assert_not_called()


class TestErrorDuringCuaOffersRetry:
    async def test_cua_error_shows_error_and_offers_retry(self) -> None:
        instructions = iter([("Click button", "Button clicked"), ("/done", "")])
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            # _try_execute_step asks get_instruction for retry/skip on CUA error
            get_instruction=AsyncMock(return_value=""),  # Enter = retry
        )
        ps = mock_playbook_service()
        call_count = 0

        async def failing_then_succeeding_run(messages):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("CUA crash")
            yield make_cua_result()

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=ps,
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="login",
        )

        verifier = mock_verifier([mock_verification_result(passed=True)])
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            mock_inst = MagicMock()
            mock_inst.run = MagicMock(side_effect=failing_then_succeeding_run)
            MockAgent.return_value = mock_inst
            result = await session.run()

        assert result is not None
        assert cb.show_error.call_count >= 1
