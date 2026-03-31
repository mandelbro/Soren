"""Tests for TrainingSession orchestrator with callback-based I/O (Task 014)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from soren.models.site_playbook import LoginFlow, PlaybookStep, SiteFlows, SitePlaybook
from soren.services.training_session import TrainingCallbacks, TrainingSession, TrainingState

# Patch targets: apply_all and ComputerAgent are imported lazily inside _execute_single_step.
_PATCH_APPLY_ALL = "soren.patches.apply_all"
_PATCH_CUA_AGENT = "agent.ComputerAgent"


# --- Helpers ---


def _mock_callbacks(**overrides: AsyncMock) -> TrainingCallbacks:
    """Create a mock callbacks object implementing the protocol."""
    cb = MagicMock(spec=TrainingCallbacks)
    cb.show_screenshot = overrides.get("show_screenshot", AsyncMock())
    cb.get_instruction = overrides.get("get_instruction", AsyncMock(return_value="/done"))
    cb.confirm_step = overrides.get("confirm_step", AsyncMock(return_value="y"))
    cb.get_expected_after = overrides.get("get_expected_after", AsyncMock(return_value="Screen looks correct"))
    cb.show_message = overrides.get("show_message", AsyncMock())
    cb.show_error = overrides.get("show_error", AsyncMock())
    return cb


def _mock_vm() -> AsyncMock:
    vm = AsyncMock()
    vm.ensure_ready = AsyncMock()
    computer = AsyncMock()
    computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
    vm.connect_computer = AsyncMock(return_value=computer)
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


def _make_cua_result(screenshot_b64: str = "ZmFrZQ==") -> dict:
    """Build a single CUA agent iteration result with a screenshot."""
    return {
        "output": [
            {
                "type": "computer_call_output",
                "output": {"image_url": f"data:image/png;base64,{screenshot_b64}"},
            }
        ]
    }


async def _fake_agent_run(messages: list) -> list[dict]:  # noqa: ARG001
    """Async generator that yields one CUA result then stops."""
    yield _make_cua_result()


def _patched_cua():
    """Return a context manager that patches apply_all and ComputerAgent for CUA calls."""
    mock_agent_inst = MagicMock()
    mock_agent_inst.run = MagicMock(side_effect=lambda m: _fake_agent_run(m))
    return mock_agent_inst


# --- Tests ---


class TestFullLoopThreeStepsSave:
    async def test_records_three_steps_and_saves(self) -> None:
        instructions = iter(["Click login", "Enter email", "Click submit", "/done"])
        cb = _mock_callbacks(
            get_instruction=AsyncMock(side_effect=lambda: next(instructions)),
            confirm_step=AsyncMock(return_value="y"),
            get_expected_after=AsyncMock(return_value="Step done"),
        )

        session = TrainingSession(
            vm_manager=_mock_vm(),
            playbook_service=(ps := _mock_playbook_service()),
            llm=_mock_llm(),
            callbacks=cb,
            source_name="prolific",
            flow_type="login",
        )

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT) as MockAgent:
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 3
        assert result.verified_by == "human"
        assert result.confidence == 0.5
        ps.save_flow.assert_called_once()
        assert session.state == TrainingState.DONE


class TestConfirmStepDoneRecordsAndFinishes:
    async def test_confirm_d_records_step_and_finishes(self) -> None:
        """confirm_step returning 'd' records the current step then exits."""
        instructions = iter(["Click login"])
        cb = _mock_callbacks(
            get_instruction=AsyncMock(side_effect=lambda: next(instructions)),
            confirm_step=AsyncMock(return_value="d"),
            get_expected_after=AsyncMock(return_value="Logged in"),
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

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT) as MockAgent:
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 1
        assert result.steps[0].instruction == "Click login"
        ps.save_flow.assert_called_once()
        assert session.state == TrainingState.DONE


class TestDoneExitsStepLoop:
    async def test_done_immediately_saves(self) -> None:
        cb = _mock_callbacks(get_instruction=AsyncMock(return_value="/done"))
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


class TestAbortReturnsNone:
    async def test_abort_returns_none_nothing_saved(self) -> None:
        cb = _mock_callbacks(get_instruction=AsyncMock(return_value="/abort"))
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


class TestUndoRemovesLastStep:
    async def test_undo_after_two_steps_saves_one(self) -> None:
        instructions = iter(["Click A", "Click B", "/undo", "/done"])
        cb = _mock_callbacks(
            get_instruction=AsyncMock(side_effect=lambda: next(instructions)),
            confirm_step=AsyncMock(return_value="y"),
            get_expected_after=AsyncMock(return_value="OK"),
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

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT) as MockAgent:
            MockAgent.return_value = _patched_cua()
            result = await session.run()

        assert result is not None
        assert len(result.steps) == 1
        assert result.steps[0].instruction == "Click A"


class TestPrereqReplayRunsLoginForDiscovery:
    async def test_login_playbook_replayed(self) -> None:
        cb = _mock_callbacks(get_instruction=AsyncMock(return_value="/done"))
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
        cb = _mock_callbacks(get_instruction=AsyncMock(return_value="/done"))
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
        instructions = iter(["Click button", "retry", "/done"])
        cb = _mock_callbacks(
            get_instruction=AsyncMock(side_effect=lambda: next(instructions)),
            confirm_step=AsyncMock(return_value="y"),
            get_expected_after=AsyncMock(return_value="OK"),
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

        with patch(_PATCH_APPLY_ALL), patch(_PATCH_CUA_AGENT) as MockAgent:
            mock_inst = MagicMock()
            mock_inst.run = MagicMock(side_effect=failing_then_succeeding_run)
            MockAgent.return_value = mock_inst
            result = await session.run()

        assert result is not None
        assert cb.show_error.call_count >= 1


class TestStateTransitions:
    async def test_login_flow_reaches_done(self) -> None:
        """Login training goes INIT -> NAVIGATE -> STEP_LOOP -> FINALIZING -> SAVING -> DONE."""
        cb = _mock_callbacks(get_instruction=AsyncMock(return_value="/done"))

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
        """Discovery training hits PREREQ_REPLAY state."""
        cb = _mock_callbacks(get_instruction=AsyncMock(return_value="/done"))
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
