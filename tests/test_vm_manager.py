"""Tests for VMManager and LumeClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from soren.models.vm import VMConfig
from soren.services.vm_manager import LumeClient, LumeConnectionError, VMManager

# --- LumeClient tests ---


@pytest.mark.asyncio
async def test_lume_client_list_vms_constructs_request():
    """LumeClient constructs correct HTTP request for list_vms."""
    expected = [{"name": "soren-vm", "status": "running"}]
    with patch.object(LumeClient, "_request", new_callable=AsyncMock, return_value=expected) as mock_req:
        client = LumeClient(base_url="http://localhost:7777")
        result = await client.list_vms()

        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == "/lume/vms" or "/lume/vms" in str(call_args)
        assert result == expected


@pytest.mark.asyncio
async def test_lume_client_connection_refused_clear_error():
    """LumeClient handles connection refused with clear error message."""
    with patch("soren.services.vm_manager.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_cls.return_value = mock_client

        client = LumeClient(base_url="http://localhost:7777")

        with pytest.raises(LumeConnectionError) as exc_info:
            await client.list_vms()

        assert "lume serve" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_lume_client_list_vms_parses_json():
    """LumeClient parses JSON response for list_vms."""
    expected = [
        {"name": "vm1", "status": "stopped"},
        {"name": "vm2", "status": "running", "vncUrl": "vnc://:x@host:5900"},
    ]
    with patch.object(LumeClient, "_request", new_callable=AsyncMock, return_value=expected):
        client = LumeClient()
        result = await client.list_vms()

        assert len(result) == 2
        assert result[0]["name"] == "vm1"
        assert result[1]["vncUrl"] == "vnc://:x@host:5900"


@pytest.mark.asyncio
async def test_lume_client_get_vm_parses_json():
    """LumeClient parses JSON response for get_vm."""
    expected = {
        "name": "soren-vm",
        "status": "running",
        "memory": "8GB",
        "vncUrl": "vnc://:secret@localhost:5900",
    }
    with patch.object(LumeClient, "_request", new_callable=AsyncMock, return_value=expected):
        client = LumeClient()
        result = await client.get_vm("soren-vm")

        assert result["name"] == "soren-vm"
        assert result["status"] == "running"
        assert "vnc" in result["vncUrl"].lower()


# --- VMManager tests ---


@pytest.mark.asyncio
async def test_vm_manager_ensure_ready_starts_when_not_running():
    """VMManager.ensure_ready() starts VM when not running."""
    config = VMConfig(name="test-vm", lume_url="http://localhost:7777")
    manager = VMManager(config)

    with patch.object(manager._client, "get_vm", side_effect=Exception("not found")):
        with patch.object(manager._client, "list_vms", return_value=[]):
            with patch.object(manager._client, "start_vm", new_callable=AsyncMock) as start:
                with patch.object(manager, "is_running", new_callable=AsyncMock) as is_run:
                    with patch.object(manager, "is_healthy", new_callable=AsyncMock) as is_healthy:
                        is_run.side_effect = [False, True]
                        is_healthy.return_value = True

                        await manager.ensure_ready(timeout_s=5)

                        start.assert_called_once_with("test-vm")


@pytest.mark.asyncio
async def test_vm_manager_ensure_ready_noop_when_running():
    """VMManager.ensure_ready() is a no-op when VM is already running."""
    config = VMConfig(name="test-vm")
    manager = VMManager(config)

    with patch.object(manager._client, "start_vm", new_callable=AsyncMock) as start:
        with patch.object(manager, "is_running", return_value=True):
            with patch.object(manager, "is_healthy", return_value=True):
                await manager.ensure_ready(timeout_s=5)
                start.assert_not_called()


@pytest.mark.asyncio
async def test_vm_manager_stop_sends_request():
    """VMManager.stop() sends stop request via HTTP API."""
    config = VMConfig(name="test-vm")
    manager = VMManager(config)

    with patch.object(manager._client, "stop_vm", new_callable=AsyncMock) as stop:
        await manager.stop()
        stop.assert_called_once_with("test-vm")


@pytest.mark.asyncio
async def test_vm_manager_is_running_true_when_running():
    """VMManager.is_running() returns True when VM state is running."""
    config = VMConfig(name="test-vm")
    manager = VMManager(config)

    with patch.object(manager._client, "get_vm", return_value={"status": "running"}):
        assert await manager.is_running() is True


@pytest.mark.asyncio
async def test_vm_manager_is_running_false_when_stopped():
    """VMManager.is_running() returns False when VM state is stopped."""
    config = VMConfig(name="test-vm")
    manager = VMManager(config)

    with patch.object(manager._client, "get_vm", return_value={"status": "stopped"}):
        assert await manager.is_running() is False


@pytest.mark.asyncio
async def test_vm_manager_is_running_false_on_connection_error():
    """VMManager.is_running() returns False on connection error."""
    config = VMConfig(name="test-vm")
    manager = VMManager(config)

    with patch.object(manager._client, "get_vm", side_effect=LumeConnectionError("unreachable")):
        assert await manager.is_running() is False


@pytest.mark.asyncio
async def test_vm_manager_wait_for_ready_retries_on_transient_failures():
    """VMManager._wait_for_ready() retries on transient failures."""
    config = VMConfig(name="test-vm")
    manager = VMManager(config)

    with (
        patch.object(manager, "is_healthy", new_callable=AsyncMock) as healthy,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        healthy.side_effect = [False, False, True]
        await manager._wait_for_ready(timeout_s=30)
        assert healthy.call_count == 3


@pytest.mark.asyncio
async def test_vm_manager_wait_for_ready_raises_timeout():
    """VMManager._wait_for_ready() raises TimeoutError after timeout."""
    config = VMConfig(name="test-vm")
    manager = VMManager(config)

    with patch.object(manager, "is_healthy", return_value=False):
        with pytest.raises(TimeoutError) as exc_info:
            await manager._wait_for_ready(timeout_s=1)
        assert "did not become ready" in str(exc_info.value)


@pytest.mark.asyncio
async def test_prepare_session_environment_noop_when_isolation_disabled():
    """prepare_session_environment() skips app/space changes when disabled."""
    config = VMConfig(name="test-vm", session_isolation_enabled=False)
    manager = VMManager(config)
    manager._computer = MagicMock()
    manager._computer.interface.run_command = AsyncMock()

    with patch.object(manager, "ensure_ready", new_callable=AsyncMock):
        result = await manager.prepare_session_environment()

    assert result.success is True
    assert result.attempted_new_space is False
    manager._computer.interface.run_command.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_session_environment_enforces_whitelist_and_opens_required_apps():
    """prepare_session_environment() closes non-whitelisted apps and opens required apps."""
    config = VMConfig(
        name="test-vm",
        allowed_apps=["Safari"],
        app_quit_grace_period_s=0.0,
        create_new_space_per_session=True,
    )
    manager = VMManager(config)
    manager._computer = MagicMock()
    manager._computer.interface.run_command = AsyncMock()

    with (
        patch.object(manager, "ensure_ready", new_callable=AsyncMock),
        patch.object(manager, "_attempt_new_space", new_callable=AsyncMock, return_value=True),
        patch.object(manager, "_list_foreground_apps", new_callable=AsyncMock) as list_apps,
        patch.object(manager, "_run_osascript", new_callable=AsyncMock) as run_script,
    ):
        list_apps.side_effect = [
            ["Safari", "Slack", "Python"],
            ["Safari"],
        ]
        run_script.side_effect = lambda script: "Safari" if "frontmost is true" in script else ""

        result = await manager.prepare_session_environment()

    assert result.success is True
    assert sorted(result.closed_apps) == ["Python", "Slack"]
    assert result.remaining_disallowed_apps == []
    manager._computer.interface.run_command.assert_called_once_with('open -a "Safari"')


# --- CLI tests (vm status, vm stop) ---


def test_cli_vm_status_displays_formatted_output():
    """CLI vm status displays formatted output (mock Lume API)."""
    from click.testing import CliRunner

    from soren.cli import cli

    runner = CliRunner()

    with patch("soren.cli.vm.load_settings") as load:
        load.return_value.vm = VMConfig()

        with patch(
            "soren.services.vm_manager.LumeClient._request",
            new_callable=AsyncMock,
            return_value=[
                {"name": "soren-vm", "status": "running", "memory": "8GB", "vncUrl": "vnc://..."},
            ],
        ):
            result = runner.invoke(cli, ["vm", "status"])

    assert result.exit_code == 0
    assert "soren-vm" in result.output or "Lume VMs" in result.output


def test_cli_vm_stop_sends_request():
    """CLI vm stop sends stop request (mock Lume API)."""
    from click.testing import CliRunner

    from soren.cli import cli

    runner = CliRunner()

    with patch("soren.cli.vm.load_settings") as load:
        load.return_value.vm = VMConfig()

        with patch(
            "soren.cli.vm.VMManager",
            spec=VMManager,
        ) as mock_vm_class:
            mock_manager = AsyncMock(spec=VMManager)
            mock_manager.is_running = AsyncMock(return_value=True)
            mock_manager.stop = AsyncMock()
            mock_vm_class.return_value = mock_manager

            result = runner.invoke(cli, ["vm", "stop"], input="y\n")

    assert result.exit_code == 0
    mock_manager.stop.assert_called_once()


def test_vm_config_lume_url_default():
    """VMConfig default lume_url is http://localhost:7777."""
    config = VMConfig()
    assert config.lume_url == "http://localhost:7777"
