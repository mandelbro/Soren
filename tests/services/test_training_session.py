"""Tests for TrainingSession orchestrator with autonomous verify+retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from soren.models.site_playbook import LoginFlow, PlaybookStep, SiteFlows, SitePlaybook
from soren.services.training_session import TrainingCallbacks, TrainingSession, TrainingState

# Patch targets: apply_all and ComputerAgent are imported lazily inside _execute_single_step.
_PATCH_APPLY_ALL = "soren.patches.apply_all"
_PATCH_CUA_AGENT = "cua_agent.ComputerAgent"
_PATCH_VERIFIER = "soren.services.training_session.TrainingSession._get_verifier"


# --- Helpers ---


def _mock_verification_result(passed: bool = True, confidence: float = 0.9, reasoning: str | None = None):
    """Create a mock VerificationResult."""
    vr = MagicMock()
    vr.passed = passed
    vr.confidence = confidence
    vr.tier_used = 2
    vr.reasoning = reasoning
    vr.duration_ms = 10.0
    return vr


def _mock_verifier(results: list | None = None):
    """Create a mock ActionVerifier that returns results in sequence.

    After the explicit results are consumed, returns a default passing result
    to avoid StopAsyncIteration on unexpected extra calls.
    """
    verifier = AsyncMock()
    if results is None:
        results = [_mock_verification_result(passed=True)]
    result_iter = iter(results)
    default = _mock_verification_result(passed=True)

    async def _verify_side_effect(*args, **kwargs):
        return next(result_iter, default)

    verifier.verify = AsyncMock(side_effect=_verify_side_effect)
    return verifier


def _mock_callbacks(**overrides: AsyncMock) -> TrainingCallbacks:
    """Create a mock callbacks object implementing the protocol."""
    cb = MagicMock(spec=TrainingCallbacks)
    cb.show_screenshot = overrides.get("show_screenshot", AsyncMock())
    cb.get_instruction = overrides.get("get_instruction", AsyncMock(return_value="/done"))
    cb.confirm_step = overrides.get("confirm_step", AsyncMock(return_value="y"))
    cb.get_expected_after = overrides.get("get_expected_after", AsyncMock(return_value="Screen looks correct"))
    cb.show_message = overrides.get("show_message", AsyncMock())
    cb.show_error = overrides.get("show_error", AsyncMock())
    # Autonomous verify+retry callbacks
    cb.get_instruction_with_expected = overrides.get(
        "get_instruction_with_expected",
        AsyncMock(return_value=("/done", "")),
    )
    cb.show_verification_result = overrides.get("show_verification_result", AsyncMock())
    cb.handle_stuck = overrides.get("handle_stuck", AsyncMock(return_value="skip"))
    return cb


def _mock_vm() -> AsyncMock:
    vm = AsyncMock()
    vm.ensure_ready = AsyncMock()
    computer = AsyncMock()
    computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
    vm.connect_computer = AsyncMock(return_value=computer)
    vm.prepare_session_environment = AsyncMock()
    return vm


def _mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value="A screen with a form")
    return llm


def _mock_playbook_service() -> AsyncMock:
    ps = AsyncMock()
    ps.load_site = MagicMock(return_value=None)
    ps.save_flow = MagicMock()
    return ps


def _make_cua_plan() -> dict:
    """Build the first SDK yield: VLM planned action (computer_call).

    Mirrors the canonical CUA SDK shape: ``action`` is a sibling of ``type`` at
    the top level of the item (see ``agent/responses.py::make_click_item``).
    """
    return {
        "output": [
            {
                "type": "computer_call",
                "action": {"type": "click", "x": 100, "y": 200, "button": "left"},
            }
        ]
    }


def _make_cua_result(screenshot_b64: str = "ZmFrZQ==") -> dict:
    """Build the second SDK yield: action result with screenshot (computer_call_output)."""
    return {
        "output": [
            {
                "type": "computer_call_output",
                "output": {"image_url": f"data:image/png;base64,{screenshot_b64}"},
            }
        ]
    }


def _make_cua_completion() -> dict:
    """Build the final SDK yield: assistant completion message."""
    return {
        "output": [
            {
                "role": "assistant",
                "content": "Action completed successfully.",
            }
        ]
    }


async def _fake_agent_run(messages: list) -> list[dict]:  # noqa: ARG001
    """Async generator mimicking actual CUA SDK: plan -> execute (screenshot) -> assistant done."""
    yield _make_cua_plan()
    yield _make_cua_result()
    yield _make_cua_completion()


def _patched_cua():
    """Return a mock ComputerAgent whose run() accurately mimics the SDK two-phase generator."""
    mock_agent_inst = MagicMock()
    mock_agent_inst.run = MagicMock(side_effect=lambda m: _fake_agent_run(m))
    return mock_agent_inst


# --- Tests: Autonomous Verify+Retry ---


class TestAutoVerifyPassRecordsStep:
    """Verification passes on first try — step is auto-recorded without human confirm."""

    async def test_pass_first_try_records_step(self) -> None:
        instructions = iter([("Click login", "Login modal visible"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier([_mock_verification_result(passed=True)])
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
            max_verify_retries=3,
        )

        verifier = _mock_verifier(
            [
                _mock_verification_result(passed=False, reasoning="Page not loaded yet"),
                _mock_verification_result(passed=True),
            ]
        )
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 1
        # show_verification_result called twice (fail then pass)
        assert cb.show_verification_result.call_count == 2
        # Auto-retry message was shown
        cb.show_message.assert_any_call("Verification failed. Auto-retrying (1/3)...")


class TestAutoNavigateViaKeyboard:
    """Auto-URL-navigate policy (ADR-0001): when source_url is set, the
    NAVIGATE state dispatches Cmd+L + type_text(url) + Return via CUA's
    keyboard transport instead of asking UI-TARS to visually click the
    URL bar. Saves a training cycle on a deterministic step and works
    when the Safari chrome crop is enabled.
    """

    async def test_navigate_dispatches_cmd_l_then_type_then_return(self) -> None:
        """The navigation helper must fire hotkey('cmd','l'), type_text(url), press_key('return')."""
        cb = _mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = _mock_vm()
        computer = AsyncMock()
        computer.interface = AsyncMock()
        # Track call order across all three keyboard methods
        call_order: list[str] = []

        async def record_hotkey(*keys, delay=None):  # noqa: ARG001
            call_order.append(f"hotkey:{'+'.join(keys)}")

        async def record_type_text(text, delay=None):  # noqa: ARG001
            call_order.append(f"type_text:{text}")

        async def record_press_key(key, delay=None):  # noqa: ARG001
            call_order.append(f"press_key:{key}")

        computer.interface.hotkey = AsyncMock(side_effect=record_hotkey)
        computer.interface.type_text = AsyncMock(side_effect=record_type_text)
        computer.interface.press_key = AsyncMock(side_effect=record_press_key)
        computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
        vm.connect_computer = AsyncMock(return_value=computer)

        session = TrainingSession(
            vm_manager=vm,
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="sproutful",
            flow_type="login",
            source_url="https://sproutful.com",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            await session.run()

        # Must see these three calls in this exact order
        assert "hotkey:cmd+l" in call_order, f"Expected hotkey:cmd+l in {call_order}"
        assert "type_text:https://sproutful.com" in call_order, f"Expected type_text in {call_order}"
        assert "press_key:return" in call_order, f"Expected press_key:return in {call_order}"

        hotkey_idx = call_order.index("hotkey:cmd+l")
        type_idx = call_order.index("type_text:https://sproutful.com")
        return_idx = call_order.index("press_key:return")
        assert hotkey_idx < type_idx < return_idx, (
            f"Expected Cmd+L -> type URL -> Return in that order; got {call_order}"
        )

    async def test_navigate_bypasses_ui_tars_when_url_set(self) -> None:
        """When source_url is set, _navigate must NOT invoke _execute_single_step.

        The whole point of ADR-0001's training-mode policy is to avoid
        wasting a VLM cycle on a deterministic URL navigation. If this test
        fails, we're still routing through UI-TARS and the feature is broken.
        """
        cb = _mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = _mock_vm()
        computer = AsyncMock()
        computer.interface = AsyncMock()
        computer.interface.hotkey = AsyncMock()
        computer.interface.type_text = AsyncMock()
        computer.interface.press_key = AsyncMock()
        computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
        vm.connect_computer = AsyncMock(return_value=computer)

        session = TrainingSession(
            vm_manager=vm,
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="sproutful",
            flow_type="login",
            source_url="https://sproutful.com",
        )

        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_execute_single_step", wraps=session._execute_single_step) as mock_step,
        ):
            await session.run()

        # _execute_single_step may be called zero times (only login flow with /done).
        # Critically, it must NOT have been called for the navigation step.
        nav_calls = [
            call
            for call in mock_step.call_args_list
            if call.args and isinstance(call.args[0], str) and "sproutful.com" in call.args[0]
        ]
        assert not nav_calls, f"Navigation must not route through UI-TARS / _execute_single_step; got {nav_calls}"
        # ComputerAgent must not have been constructed for navigation either
        assert MockAgent.call_count == 0, (
            f"No ComputerAgent should be created for navigation; got {MockAgent.call_count} calls"
        )

    async def test_navigation_sleeps_long_enough_for_page_to_load(self) -> None:
        """After press_key('return'), we must wait long enough for the page
        to load before the post-navigation screenshot is captured.

        Regression: the first cut of this feature took the "current screen"
        screenshot immediately after Enter, so the panel displayed the
        pre-navigation Safari Favorites page instead of the navigated-to
        target. The fix is a post-Return sleep calibrated for realistic
        page-load times (>= 2 seconds).
        """
        from soren.services import training_session as ts_module

        cb = _mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = _mock_vm()
        computer = AsyncMock()
        computer.interface = AsyncMock()
        computer.interface.hotkey = AsyncMock()
        computer.interface.type_text = AsyncMock()
        computer.interface.press_key = AsyncMock()
        computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
        vm.connect_computer = AsyncMock(return_value=computer)

        session = TrainingSession(
            vm_manager=vm,
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="sproutful",
            flow_type="login",
            source_url="https://sproutful.com",
        )

        # Patch asyncio.sleep on the training_session module so we can see
        # what durations were requested during navigation, without actually
        # sleeping for seconds in the test.
        sleep_durations: list[float] = []

        async def fake_sleep(duration):
            sleep_durations.append(duration)

        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT),
            patch.object(ts_module.asyncio, "sleep", side_effect=fake_sleep),
        ):
            await session.run()

        # We expect at least one sleep of >= 2 seconds to let the page load
        # before the post-navigation screenshot is captured.
        assert any(d >= 2.0 for d in sleep_durations), (
            f"Expected a post-Return sleep of >= 2s for page load; got {sleep_durations}"
        )
        computer.interface.press_key.assert_any_await("return")

    async def test_no_navigation_when_url_empty(self) -> None:
        """If source_url is empty string (default), no keyboard dispatch happens."""
        cb = _mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = _mock_vm()
        computer = AsyncMock()
        computer.interface = AsyncMock()
        computer.interface.hotkey = AsyncMock()
        computer.interface.type_text = AsyncMock()
        computer.interface.press_key = AsyncMock()
        computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
        vm.connect_computer = AsyncMock(return_value=computer)

        session = TrainingSession(
            vm_manager=vm,
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="sproutful",
            flow_type="login",
            # source_url omitted — default ""
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            await session.run()

        computer.interface.hotkey.assert_not_called()
        computer.interface.type_text.assert_not_called()
        # press_key may be called by other paths (e.g. enter-key patch test setup),
        # but hotkey and type_text are specific to the nav path.


class TestRetryInstructionIncludesVerifierFeedback:
    """Regression test: retries must feed the verifier's failure reasoning back
    to UI-TARS so the model has a signal to pick a different element.

    Before this change, retries re-used the identical instruction on every
    attempt, and UI-TARS would lock onto the same (wrong) coordinates — the
    retry logic was effectively a no-op against deterministic models.
    """

    async def test_second_attempt_instruction_carries_verifier_reasoning(self) -> None:
        instructions = iter([("Click the Log in button", "Login modal visible"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
            max_verify_retries=3,
        )

        verifier_reasoning = (
            "The screenshot shows the homepage with a 'LOG IN OR SIGN UP' text "
            "but no modal with input fields. That text is part of the page content, "
            "not a modal overlay."
        )
        verifier = _mock_verifier(
            [
                _mock_verification_result(passed=False, reasoning=verifier_reasoning),
                _mock_verification_result(passed=True),
            ]
        )

        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
            max_verify_retries=3,
        )

        verifier = _mock_verifier(
            [
                _mock_verification_result(passed=False, reasoning="No modal visible"),
                _mock_verification_result(passed=True),
            ]
        )

        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
            await session.run()

        # _make_cua_plan() emits click x=100 y=200; retry (call index 1) must cite those
        retry_instructions = MockAgent.call_args_list[1].kwargs.get("instructions", "")
        assert "100" in retry_instructions and "200" in retry_instructions, (
            f"Retry instructions should mention last click coords (100, 200); got: {retry_instructions!r}"
        )

    async def test_system_prompt_warns_about_safari_chrome_when_crop_disabled(self, monkeypatch) -> None:
        """CUA system prompt must tell UI-TARS about Safari's browser chrome layout
        WHEN THE CROP IS DISABLED.

        Ground-truth screenshot showed UI-TARS was clicking at y~85 (inside
        Safari's toolbar) when the real target was at y~115 (inside the
        Sproutful page header). The model conflates 'top of screen' with
        'page header', and needs an explicit chrome boundary to break the
        conflation — but only when it's actually seeing the chrome.
        """
        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)

        instructions = iter([("Click Log in", "Modal visible"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier([_mock_verification_result(passed=True)])

        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier([_mock_verification_result(passed=True)])

        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
            max_verify_retries=3,
        )

        verifier = _mock_verifier(
            [
                _mock_verification_result(passed=False, reasoning="FIRST_REASON_hit_nav_bar"),
                _mock_verification_result(passed=False, reasoning="SECOND_REASON_hit_hero"),
                _mock_verification_result(passed=True),
            ]
        )

        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
            await session.run()

        assert MockAgent.call_count == 3
        attempt3_instructions = MockAgent.call_args_list[2].kwargs.get("instructions", "")
        # Third attempt must use SECOND reason (the most recent failure), not FIRST
        assert "SECOND_REASON_hit_hero" in attempt3_instructions
        assert "FIRST_REASON_hit_nav_bar" not in attempt3_instructions, (
            "Retry should carry only the most recent verifier reasoning, not a growing log"
        )


class TestRetriesExhaustedHumanSkip:
    """All 3 verification attempts fail, human chooses skip — step NOT recorded."""

    async def test_human_skip_after_exhausted_retries(self) -> None:
        instructions = iter([("Click login", "Login modal visible"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            handle_stuck=AsyncMock(return_value="skip"),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
            max_verify_retries=3,
        )

        fail_result = _mock_verification_result(passed=False, reasoning="Wrong page")
        verifier = _mock_verifier([fail_result, fail_result, fail_result])
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            handle_stuck=AsyncMock(return_value="accept"),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
            max_verify_retries=2,
        )

        fail_result = _mock_verification_result(passed=False)
        verifier = _mock_verifier([fail_result, fail_result])
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            handle_stuck=AsyncMock(return_value="abort"),
        )
        ps = _mock_playbook_service()

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=ps,
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
            max_verify_retries=1,
        )

        fail_result = _mock_verification_result(passed=False)
        verifier = _mock_verifier([fail_result])
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is None
        ps.save_flow.assert_not_called()


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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier(
            [
                _mock_verification_result(passed=True),
                _mock_verification_result(passed=True),
                _mock_verification_result(passed=True),
            ]
        )
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 3
        assert result.verified_by == "human"
        assert result.confidence == 0.5
        assert session.state == TrainingState.DONE


class TestSlashCommandsStillWork:
    """Slash commands (/done, /abort, /undo, /steps, /help) work identically."""

    async def test_done_immediately_saves(self) -> None:
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )
        ps = _mock_playbook_service()

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=ps,
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 0
        ps.save_flow.assert_called_once()

    async def test_abort_returns_none(self) -> None:
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/abort", "")),
        )
        ps = _mock_playbook_service()

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=ps,
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
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
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier(
            [
                _mock_verification_result(passed=True),
                _mock_verification_result(passed=True),
            ]
        )
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 1
        assert result.steps[0].instruction == "Click A"

    async def test_help_shows_text(self) -> None:
        instructions = iter([("/help", ""), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            await session.run()

        help_calls = [c for c in cb.show_message.call_args_list if "/done" in str(c)]
        assert len(help_calls) >= 1


class TestPrereqReplayRunsLoginForDiscovery:
    async def test_login_playbook_replayed(self) -> None:
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )
        ps = _mock_playbook_service()

        login_flow = LoginFlow(
            steps=[PlaybookStep(seq=1, instruction="Click login btn", expected_after="Logged in")],
        )
        site = SitePlaybook(
            source_name="prolific",
            source_url="https://prolific.com",
            flows=SiteFlows(login=login_flow),
        )
        ps.load_site = MagicMock(return_value=site)

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=ps,
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="discovery",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT) as MockAgent:
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is not None
        replay_msgs = [c for c in cb.show_message.call_args_list if "Replaying login flow" in str(c)]
        assert len(replay_msgs) >= 1


class TestPrereqReplaySkippedForLogin:
    async def test_no_prereq_replay_for_login_flow(self) -> None:
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )
        ps = _mock_playbook_service()

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=ps,
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            result = await session.run()

        assert result is not None
        replay_msgs = [c for c in cb.show_message.call_args_list if "Replaying login" in str(c)]
        assert len(replay_msgs) == 0


class TestErrorDuringCuaOffersRetry:
    async def test_cua_error_shows_error_and_offers_retry(self) -> None:
        instructions = iter([("Click button", "Button clicked"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
            # _try_execute_step asks get_instruction for retry/skip on CUA error
            get_instruction=AsyncMock(return_value=""),  # Enter = retry
        )
        ps = _mock_playbook_service()
        call_count = 0

        async def failing_then_succeeding_run(messages):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("CUA crash")
            yield _make_cua_result()

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=ps,
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier([_mock_verification_result(passed=True)])
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            mock_inst = MagicMock()
            mock_inst.run = MagicMock(side_effect=failing_then_succeeding_run)
            MockAgent.return_value = mock_inst
            result = await session.run()

        assert result is not None
        assert cb.show_error.call_count >= 1


class TestStateTransitions:
    async def test_login_flow_reaches_done(self) -> None:
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="test",
            flow_type="login",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            await session.run()

        assert session.state == TrainingState.DONE

    async def test_discovery_flow_includes_prereq(self) -> None:
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )
        ps = _mock_playbook_service()
        ps.load_site = MagicMock(return_value=None)

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=ps,
            llm=_mock_llm(),
            callbacks=cb,
            source_name="test",
            flow_type="discovery",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            result = await session.run()

        assert result is not None
        assert session.state == TrainingState.DONE
        no_login_msgs = [c for c in cb.show_message.call_args_list if "No login playbook" in str(c)]
        assert len(no_login_msgs) >= 1


class TestVerifierLazyConstruction:
    """ActionVerifier should NOT be created when only slash commands are used."""

    async def test_no_verifier_on_immediate_done(self) -> None:
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="test",
            flow_type="login",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT):
            await session.run()

        assert session._verifier is None


class TestConfirmPromptDisplaysShortcutKeys:
    """Regression: Rich markup [y], [n], [d] must render as visible text, not get stripped as style tags."""

    async def test_prompt_shows_shortcut_keys(self) -> None:
        from rich.text import Text

        from soren.cli.train import RichTrainingCallbacks

        cb = RichTrainingCallbacks()
        with patch("soren.cli.train.console") as mock_console:
            mock_console.input.return_value = "y"
            await cb.confirm_step(b"fake-screenshot", "A login form")

            prompt_markup = mock_console.input.call_args[0][0]
            rendered = Text.from_markup(prompt_markup).plain
            assert "[y]" in rendered, f"Expected '[y]' in prompt, got: {rendered!r}"
            assert "[n]" in rendered, f"Expected '[n]' in prompt, got: {rendered!r}"
            assert "[d]" in rendered, f"Expected '[d]' in prompt, got: {rendered!r}"


class TestExecuteSingleStepConsumesFullGenerator:
    """Regression tests: _execute_single_step must run the generator past the first yield."""

    async def test_screenshot_captured_from_execution_phase_not_plan_phase(self) -> None:
        iterations_seen: list[int] = []

        async def two_phase_run(messages):  # noqa: ARG001
            iterations_seen.append(1)
            yield _make_cua_plan()
            iterations_seen.append(2)
            yield _make_cua_result()
            iterations_seen.append(3)
            yield _make_cua_completion()

        instructions = iter([("Click the button", "Button clicked"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier([_mock_verification_result(passed=True)])
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            mock_inst = MagicMock()
            mock_inst.run = MagicMock(side_effect=lambda m: two_phase_run(m))
            MockAgent.return_value = mock_inst
            result = await session.run()

        assert 2 in iterations_seen, "Execution phase (iteration 2) was never reached — loop broke too early"
        assert result is not None
        assert len(result.steps) == 1

    async def test_computer_call_action_logged_at_info(self, caplog) -> None:
        """Each computer_call item must emit an INFO log line with action type and coords.

        Without this, the only signal a click was issued is whether the verifier
        passed afterwards — which makes coordinate misses impossible to diagnose
        from the training run output alone.
        """
        import logging

        instructions = iter([("Click the login button", "Modal opens"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier([_mock_verification_result(passed=True)])
        with (
            caplog.at_level(logging.INFO, logger="soren.services.training_session"),
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = _patched_cua()
            await session.run()

        action_records = [
            r for r in caplog.records if r.name == "soren.services.training_session" and "CUA action" in r.getMessage()
        ]
        assert action_records, (
            f"Expected at least one INFO 'CUA action' log line; got {[r.getMessage() for r in caplog.records]}"
        )
        msg = action_records[0].getMessage()
        # _make_cua_plan() emits click at x=100 y=200
        assert "click" in msg
        assert "100" in msg
        assert "200" in msg

    async def test_plan_only_first_yield_has_no_screenshot(self) -> None:
        iterations_seen: list[int] = []

        async def plan_only_run(messages):  # noqa: ARG001
            iterations_seen.append(1)
            yield _make_cua_plan()
            iterations_seen.append(2)
            yield _make_cua_result()
            iterations_seen.append(3)
            yield _make_cua_completion()

        instructions = iter([("Click something", "Done"), ("/done", "")])
        cb = _mock_callbacks(
            get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=_mock_playbook_service(),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        verifier = _mock_verifier([_mock_verification_result(passed=True)])
        with (
            patch(_PATCH_APPLY_ALL),
            patch(_PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            mock_inst = MagicMock()
            mock_inst.run = MagicMock(side_effect=lambda m: plan_only_run(m))
            MockAgent.return_value = mock_inst
            await session.run()

        assert 2 in iterations_seen, "Generator stopped at plan phase — VLM action was never executed on VM"
