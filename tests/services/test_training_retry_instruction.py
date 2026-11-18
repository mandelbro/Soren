"""Retry instructions carry verifier feedback and chrome-aware prompts to UI-TARS."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from soren.services.training_session import TrainingSession
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


class TestRetryInstructionIncludesVerifierFeedback:
    """Regression test: retries must feed the verifier's failure reasoning back
    to UI-TARS so the model has a signal to pick a different element.

    Before this change, retries re-used the identical instruction on every
    attempt, and UI-TARS would lock onto the same (wrong) coordinates — the
    retry logic was effectively a no-op against deterministic models.
    """

    async def test_second_attempt_instruction_carries_verifier_reasoning(self) -> None:
        instructions = iter([("Click the Log in button", "Login modal visible"), ("/done", "")])
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

        verifier_reasoning = (
            "The screenshot shows the homepage with a 'LOG IN OR SIGN UP' text "
            "but no modal with input fields. That text is part of the page content, "
            "not a modal overlay."
        )
        verifier = mock_verifier(
            [
                mock_verification_result(passed=False, reasoning=verifier_reasoning),
                mock_verification_result(passed=True),
            ]
        )

        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            await session.run()

        # Two ComputerAgent constructions expected: one per attempt.
        assert MockAgent.call_count == 2, (
            f"Expected 2 ComputerAgent constructions (1 original + 1 retry); got {MockAgent.call_count}"
        )

        # Attempt 1 (index 0): the original instruction only — no feedback.
        attempt1_kwargs = MockAgent.call_args_list[0].kwargs
        attempt1_instructions = attempt1_kwargs.get("instructions", "")
        assert "Click the Log in button" in attempt1_instructions
        assert "LOG IN OR SIGN UP" not in attempt1_instructions, (
            "First attempt should not carry verifier feedback (there is none yet)"
        )

        # Attempt 2 (index 1): instructions must carry the verifier reasoning
        # AND the original instruction AND a clear "this is a retry" framing.
        attempt2_kwargs = MockAgent.call_args_list[1].kwargs
        attempt2_instructions = attempt2_kwargs.get("instructions", "")
        assert "Click the Log in button" in attempt2_instructions, "Retry must still include the original instruction"
        assert "LOG IN OR SIGN UP" in attempt2_instructions, (
            "Retry instructions must include the verifier's failure reasoning"
        )
        # Retry should also clearly signal that previous attempt failed
        assert "retry" in attempt2_instructions.lower() or "previous" in attempt2_instructions.lower(), (
            "Retry instructions should clearly frame this as a correction of a prior attempt"
        )

    async def test_retry_includes_last_click_coordinates(self) -> None:
        """Retry feedback must carry the exact (x, y) of the previous failed click.

        Generic reasoning ("modal didn't appear") is weaker than concrete data
        ("you clicked at (100, 200) which is in Safari's toolbar"). Without the
        actual coords in the retry prompt, UI-TARS has no way to understand
        which pixel was wrong.
        """
        instructions = iter([("Click the Log in button", "Login modal visible"), ("/done", "")])
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
                mock_verification_result(passed=False, reasoning="No modal visible"),
                mock_verification_result(passed=True),
            ]
        )

        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            await session.run()

        # make_cua_plan() emits click x=100 y=200; retry (call index 1) must cite those
        retry_instructions = MockAgent.call_args_list[1].kwargs.get("instructions", "")
        assert "100" in retry_instructions and "200" in retry_instructions, (
            f"Retry instructions should mention last click coords (100, 200); got: {retry_instructions!r}"
        )

    async def test_system_prompt_warns_about_safari_chrome_when_crop_disabled(self, monkeypatch) -> None:
        """CUA system prompt must tell UI-TARS about Safari's browser chrome layout
        WHEN THE CROP IS DISABLED.

        Ground-truth screenshot showed UI-TARS was clicking at y~85 (inside
        Safari's toolbar) when the real target was at y~115 (inside the
        Acme Panel page header). The model conflates 'top of screen' with
        'page header', and needs an explicit chrome boundary to break the
        conflation — but only when it's actually seeing the chrome.
        """
        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)

        instructions = iter([("Click Log in", "Modal visible"), ("/done", "")])
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
            await session.run()

        call_instructions = MockAgent.call_args_list[0].kwargs.get("instructions", "")
        # The chrome warning must mention Safari AND a concrete y boundary
        lowered = call_instructions.lower()
        assert "safari" in lowered, f"System prompt missing Safari chrome note: {call_instructions!r}"
        assert "chrome" in lowered or "toolbar" in lowered, (
            f"System prompt should mention browser chrome or toolbar: {call_instructions!r}"
        )
        # Must express the chrome/webpage boundary numerically so the model can ground it
        assert any(token in call_instructions for token in ("y=", "y >", "y<", "pixels", "px")), (
            f"System prompt should give UI-TARS a concrete numeric boundary: {call_instructions!r}"
        )

    async def test_chrome_prompt_omitted_when_crop_active(self, monkeypatch) -> None:
        """When SOREN_CHROME_CROP_Y is set, UI-TARS sees a webpage-only image
        with no Safari chrome. The chrome prompt's screen-space coordinates
        ('webpage starts at y=100') are actively wrong in that case — they'd
        tell the model to skip the webpage header (which, in the cropped image,
        is at y=0-60). So the prompt must be OMITTED when crop is active.
        """
        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "100")

        instructions = iter([("Click Log in", "Modal visible"), ("/done", "")])
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
            await session.run()

        call_instructions = MockAgent.call_args_list[0].kwargs.get("instructions", "")
        # The chrome-specific guidance must NOT be in the prompt when crop is active.
        # We check for the distinctive phrasing from _SAFARI_CHROME_PROMPT rather
        # than generic words like "safari" (which may legitimately appear in task
        # instructions written by the operator).
        assert "CONTEXT: You are operating a macOS virtual machine with Safari" not in call_instructions, (
            f"Chrome prompt should be omitted when crop is active; got: {call_instructions!r}"
        )
        assert "NEVER click at y<100" not in call_instructions, (
            "Screen-space chrome guidance is wrong when UI-TARS sees a cropped image — "
            "it would tell the model to skip the webpage header"
        )
        # The user's actual instruction must still be present
        assert "Click Log in" in call_instructions, (
            f"Original instruction must still be in the prompt: {call_instructions!r}"
        )

    async def test_third_attempt_carries_latest_reasoning_not_stale(self) -> None:
        """Each retry carries the MOST RECENT verifier reasoning, not the first one."""
        instructions = iter([("Click login", "Modal visible"), ("/done", "")])
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
                mock_verification_result(passed=False, reasoning="FIRST_REASON_hit_nav_bar"),
                mock_verification_result(passed=False, reasoning="SECOND_REASON_hit_hero"),
                mock_verification_result(passed=True),
            ]
        )

        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            await session.run()

        assert MockAgent.call_count == 3
        attempt3_instructions = MockAgent.call_args_list[2].kwargs.get("instructions", "")
        # Third attempt must use SECOND reason (the most recent failure), not FIRST
        assert "SECOND_REASON_hit_hero" in attempt3_instructions
        assert "FIRST_REASON_hit_nav_bar" not in attempt3_instructions, (
            "Retry should carry only the most recent verifier reasoning, not a growing log"
        )
