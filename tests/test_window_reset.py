"""Tests for window management reset and post-reset verification."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.reset import ResetResult
from soren.models.vm import VMConfig
from soren.services.reset_verification import verify_reset
from soren.services.vm_manager import VMManager

# --- ResetResult tests ---


def test_reset_result_model():
    """ResetResult captures duration and window count."""
    r = ResetResult(
        success=True,
        duration_seconds=3.5,
        window_count=1,
        warnings=[],
    )
    assert r.success is True
    assert r.duration_seconds == 3.5
    assert r.window_count == 1
    assert r.warnings == []


def test_reset_result_with_warnings():
    """ResetResult captures warnings."""
    r = ResetResult(
        success=False,
        duration_seconds=5.0,
        window_count=2,
        warnings=["Expected 1 Safari window, got 2"],
    )
    assert r.success is False
    assert len(r.warnings) == 1


# --- verify_reset tests ---


@pytest.mark.asyncio
async def test_verify_reset_passes_with_one_window_blank_page():
    """Verification passes with exactly one window on blank page."""
    mock_iface = AsyncMock()
    mock_iface.run_command = AsyncMock(
        side_effect=[
            MagicMock(stdout="1", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
        ]
    )
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    issues = await verify_reset(mock_computer)
    assert issues == []


@pytest.mark.asyncio
async def test_verify_reset_detects_multiple_windows():
    """Verification detects multiple windows open."""
    mock_iface = AsyncMock()
    mock_iface.run_command = AsyncMock(return_value=MagicMock(stdout="3", stderr="", returncode=0))
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    issues = await verify_reset(mock_computer)
    assert any("Expected 1" in i and "got 3" in i for i in issues)


@pytest.mark.asyncio
async def test_verify_reset_detects_zero_windows():
    """Verification detects zero windows (Safari not running)."""
    mock_iface = AsyncMock()
    mock_iface.run_command = AsyncMock(return_value=MagicMock(stdout="0", stderr="", returncode=0))
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    issues = await verify_reset(mock_computer)
    assert any("Expected 1" in i and "got 0" in i for i in issues)


@pytest.mark.asyncio
async def test_verify_reset_detects_non_blank_url():
    """Verification detects window not on start/blank page."""
    mock_iface = AsyncMock()
    mock_iface.run_command = AsyncMock(
        side_effect=[
            MagicMock(stdout="1", stderr="", returncode=0),
            MagicMock(stdout="https://example.com/task-page", stderr="", returncode=0),
        ]
    )
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    issues = await verify_reset(mock_computer)
    assert any("start/blank" in i or "example.com" in i for i in issues)


# --- VMManager.reset_browser_state tests ---


@pytest.mark.asyncio
async def test_reset_sequence_sends_close_command():
    """Reset sequence sends osascript close-all-windows command."""
    config = VMConfig(name="test-vm", pristine_name="test-pristine")
    manager = VMManager(config)

    mock_run = AsyncMock(
        side_effect=[
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
        ]
    )
    mock_iface = MagicMock()
    mock_iface.run_command = mock_run
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    with (
        patch.object(manager, "ensure_ready", new_callable=AsyncMock),
        patch.object(manager, "connect_computer", new_callable=AsyncMock, return_value=mock_computer),
    ):
        manager._computer = mock_computer
        await manager.reset_browser_state()

    assert any("close every window" in str(c) for c in mock_run.call_args_list)


@pytest.mark.asyncio
async def test_reset_sequence_sends_open_command():
    """Reset sequence sends osascript open-new-window command."""
    config = VMConfig(name="test-vm", pristine_name="test-pristine")
    manager = VMManager(config)

    mock_run = AsyncMock(
        side_effect=[
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
        ]
    )
    mock_iface = MagicMock()
    mock_iface.run_command = mock_run
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    with (
        patch.object(manager, "ensure_ready", new_callable=AsyncMock),
        patch.object(manager, "connect_computer", new_callable=AsyncMock, return_value=mock_computer),
    ):
        manager._computer = mock_computer
        await manager.reset_browser_state()

    assert any("make new document" in str(c) for c in mock_run.call_args_list)


@pytest.mark.asyncio
async def test_reset_verifies_window_count():
    """Reset verifies window count after close/open."""
    config = VMConfig(name="test-vm", pristine_name="test-pristine")
    manager = VMManager(config)

    # close, open, verify (count+urls), final count
    mock_run = AsyncMock(
        side_effect=[
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
        ]
    )
    mock_iface = MagicMock()
    mock_iface.run_command = mock_run
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    with (
        patch.object(manager, "ensure_ready", new_callable=AsyncMock),
        patch.object(manager, "connect_computer", new_callable=AsyncMock, return_value=mock_computer),
    ):
        manager._computer = mock_computer
        result = await manager.reset_browser_state()

    assert result.window_count == 1
    assert result.success is True


@pytest.mark.asyncio
async def test_reset_fallback_on_osascript_failure():
    """Reset falls back to pkill + reopen when osascript fails."""
    config = VMConfig(name="test-vm", pristine_name="test-pristine")
    manager = VMManager(config)

    call_count = 0

    def run_side_effect(cmd):
        nonlocal call_count
        call_count += 1
        if "osascript" in cmd and "close" in cmd:
            raise RuntimeError("osascript failed")
        if "pkill" in cmd:
            return MagicMock(stdout="", stderr="", returncode=0)
        return MagicMock(stdout="1", stderr="", returncode=0)

    mock_run = AsyncMock(side_effect=run_side_effect)
    mock_iface = MagicMock()
    mock_iface.run_command = mock_run
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    with (
        patch.object(manager, "ensure_ready", new_callable=AsyncMock),
        patch.object(manager, "connect_computer", new_callable=AsyncMock, return_value=mock_computer),
    ):
        manager._computer = mock_computer
        result = await manager.reset_browser_state()

    assert any("pkill" in str(c) for c in mock_run.call_args_list)
    assert any("osascript" in str(w) for w in result.warnings) or "pkill" in str(mock_run.call_args_list)


@pytest.mark.asyncio
async def test_reset_cookies_preserved():
    """Reset does not delete cookies/localStorage (window management only)."""
    config = VMConfig(name="test-vm", pristine_name="test-pristine")
    manager = VMManager(config)

    mock_run = AsyncMock(
        side_effect=[
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="1", stderr="", returncode=0),
        ]
    )
    mock_iface = MagicMock()
    mock_iface.run_command = mock_run
    mock_computer = MagicMock()
    mock_computer.interface = mock_iface

    with (
        patch.object(manager, "ensure_ready", new_callable=AsyncMock),
        patch.object(manager, "connect_computer", new_callable=AsyncMock, return_value=mock_computer),
    ):
        manager._computer = mock_computer
        await manager.reset_browser_state()

    # No cookie/localStorage deletion commands
    for call in mock_run.call_args_list:
        cmd = call[0][0] if call[0] else ""
        assert "rm " not in cmd or "Cookies" not in cmd
        assert "localStorage" not in cmd.lower() or "rm" not in cmd


# --- CLI vm reset tests ---


def test_vm_reset_displays_results():
    """CLI vm reset displays results with status indicators."""
    from click.testing import CliRunner

    from soren.cli import cli

    runner = CliRunner()
    with patch("soren.cli.vm.load_settings") as load:
        load.return_value.vm = VMConfig()
        with patch("soren.cli.vm.VMManager") as mock_vm_cls:
            mock_manager = AsyncMock()
            mock_manager.reset_browser_state = AsyncMock(
                return_value=ResetResult(
                    success=True,
                    duration_seconds=3.2,
                    window_count=1,
                    warnings=[],
                )
            )
            mock_vm_cls.return_value = mock_manager
            result = runner.invoke(cli, ["vm", "reset"])
    assert result.exit_code == 0
    assert "Browser reset" in result.output
    assert "3.2" in result.output or "3" in result.output
