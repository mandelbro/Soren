"""VNC click coordinate rescaling from VNC-image space to screen-logical space."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.patches.vnc_screenshot import (
    patch_computer_screenshot,
)

# Sleeps run on a virtual clock (tests/conftest.py::vnc_virtual_clock).
pytestmark = pytest.mark.usefixtures("vnc_virtual_clock")


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
