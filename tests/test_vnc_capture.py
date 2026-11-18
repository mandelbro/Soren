"""VNC screenshot capture: URL parsing, vncdo capture, screenshot patching, and computer connection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.patches.vnc_screenshot import (
    VNC_URL_RE,
    _capture_vnc_screenshot,
    _parse_vnc_url,
    patch_computer_screenshot,
)

# Sleeps run on a virtual clock (tests/conftest.py::vnc_virtual_clock).
pytestmark = pytest.mark.usefixtures("vnc_virtual_clock")


class TestParseVncUrl:
    def test_extracts_host_port_password(self):
        host, port, password = _parse_vnc_url("vnc://:secret@localhost:5900")
        assert host == "localhost"
        assert port == 5900
        assert password == "secret"

    def test_handles_missing_password_gracefully(self):
        with pytest.raises(ValueError, match="Invalid VNC URL"):
            _parse_vnc_url("vnc://localhost:5900")

    def test_raises_on_malformed_url(self):
        with pytest.raises(ValueError, match="Invalid VNC URL"):
            _parse_vnc_url("not-a-vnc-url")

    def test_vnc_url_re_matches_standard_format(self):
        m = VNC_URL_RE.match("vnc://:pw123@host.example.com:5901")
        assert m
        assert m.group(1) == "pw123"
        assert m.group(2) == "host.example.com"
        assert m.group(3) == "5901"


class TestCaptureVncScreenshot:
    def test_invokes_vncdo_with_correct_args(self):
        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"x")  # write so read_bytes succeeds
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            _capture_vnc_screenshot("localhost", 5900, "secret")
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "vncdo" or "vncdo" in str(call_args)
            assert "-s" in call_args
            assert "localhost::5900" in call_args
            assert "-p" in call_args
            assert "secret" in call_args
            assert "capture" in call_args

    def test_returns_png_bytes_on_success(self):
        png_data = b"\x89PNG\r\n\x1a\n"

        def fake_run(cmd, **kwargs):
            out_path = cmd[-1]  # capture output file is last arg
            Path(out_path).write_bytes(png_data)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            result = _capture_vnc_screenshot("localhost", 5900, "pw")
        assert result == png_data

    def test_raises_on_vncdo_timeout(self):
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("vncdo", 10)
            with pytest.raises(RuntimeError, match="timed out"):
                _capture_vnc_screenshot("localhost", 5900, "pw")

    def test_raises_on_vncdo_not_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(RuntimeError, match="vncdo not found"):
                _capture_vnc_screenshot("localhost", 5900, "pw")


class TestPatchComputerScreenshot:
    @pytest.mark.asyncio
    async def test_replaces_screenshot_method(self):
        computer = MagicMock()
        computer.interface = MagicMock()
        computer.interface.screenshot = AsyncMock(return_value=b"original")

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        assert computer.interface.screenshot != b"original"
        assert callable(computer.interface.screenshot)

    @pytest.mark.asyncio
    async def test_patched_screenshot_returns_vnc_capture(self):
        computer = MagicMock()
        computer.interface = MagicMock()

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch(
            "soren.patches.vnc_screenshot._capture_vnc_screenshot",
            return_value=b"vnc-png-data",
        ):
            result = await computer.interface.screenshot()
        assert result == b"vnc-png-data"


@pytest.mark.usefixtures("fake_cua_sdk")
class TestConnectComputer:
    @pytest.mark.asyncio
    async def test_connect_computer_creates_with_correct_args(self):
        from soren.models.vm import VMConfig
        from soren.services.vm_manager import VMManager

        config = VMConfig(name="soren-vm", memory_gb=8, display="1280x720")
        manager = VMManager(config)

        with patch("computer.Computer") as mock_computer_cls:
            mock_computer = MagicMock()
            mock_computer.run = AsyncMock()
            mock_computer_cls.return_value = mock_computer

            result = await manager.connect_computer()

            mock_computer_cls.assert_called_once()
            call_kw = mock_computer_cls.call_args[1]
            assert call_kw["os_type"] == "macos"
            assert call_kw["provider_type"] == "lume"
            assert call_kw["name"] == "soren-vm"
            assert call_kw["display"] == "1280x720"
            assert call_kw["memory"] == "8GB"
            assert result == mock_computer

    @pytest.mark.asyncio
    async def test_connect_computer_applies_vnc_patch(self):
        from soren.models.vm import VMConfig
        from soren.services.vm_manager import VMManager

        config = VMConfig(name="soren-vm")
        manager = VMManager(config)

        with (
            patch("computer.Computer") as mock_computer_cls,
            patch(
                "soren.patches.vnc_screenshot.patch_computer_screenshot",
            ) as mock_patch,
        ):
            mock_computer = MagicMock()
            mock_computer.run = AsyncMock()
            mock_computer_cls.return_value = mock_computer

            await manager.connect_computer()

            mock_patch.assert_called_once_with(mock_computer, manager._client, "soren-vm")
