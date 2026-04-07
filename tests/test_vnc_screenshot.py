"""Tests for VNC screenshot patch."""

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


class TestPatchComputerClickScaling:
    """Tests for the coordinate rescaling wrappers added alongside screenshot patch."""

    @pytest.mark.asyncio
    async def test_click_methods_replaced(self):
        """patch_computer_screenshot must replace left_click, right_click, double_click."""
        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.right_click = AsyncMock()
        interface.double_click = AsyncMock()
        computer.interface = interface

        orig_left = interface.left_click
        orig_right = interface.right_click
        orig_double = interface.double_click

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        assert interface.left_click is not orig_left
        assert interface.right_click is not orig_right
        assert interface.double_click is not orig_double

    @pytest.mark.asyncio
    async def test_click_passes_through_at_unity_scale(self):
        """When VNC resolution == screen resolution, coordinates are unchanged."""
        import io

        from PIL import Image

        # Build a 1920x1080 PNG in memory to simulate VNC screenshot
        buf = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # Keep a reference to the original mock BEFORE patch replaces it
        orig_left_click = AsyncMock()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = orig_left_click
        interface.get_screen_size = AsyncMock(return_value={"width": 1920, "height": 1080})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch(
            "soren.patches.vnc_screenshot._capture_vnc_screenshot",
            return_value=png_bytes,
        ):
            # Trigger screenshot to initialise scale factors
            await interface.screenshot()
            # Now perform a click via the patched method
            await interface.left_click(960, 540)

        # The wrapper must have forwarded (960, 540) unchanged to the original
        orig_left_click.assert_awaited_once_with(960, 540)

    @pytest.mark.asyncio
    async def test_click_rescales_at_2x_retina(self):
        """When VNC is 2x Retina (3840x2160) vs screen (1920x1080), coordinates are halved."""
        import io

        from PIL import Image

        # Build a 3840x2160 PNG to simulate a Retina VNC framebuffer
        buf = io.BytesIO()
        Image.new("RGB", (3840, 2160)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # Keep a reference to the original mock BEFORE patch replaces it
        orig_left_click = AsyncMock()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = orig_left_click
        interface.get_screen_size = AsyncMock(return_value={"width": 1920, "height": 1080})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch(
            "soren.patches.vnc_screenshot._capture_vnc_screenshot",
            return_value=png_bytes,
        ):
            await interface.screenshot()
            # VLM target (1920, 1080) in 3840x2160 VNC space => screen (960, 540)
            await interface.left_click(1920, 1080)

        # Coordinates must have been halved before forwarding to the original
        orig_left_click.assert_awaited_once_with(960, 540)

    @pytest.mark.asyncio
    async def test_click_scale_initialised_only_once(self):
        """Scale is computed on the first screenshot only, not on subsequent calls."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.get_screen_size = AsyncMock(return_value={"width": 1920, "height": 1080})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch(
            "soren.patches.vnc_screenshot._capture_vnc_screenshot",
            return_value=png_bytes,
        ):
            await interface.screenshot()
            await interface.screenshot()
            await interface.screenshot()

        # get_screen_size should have been called exactly once
        assert interface.get_screen_size.await_count == 1


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
