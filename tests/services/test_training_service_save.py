"""TrainingSession integration for service slash commands and write-back at /done (Phase 4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.services.service_context import get_active_service
from soren.services.service_registry import ServiceRegistry, load_service_file
from soren.services.service_writer import ServiceWriter
from soren.services.training_session import TrainingSession
from tests.service_helpers import make_resolved_service
from tests.services.training_helpers import (
    PATCH_APPLY_ALL,
    PATCH_CUA_AGENT,
    fake_agent_run,
    mock_callbacks,
    mock_llm,
    mock_playbook_service,
    mock_verification_result,
    mock_verifier,
    mock_vm,
)

# Unit tests: fake the CUA SDK (see tests/conftest.py::fake_cua_sdk).
pytestmark = pytest.mark.usefixtures("fake_cua_sdk", "training_virtual_clock")


def _session(tmp_path: Path, script: list[tuple[str, str]], *, service="default", confirm=True, **cb_overrides):
    inputs = iter(script)
    cb = mock_callbacks(get_instruction_with_expected=AsyncMock(side_effect=lambda: next(inputs)), **cb_overrides)
    cb.confirm_service_update = AsyncMock(return_value=confirm)
    if service == "default":
        service = make_resolved_service(
            tmp_path, "gcal", url="https://calendar.example", config={"chrome_height_px": 100}
        )
    registry = ServiceRegistry(tmp_path / "services")
    session = TrainingSession(
        vm_manager=mock_vm(),
        playbook_service=mock_playbook_service(),
        llm=mock_llm(),
        callbacks=cb,
        source_name="gcal",
        flow_type="login",
        service=service,
        service_writer=ServiceWriter(registry),
    )
    return session, cb, registry


async def _run(session: TrainingSession, seen_crops: list | None = None):
    agent = MagicMock()

    async def run(messages):
        if seen_crops is not None:
            active = get_active_service()
            seen_crops.append(active.config.chrome_height_px if active else None)
        async for item in fake_agent_run(messages):
            yield item

    agent.run = run
    with (
        patch(PATCH_APPLY_ALL),
        patch(PATCH_CUA_AGENT, return_value=agent),
        patch.object(session, "_get_verifier", return_value=mock_verifier([mock_verification_result(passed=True)] * 5)),
    ):
        return await session.run()


class TestCommandsInStepLoop:
    async def test_service_commands_not_recorded_as_steps(self, tmp_path: Path) -> None:
        session, cb, _ = _session(tmp_path, [("/instruction Use the sidebar", ""), ("/done", "")])
        await _run(session)
        assert session.steps == []
        messages = " ".join(str(c.args[0]) for c in cb.show_message.await_args_list)
        assert "config.instructions += Use the sidebar" in messages or "Use the sidebar" in messages

    async def test_crop_applies_to_following_steps(self, tmp_path: Path) -> None:
        session, _, _ = _session(
            tmp_path, [("Click A", "A done"), ("/crop 90", ""), ("Click B", "B done"), ("/done", "")]
        )
        seen: list = []
        await _run(session, seen)
        assert seen == [100, 90]

    async def test_help_lists_service_commands(self, tmp_path: Path) -> None:
        session, cb, _ = _session(tmp_path, [("/help", ""), ("/done", "")])
        await _run(session)
        help_text = str(cb.show_message.await_args_list[0].args[0])
        for command in ("/shortcut", "/instruction", "/note", "/crop", "/service"):
            assert command in help_text


class TestWriteBackAtDone:
    async def test_confirmed_changes_written(self, tmp_path: Path) -> None:
        session, cb, registry = _session(tmp_path, [("/shortcut w Week view", ""), ("/crop 90", ""), ("/done", "")])
        assert await _run(session) is not None
        path, changes, warnings = cb.confirm_service_update.await_args.args
        assert path == registry.path_for("gcal")
        assert "config.chrome_height_px: 90 (/crop)" in changes
        written = load_service_file(path)
        assert written.config.chrome_height_px == 90
        assert [s.keys for s in written.config.keyboard_shortcuts or []] == [["w"]]
        messages = " ".join(str(c.args[0]) for c in cb.show_message.await_args_list)
        assert "Service file updated" in messages

    async def test_declined_changes_not_written(self, tmp_path: Path) -> None:
        session, cb, registry = _session(tmp_path, [("/crop 90", ""), ("/done", "")], confirm=False)
        await _run(session)
        cb.confirm_service_update.assert_awaited_once()
        assert load_service_file(registry.path_for("gcal")).config.chrome_height_px == 100

    async def test_abort_writes_nothing(self, tmp_path: Path) -> None:
        session, cb, registry = _session(tmp_path, [("/crop 90", ""), ("/abort", "")])
        assert await _run(session) is None
        cb.confirm_service_update.assert_not_awaited()
        assert load_service_file(registry.path_for("gcal")).config.chrome_height_px == 100

    async def test_no_changes_no_prompt(self, tmp_path: Path) -> None:
        session, cb, _ = _session(tmp_path, [("/done", "")])
        await _run(session)
        cb.confirm_service_update.assert_not_awaited()

    async def test_write_failure_reported_and_playbook_still_saved(self, tmp_path: Path) -> None:
        session, cb, _ = _session(tmp_path, [("/crop 90", ""), ("/done", "")])
        with patch.object(ServiceWriter, "write", side_effect=OSError("read-only file system")):
            playbook = await _run(session)
        assert playbook is not None
        session._playbook_service.save_flow.assert_called_once()
        errors = " ".join(str(c.args[0]) for c in cb.show_error.await_args_list)
        assert "read-only file system" in errors

    async def test_file_edited_during_confirmation_is_not_overwritten(self, tmp_path: Path) -> None:
        """A hand edit (or `services migrate`) made while the trainer reads the prompt must survive."""
        session, cb, registry = _session(tmp_path, [("/crop 90", ""), ("/done", "")])
        path = registry.path_for("gcal")

        async def confirm_while_someone_edits(*_args):
            path.write_text(path.read_text(encoding="utf-8") + "notes: edited by hand\n", encoding="utf-8")
            return True

        cb.confirm_service_update = AsyncMock(side_effect=confirm_while_someone_edits)
        await _run(session)

        written = load_service_file(path)
        assert written.notes == "edited by hand"
        assert written.config.chrome_height_px == 100
        errors = " ".join(str(c.args[0]) for c in cb.show_error.await_args_list)
        assert "changed" in errors and "/crop" not in errors  # tells the trainer to re-apply; no stack trace

    async def test_playbook_saved_before_service_prompt(self, tmp_path: Path) -> None:
        session, cb, _ = _session(tmp_path, [("/crop 90", ""), ("/done", "")])
        order: list[str] = []
        session._playbook_service.save_flow.side_effect = lambda *a, **k: order.append("playbook")
        cb.confirm_service_update.side_effect = lambda *a: order.append("confirm") or True
        await _run(session)
        assert order == ["playbook", "confirm"]

    async def test_without_service_commands_are_unavailable(self, tmp_path: Path) -> None:
        session, cb, _ = _session(tmp_path, [("/crop 90", ""), ("/done", "")], service=None)
        await _run(session)
        messages = " ".join(str(c.args[0]) for c in cb.show_message.await_args_list)
        assert "unavailable" in messages
        cb.confirm_service_update.assert_not_awaited()
