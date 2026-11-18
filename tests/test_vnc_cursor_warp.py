"""Cursor warp before clicks (operator visibility) in the VNC patch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.patches.vnc_screenshot import (
    patch_computer_screenshot,
)

# Sleeps run on a virtual clock (tests/conftest.py::vnc_virtual_clock).
pytestmark = pytest.mark.usefixtures("vnc_virtual_clock")


class TestCursorWarpObservability:
    """Cursor warp before click — visible feedback in the VM Screen Sharing window.

    Behavior under test: each click wrapper must call ``interface.move_cursor`` at
    the *post-rescaling* coordinates and pause briefly before dispatching the click.
    The user watching the VM via Screen Sharing then sees the cursor jump to the
    intended target — instant visual confirmation of where the model is aiming.
    """

    @pytest.mark.asyncio
    async def test_left_click_warps_cursor_before_click(self):
        """left_click wrapper must move_cursor to the rescaled coords first."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        call_order: list[str] = []

        async def fake_move_cursor(x, y, delay=None):  # noqa: ARG001
            call_order.append(f"move:{x},{y}")

        async def fake_left_click(x, y, **kwargs):  # noqa: ARG001
            call_order.append(f"click:{x},{y}")

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock(side_effect=fake_left_click)
        interface.move_cursor = AsyncMock(side_effect=fake_move_cursor)
        interface.get_screen_size = AsyncMock(return_value={"width": 1920, "height": 1080})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch(
            "soren.patches.vnc_screenshot._capture_vnc_screenshot",
            return_value=png_bytes,
        ):
            await interface.screenshot()  # init scale
            await interface.left_click(960, 540)

        assert call_order == ["move:960,540", "click:960,540"], f"Expected cursor warp before click; got {call_order}"

    @pytest.mark.asyncio
    async def test_right_click_warps_cursor_before_click(self):
        """right_click wrapper must also warp the cursor first."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        call_order: list[str] = []

        async def fake_move(x, y, delay=None):  # noqa: ARG001
            call_order.append(f"move:{x},{y}")

        async def fake_right(x, y, **kwargs):  # noqa: ARG001
            call_order.append(f"click:{x},{y}")

        computer = MagicMock()
        interface = MagicMock()
        interface.right_click = AsyncMock(side_effect=fake_right)
        interface.move_cursor = AsyncMock(side_effect=fake_move)
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
            await interface.right_click(100, 200)

        assert call_order == ["move:100,200", "click:100,200"]

    @pytest.mark.asyncio
    async def test_double_click_warps_cursor_before_click(self):
        """double_click wrapper must also warp the cursor first."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        call_order: list[str] = []

        async def fake_move(x, y, delay=None):  # noqa: ARG001
            call_order.append(f"move:{x},{y}")

        async def fake_double(x, y, **kwargs):  # noqa: ARG001
            call_order.append(f"click:{x},{y}")

        computer = MagicMock()
        interface = MagicMock()
        interface.double_click = AsyncMock(side_effect=fake_double)
        interface.move_cursor = AsyncMock(side_effect=fake_move)
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
            await interface.double_click(50, 75)

        assert call_order == ["move:50,75", "click:50,75"]

    @pytest.mark.asyncio
    async def test_click_warp_uses_post_rescale_coordinates(self):
        """Cursor warp must use the rescaled (screen-logical) coords, not the raw VNC coords."""
        import io

        from PIL import Image

        # 2x Retina VNC framebuffer vs 1920x1080 logical screen
        buf = io.BytesIO()
        Image.new("RGB", (3840, 2160)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        warp_target: dict[str, int] = {}

        async def fake_move(x, y, delay=None):  # noqa: ARG001
            warp_target["x"] = x
            warp_target["y"] = y

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.move_cursor = AsyncMock(side_effect=fake_move)
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

        assert warp_target == {"x": 960, "y": 540}, f"Expected warp at rescaled coords (960, 540); got {warp_target}"

    @pytest.mark.asyncio
    async def test_warp_skipped_when_move_cursor_unavailable(self):
        """If interface has no move_cursor attribute the click must still dispatch."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        orig_left = AsyncMock()

        computer = MagicMock(spec=["interface"])
        # spec=["screenshot", "left_click", "right_click", "double_click", "get_screen_size"]
        # leaves move_cursor genuinely missing (AttributeError on access)
        interface = MagicMock(spec=["screenshot", "left_click", "right_click", "double_click", "get_screen_size"])
        interface.left_click = orig_left
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
            await interface.left_click(100, 200)  # must not raise

        orig_left.assert_awaited_once_with(100, 200)

    @pytest.mark.asyncio
    async def test_warp_failure_logged_at_warning(self, caplog):
        """A move_cursor exception must surface at WARNING, not be swallowed at DEBUG.

        Regression guard against silent-failure anti-pattern: the first attempt
        at this patch logged warp failures at DEBUG, which meant the training
        run log gave no hint that move_cursor was broken on the VM side.
        """
        import io
        import logging

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # Hold the original AsyncMock ref because patch_computer_screenshot
        # wraps left_click in a scaled/warped closure.
        orig_left_click = AsyncMock()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = orig_left_click
        interface.move_cursor = AsyncMock(side_effect=RuntimeError("VM rejected move_cursor"))
        interface.get_screen_size = AsyncMock(return_value={"width": 1920, "height": 1080})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with (
            caplog.at_level(logging.WARNING, logger="soren.patches.vnc_screenshot"),
            patch(
                "soren.patches.vnc_screenshot._capture_vnc_screenshot",
                return_value=png_bytes,
            ),
        ):
            await interface.screenshot()
            await interface.left_click(500, 300)

        warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING and "Cursor warp failed" in r.getMessage()
        ]
        assert warnings, f"Expected WARNING 'Cursor warp failed' log; got {[r.getMessage() for r in caplog.records]}"
        assert "VM rejected move_cursor" in warnings[0].getMessage()
        # Click must still dispatch even when warp fails
        orig_left_click.assert_awaited_once_with(500, 300)

    @pytest.mark.asyncio
    async def test_missing_move_cursor_logged_at_warning(self, caplog):
        """When the interface has no move_cursor at patch time, emit a WARNING at patch time.

        This turns a silent cursor-warp no-op into a visible signal in the
        training log: if the user re-runs and doesn't see any "Cursor warp
        enabled" line, they know the warp was never wired up.
        """
        import logging

        computer = MagicMock(spec=["interface"])
        interface = MagicMock(spec=["screenshot", "left_click", "right_click", "double_click", "get_screen_size"])
        interface.left_click = AsyncMock()
        interface.get_screen_size = AsyncMock(return_value={"width": 1920, "height": 1080})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        with caplog.at_level(logging.WARNING, logger="soren.patches.vnc_screenshot"):
            patch_computer_screenshot(computer, lume_client, "test-vm")

        warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING and "Cursor warp disabled" in r.getMessage()
        ]
        assert warnings, f"Expected WARNING 'Cursor warp disabled' log; got {[r.getMessage() for r in caplog.records]}"
