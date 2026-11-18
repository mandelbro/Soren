"""Training navigation: keyboard auto-navigation (ADR-0001) and login prerequisite replay."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.site_playbook import LoginFlow, PlaybookStep, SiteFlows, SitePlaybook
from soren.services.training_session import TrainingSession
from tests.services.training_helpers import (
    PATCH_APPLY_ALL,
    PATCH_CUA_AGENT,
    mock_callbacks,
    mock_llm,
    mock_playbook_service,
    mock_vm,
    patched_cua,
)

# Unit tests: fake CUA SDK + virtual clock (tests/conftest.py, tests/services/conftest.py).
pytestmark = pytest.mark.usefixtures("fake_cua_sdk", "training_virtual_clock")


class TestAutoNavigateViaKeyboard:
    """Auto-URL-navigate policy (ADR-0001): when source_url is set, the
    NAVIGATE state dispatches Cmd+L + type_text(url) + Return via CUA's
    keyboard transport instead of asking UI-TARS to visually click the
    URL bar. Saves a training cycle on a deterministic step and works
    when the Safari chrome crop is enabled.
    """

    async def test_navigate_dispatches_cmd_l_then_type_then_return(self) -> None:
        """The navigation helper must fire hotkey('cmd','l'), type_text(url), press_key('return')."""
        cb = mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = mock_vm()
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
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="acmepanel",
            flow_type="login",
            source_url="https://acmepanel.example",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            await session.run()

        # Must see these three calls in this exact order
        assert "hotkey:cmd+l" in call_order, f"Expected hotkey:cmd+l in {call_order}"
        assert "type_text:https://acmepanel.example" in call_order, f"Expected type_text in {call_order}"
        assert "press_key:return" in call_order, f"Expected press_key:return in {call_order}"

        hotkey_idx = call_order.index("hotkey:cmd+l")
        type_idx = call_order.index("type_text:https://acmepanel.example")
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
        cb = mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = mock_vm()
        computer = AsyncMock()
        computer.interface = AsyncMock()
        computer.interface.hotkey = AsyncMock()
        computer.interface.type_text = AsyncMock()
        computer.interface.press_key = AsyncMock()
        computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
        vm.connect_computer = AsyncMock(return_value=computer)

        session = TrainingSession(
            vm_manager=vm,
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="acmepanel",
            flow_type="login",
            source_url="https://acmepanel.example",
        )

        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_execute_single_step", wraps=session._execute_single_step) as mock_step,
        ):
            await session.run()

        # _execute_single_step may be called zero times (only login flow with /done).
        # Critically, it must NOT have been called for the navigation step.
        nav_calls = [
            call
            for call in mock_step.call_args_list
            if call.args and isinstance(call.args[0], str) and "acmepanel.example" in call.args[0]
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
        from soren.services import training_navigation as nav_module

        cb = mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = mock_vm()
        computer = AsyncMock()
        computer.interface = AsyncMock()
        computer.interface.hotkey = AsyncMock()
        computer.interface.type_text = AsyncMock()
        computer.interface.press_key = AsyncMock()
        computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
        vm.connect_computer = AsyncMock(return_value=computer)

        session = TrainingSession(
            vm_manager=vm,
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="acmepanel",
            flow_type="login",
            source_url="https://acmepanel.example",
        )

        # Patch asyncio.sleep on the training_session module so we can see
        # what durations were requested during navigation, without actually
        # sleeping for seconds in the test.
        sleep_durations: list[float] = []

        async def fake_sleep(duration):
            sleep_durations.append(duration)

        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT),
            patch.object(nav_module.asyncio, "sleep", side_effect=fake_sleep),
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
        cb = mock_callbacks(get_instruction_with_expected=AsyncMock(return_value=("/done", "")))

        vm = mock_vm()
        computer = AsyncMock()
        computer.interface = AsyncMock()
        computer.interface.hotkey = AsyncMock()
        computer.interface.type_text = AsyncMock()
        computer.interface.press_key = AsyncMock()
        computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
        vm.connect_computer = AsyncMock(return_value=computer)

        session = TrainingSession(
            vm_manager=vm,
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="acmepanel",
            flow_type="login",
            # source_url omitted — default ""
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT):
            await session.run()

        computer.interface.hotkey.assert_not_called()
        computer.interface.type_text.assert_not_called()


class TestPrereqReplayRunsLoginForDiscovery:
    async def test_login_playbook_replayed(self) -> None:
        cb = mock_callbacks(
            get_instruction_with_expected=AsyncMock(return_value=("/done", "")),
        )
        ps = mock_playbook_service()

        login_flow = LoginFlow(
            steps=[PlaybookStep(seq=1, instruction="Click login btn", expected_after="Logged in")],
        )
        site = SitePlaybook(
            source_name="betapanel",
            source_url="https://betapanel.example",
            flows=SiteFlows(login=login_flow),
        )
        ps.load_site = MagicMock(return_value=site)

        session = TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=ps,
            llm=mock_llm(),
            callbacks=cb,
            source_name="betapanel",
            flow_type="discovery",
        )

        with patch(PATCH_APPLY_ALL), patch(PATCH_CUA_AGENT) as MockAgent:
            MockAgent.return_value = patched_cua()
            result = await session.run()

        assert result is not None
        replay_msgs = [c for c in cb.show_message.call_args_list if "Replaying login flow" in str(c)]
        assert len(replay_msgs) >= 1


class TestPrereqReplaySkippedForLogin:
    async def test_no_prereq_replay_for_login_flow(self) -> None:
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
        replay_msgs = [c for c in cb.show_message.call_args_list if "Replaying login" in str(c)]
        assert len(replay_msgs) == 0
