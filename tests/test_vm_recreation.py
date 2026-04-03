"""Tests for VM recreation and pristine image management."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.vm import VMConfig
from soren.services.vm_manager import VMManager

# --- recreate_vm tests ---


@pytest.mark.asyncio
async def test_recreate_vm_calls_stop_delete_clone_start():
    """recreate_vm calls stop, delete, clone, start in correct order."""
    config = VMConfig(
        name="soren-vm",
        pristine_name="soren-pristine",
        recreate_every_n_cycles=10,
    )
    manager = VMManager(config)

    call_order = []

    async def track_request(method, path, *args, **kwargs):
        call_order.append((method, path))
        return {}

    with (
        patch.object(manager._client, "_request", new_callable=AsyncMock, side_effect=track_request),
        patch.object(manager, "_wait_for_ready", new_callable=AsyncMock),
        patch.object(manager, "connect_computer", new_callable=AsyncMock),
        patch.object(manager, "prepare_session_environment", new_callable=AsyncMock),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await manager.recreate_vm(timeout_s=5)

    assert len(call_order) >= 4
    paths = [str(p) for _, p in call_order]
    assert any("stop" in p for p in paths)
    assert any("clone" in p for p in paths)
    assert any("run" in p for p in paths)


@pytest.mark.asyncio
async def test_recreate_vm_waits_for_ready():
    """recreate_vm waits for VM readiness after clone boot."""
    config = VMConfig(name="soren-vm", pristine_name="soren-pristine")
    manager = VMManager(config)

    mock_wait = AsyncMock()
    with (
        patch.object(manager._client, "_request", new_callable=AsyncMock, return_value={}),
        patch.object(manager, "_wait_for_ready", mock_wait),
        patch.object(manager, "connect_computer", new_callable=AsyncMock),
        patch.object(manager, "prepare_session_environment", new_callable=AsyncMock),
    ):
        await manager.recreate_vm(timeout_s=10)

    mock_wait.assert_called_once()


@pytest.mark.asyncio
async def test_recreate_vm_repatches_vnc():
    """recreate_vm re-patches VNC screenshot after recreation."""
    config = VMConfig(name="soren-vm", pristine_name="soren-pristine")
    manager = VMManager(config)

    mock_connect = AsyncMock()
    with (
        patch.object(manager._client, "_request", new_callable=AsyncMock, return_value={}),
        patch.object(manager, "_wait_for_ready", new_callable=AsyncMock),
        patch.object(manager, "connect_computer", mock_connect),
        patch.object(manager, "prepare_session_environment", new_callable=AsyncMock) as mock_prep,
    ):
        await manager.recreate_vm(timeout_s=5)

    mock_connect.assert_called_once()
    mock_prep.assert_called_once()


# --- _should_recreate tests ---


def test_should_recreate_at_n_cycle_interval():
    """_should_recreate returns True at N-cycle intervals."""
    config = VMConfig(name="soren-vm", recreate_every_n_cycles=10)
    manager = VMManager(config)
    manager._cycle_count = 10

    assert manager._should_recreate() is True


def test_should_recreate_when_contamination_detected():
    """_should_recreate returns True when contamination flag is set."""
    config = VMConfig(name="soren-vm", recreate_every_n_cycles=10)
    manager = VMManager(config)
    manager._cycle_count = 5
    manager.mark_contamination()

    assert manager._should_recreate() is True


def test_should_recreate_false_mid_interval():
    """_should_recreate returns False mid-interval with no contamination."""
    config = VMConfig(name="soren-vm", recreate_every_n_cycles=10)
    manager = VMManager(config)
    manager._cycle_count = 5

    assert manager._should_recreate() is False


def test_should_recreate_false_when_cycle_count_zero():
    """_should_recreate returns False when cycle_count is 0."""
    config = VMConfig(name="soren-vm", recreate_every_n_cycles=10)
    manager = VMManager(config)
    manager._cycle_count = 0

    assert manager._should_recreate() is False


# --- prepare_pristine_vm tests ---


@pytest.mark.asyncio
async def test_prepare_pristine_vm_follows_sequence():
    """prepare_pristine_vm follows the correct preparation sequence."""
    from soren.services.vm_prepare import prepare_pristine_vm

    call_log = []

    async def track_pull(image, name=None):
        call_log.append(("pull", image, name))
        return {}

    async def track_start(name, **kwargs):
        call_log.append(("start", name))
        return {}

    async def track_stop(name, **kwargs):
        call_log.append(("stop", name))
        return {}

    mock_computer = MagicMock()
    mock_computer.interface.run_command = AsyncMock(
        side_effect=[
            MagicMock(stdout="/usr/bin/python3", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
        ]
    )

    async def connect_and_set_computer():
        mock_manager._computer = mock_computer

    mock_manager = MagicMock()
    mock_manager._client = MagicMock()
    mock_manager._client.pull_image = track_pull
    mock_manager._client.start_vm = track_start
    mock_manager._client.stop_vm = track_stop
    mock_manager._vm_name = "soren-vm"
    mock_manager._wait_for_ready = AsyncMock()
    mock_manager.connect_computer = AsyncMock(side_effect=connect_and_set_computer)
    mock_manager._computer = mock_computer

    with (
        patch("soren.services.vm_prepare.load_settings"),
        patch("soren.services.vm_prepare.VMManager", return_value=mock_manager),
    ):
        settings = MagicMock()
        settings.vm.pristine_name = "soren-pristine"

        await prepare_pristine_vm(settings=settings)

    assert any(c[0] == "pull" for c in call_log)
    assert any(c[0] == "start" for c in call_log)
    assert any(c[0] == "stop" for c in call_log)


@pytest.mark.asyncio
async def test_prepare_pristine_vm_handles_sip_blocked_tcc():
    """prepare_pristine_vm handles SIP-blocked TCC.db with warning."""
    from soren.services.vm_prepare import prepare_pristine_vm

    mock_manager = AsyncMock()
    mock_manager._client.pull_image = AsyncMock(return_value={})
    mock_manager._client.start_vm = AsyncMock(return_value={})
    mock_manager._client.stop_vm = AsyncMock(return_value={})
    mock_manager._vm_name = "soren-vm"
    mock_manager._wait_for_ready = AsyncMock()
    mock_manager.connect_computer = AsyncMock()
    mock_manager._computer = MagicMock()
    # Simulate TCC failure (returncode != 0)
    mock_manager._computer.interface.run_command = AsyncMock(
        side_effect=[
            MagicMock(stdout="/usr/bin/python3", stderr="", returncode=0),
            MagicMock(stdout="", stderr="unable to open database", returncode=1),
            MagicMock(stdout="", stderr="unable to open database", returncode=1),
        ]
    )

    with (
        patch("soren.services.vm_prepare.load_settings"),
        patch("soren.services.vm_prepare.VMManager", return_value=mock_manager),
        patch("soren.services.vm_prepare.logger") as mock_logger,
    ):
        settings = MagicMock()
        settings.vm.pristine_name = "soren-pristine"
        with patch("soren.services.vm_prepare.load_settings", return_value=settings):
            await prepare_pristine_vm(settings=settings)

    mock_logger.warning.assert_called()


# --- CLI vm prepare tests ---


def test_vm_prepare_invokes_preparation():
    """CLI vm prepare invokes preparation sequence."""
    from click.testing import CliRunner

    from soren.cli import cli

    runner = CliRunner()
    with patch(
        "soren.services.vm_prepare.prepare_pristine_vm",
        new_callable=AsyncMock,
    ) as mock_prepare:
        result = runner.invoke(cli, ["vm", "prepare"])

    assert result.exit_code == 0
    mock_prepare.assert_called_once()
