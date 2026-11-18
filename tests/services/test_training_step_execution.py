"""Single CUA step execution during training, and the active service context."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.services.training_session import TrainingSession
from tests.services.training_helpers import (
    PATCH_APPLY_ALL,
    PATCH_CUA_AGENT,
    fake_agent_run,
    make_cua_completion,
    make_cua_plan,
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


class TestExecuteSingleStepConsumesFullGenerator:
    """Regression tests: _execute_single_step must run the generator past the first yield."""

    async def test_screenshot_captured_from_execution_phase_not_plan_phase(self) -> None:
        iterations_seen: list[int] = []

        async def two_phase_run(messages):  # noqa: ARG001
            iterations_seen.append(1)
            yield make_cua_plan()
            iterations_seen.append(2)
            yield make_cua_result()
            iterations_seen.append(3)
            yield make_cua_completion()

        instructions = iter([("Click the button", "Button clicked"), ("/done", "")])
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
            caplog.at_level(logging.INFO, logger="soren.services.training_step_runner"),
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = patched_cua()
            await session.run()

        action_records = [
            r
            for r in caplog.records
            if r.name == "soren.services.training_step_runner" and "CUA action" in r.getMessage()
        ]
        assert action_records, (
            f"Expected at least one INFO 'CUA action' log line; got {[r.getMessage() for r in caplog.records]}"
        )
        msg = action_records[0].getMessage()
        # make_cua_plan() emits click at x=100 y=200
        assert "click" in msg
        assert "100" in msg
        assert "200" in msg

    async def test_plan_only_first_yield_has_no_screenshot(self) -> None:
        iterations_seen: list[int] = []

        async def plan_only_run(messages):  # noqa: ARG001
            iterations_seen.append(1)
            yield make_cua_plan()
            iterations_seen.append(2)
            yield make_cua_result()
            iterations_seen.append(3)
            yield make_cua_completion()

        instructions = iter([("Click something", "Done"), ("/done", "")])
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
            mock_inst = MagicMock()
            mock_inst.run = MagicMock(side_effect=lambda m: plan_only_run(m))
            MockAgent.return_value = mock_inst
            await session.run()

        assert 2 in iterations_seen, "Generator stopped at plan phase — VLM action was never executed on VM"


class TestTrainingUsesServiceContext:
    """Phase 2 (ADR-0002): a resolved service drives the crop/prompt during training; no env var needed."""

    def _session(self, service) -> TrainingSession:
        instructions = iter([("Click Log in", "Modal visible"), ("/done", "")])
        cb = mock_callbacks(get_instruction_with_expected=AsyncMock(side_effect=lambda: next(instructions)))
        return TrainingSession(
            vm_manager=mock_vm(),
            playbook_service=mock_playbook_service(),
            llm=mock_llm(),
            callbacks=cb,
            source_name="acmepanel",
            flow_type="login",
            service=service,
        )

    async def _run(self, session: TrainingSession, seen: list | None = None):
        verifier = mock_verifier([mock_verification_result(passed=True)])
        agent = patched_cua()
        if seen is not None:
            from soren.services.service_context import get_active_service

            async def recording_run(messages):
                seen.append(get_active_service())
                async for item in fake_agent_run(messages):
                    yield item

            agent.run = recording_run
        with (
            patch(PATCH_APPLY_ALL),
            patch(PATCH_CUA_AGENT) as MockAgent,
            patch.object(session, "_get_verifier", return_value=verifier),
        ):
            MockAgent.return_value = agent
            await session.run()
        return MockAgent

    async def test_service_crop_omits_chrome_prompt_without_env(self, monkeypatch, tmp_path) -> None:
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        svc = make_resolved_service(tmp_path, "acmepanel", config={"chrome_height_px": 100})
        MockAgent = await self._run(self._session(svc))
        instructions = MockAgent.call_args_list[0].kwargs.get("instructions", "")
        assert "NEVER click at y<100" not in instructions
        assert "Click Log in" in instructions

    async def test_service_without_crop_keeps_chrome_prompt(self, monkeypatch, tmp_path) -> None:
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        svc = make_resolved_service(tmp_path, "acmepanel", config={"chrome_height_px": 0})
        MockAgent = await self._run(self._session(svc))
        assert "NEVER click at y<100" in MockAgent.call_args_list[0].kwargs.get("instructions", "")

    async def test_steps_run_inside_service_context_and_clear_after(self, monkeypatch, tmp_path) -> None:
        from soren.services.service_context import get_active_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        svc = make_resolved_service(tmp_path, "acmepanel", config={"chrome_height_px": 100})
        seen: list = []
        await self._run(self._session(svc), seen)
        assert seen and all(s is svc for s in seen)
        assert get_active_service() is None

    async def test_no_service_keeps_env_only_behavior(self, monkeypatch) -> None:
        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "100")
        MockAgent = await self._run(self._session(None))
        assert "NEVER click at y<100" not in MockAgent.call_args_list[0].kwargs.get("instructions", "")
