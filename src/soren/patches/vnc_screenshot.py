"""VNC screenshot patch for CUA Computer — bypasses stale CGDisplayCreateImage in Lume VMs.

Also patches ``interface.left_click``, ``interface.right_click``, and
``interface.double_click`` to rescale coordinates from VNC-image space to
screen-logical space.

Background
----------
The UITARS agent loop (``agent/loops/uitars.py``) takes a screenshot via
``computer_handler.screenshot()`` — which now calls our VNC-patched method —
and feeds those pixels to the VLM.  The VLM returns 0-1000 normalised
coordinates.  ``convert_to_computer_actions`` scales them by
``original_width / original_height`` (the VNC image dimensions) to get
absolute pixel coordinates, then passes them straight to
``interface.left_click(x, y)``.

The Computer API Server inside the Lume VM expects *screen-logical*
coordinates (e.g. 1920×1080).  If the VNC framebuffer is captured at a
different resolution (e.g. Retina 2×: 3840×2160), every click lands at
roughly double the intended position — mostly off-screen or on the wrong
element.

The fix: intercept ``left_click`` / ``right_click`` / ``double_click`` and
rescale the incoming coordinates from VNC-image space to screen-logical
space using the ratio ``screen_size / vnc_image_size``.  We cache the VNC
image dimensions and screen size after the first screenshot to avoid
redundant async calls on every click.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

VNC_URL_RE = re.compile(r"vnc://:([^@]+)@([^:]+):(\d+)")

logger = logging.getLogger(__name__)


def _cursor_warp_delay() -> float:
    """Read the cursor warp pause from env (default 0.4s, 0 disables warp).

    The warp pause makes the cursor visible on the VM Screen Sharing window
    just before each click, giving the operator instant visual confirmation of
    where the model is targeting. Set ``SOREN_CURSOR_WARP_DELAY=0`` to disable
    (e.g. for unattended runs where latency matters).
    """
    raw = os.environ.get("SOREN_CURSOR_WARP_DELAY", "0.4")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.4


def _parse_vnc_url(vnc_url: str) -> tuple[str, int, str]:
    """Parse VNC URL vnc://:password@host:port into (host, port, password)."""
    if not vnc_url:
        raise ValueError("VNC URL is empty")
    m = VNC_URL_RE.match(vnc_url.strip())
    if not m:
        raise ValueError(f"Invalid VNC URL format: {vnc_url!r}. Expected vnc://:password@host:port")
    password, host, port_str = m.groups()
    return host, int(port_str), password


async def _discover_vnc_url(lume_client: Any, vm_name: str) -> tuple[str, int, str]:
    """Parse VNC host, port, password from Lume API VM details."""
    info = await lume_client.get_vm(vm_name)
    vnc_url = info.get("vncUrl") or info.get("vnc_url") or ""
    return _parse_vnc_url(vnc_url)


def _capture_vnc_screenshot(host: str, port: int, password: str, timeout_s: int = 10) -> bytes:
    """Capture screenshot via vncdo (vncdotool), return PNG bytes."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        out_path = f.name
    try:
        # vncdo -s host::port -p password capture output.png
        subprocess.run(
            [
                "vncdo",
                "-s",
                f"{host}::{port}",
                "-p",
                password,
                "-t",
                str(timeout_s),
                "capture",
                out_path,
            ],
            check=True,
            capture_output=True,
            timeout=timeout_s + 2,
        )
        return Path(out_path).read_bytes()
    except FileNotFoundError as e:
        raise RuntimeError("vncdo not found. Install vncdotool: pip install vncdotool (or uv add vncdotool)") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"VNC screenshot timed out after {timeout_s}s (host={host}, port={port})") from e
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        raise RuntimeError(f"VNC screenshot failed: {err}") from e
    finally:
        Path(out_path).unlink(missing_ok=True)


def patch_computer_screenshot(computer: Any, lume_client: Any, vm_name: str) -> None:
    """Replace screenshot and coordinate-issuing methods on computer.interface.

    Patches applied:
    1. ``screenshot()`` — replaced with VNC capture via vncdotool so the VLM
       receives a fresh, accurate framebuffer image.
    2. ``left_click(x, y)``, ``right_click(x, y)``, ``double_click(x, y)`` —
       wrapped to rescale coordinates from VNC-image space to screen-logical
       space before forwarding to the Computer API Server.

    The rescaling is necessary because the UITARS loop scales VLM coordinates
    by the VNC image dimensions, but the Computer API Server expects
    screen-logical coordinates.  If these differ (e.g. Retina 2x framebuffer),
    every click lands at the wrong position.
    """
    import asyncio

    if not (hasattr(computer, "interface") and computer.interface is not None):
        return

    interface = computer.interface

    # --- Coordinate scaling state (populated lazily after first screenshot) ---
    _coord_scale: dict[str, float] = {"x": 1.0, "y": 1.0}
    _scale_initialised: list[bool] = [False]

    async def _vnc_screenshot() -> bytes:
        host, port, password = await _discover_vnc_url(lume_client, vm_name)
        loop = asyncio.get_event_loop()
        screenshot_bytes = await loop.run_in_executor(
            None,
            lambda: _capture_vnc_screenshot(host, port, password),
        )

        # Lazily compute the VNC-image → screen-logical scale factors.
        # We only need to do this once per session; the VM resolution doesn't
        # change during a training run.
        if not _scale_initialised[0]:
            try:
                from io import BytesIO

                from PIL import Image

                img = Image.open(BytesIO(screenshot_bytes))
                vnc_w, vnc_h = img.size

                # Ask the Computer API Server for the logical screen dimensions.
                # ``get_screen_size`` is a WebSocket command that bypasses VNC.
                screen = await interface.get_screen_size()
                screen_w = screen.get("width", vnc_w)
                screen_h = screen.get("height", vnc_h)

                _coord_scale["x"] = screen_w / vnc_w if vnc_w else 1.0
                _coord_scale["y"] = screen_h / vnc_h if vnc_h else 1.0
                _scale_initialised[0] = True

                logger.info(
                    "VNC coordinate scale: vnc=%dx%d screen=%dx%d scale=%.4fx%.4f",
                    vnc_w,
                    vnc_h,
                    screen_w,
                    screen_h,
                    _coord_scale["x"],
                    _coord_scale["y"],
                )
            except Exception:
                logger.warning(
                    "VNC coordinate scale initialisation failed; defaulting to 1:1",
                    exc_info=True,
                )
                _scale_initialised[0] = True  # don't retry on every screenshot

        return screenshot_bytes

    # Replace screenshot
    interface.screenshot = _vnc_screenshot

    # --- Click/coordinate wrappers ---

    _orig_left_click = interface.left_click
    _orig_right_click = interface.right_click
    _orig_double_click = interface.double_click
    _orig_move_cursor = getattr(interface, "move_cursor", None)

    def _scale_xy(x: Any, y: Any) -> tuple[int, int]:
        """Scale (x, y) from VNC-image space to screen-logical space."""
        sx = round(x * _coord_scale["x"]) if x is not None else x
        sy = round(y * _coord_scale["y"]) if y is not None else y
        return sx, sy

    async def _warp_cursor(sx: Any, sy: Any) -> None:
        """Move the VM cursor to the target before dispatching the click.

        Makes click targets visible on the Screen Sharing window so the operator
        can immediately see whether the model is aiming at the right element.
        Silently no-ops when ``move_cursor`` is unavailable on the interface.
        """
        if _orig_move_cursor is None or sx is None or sy is None:
            return
        delay = _cursor_warp_delay()
        try:
            await _orig_move_cursor(sx, sy)
        except Exception:
            logger.debug("Cursor warp failed at (%s, %s); proceeding with click", sx, sy, exc_info=True)
            return
        if delay > 0:
            import asyncio

            await asyncio.sleep(delay)

    async def _left_click_scaled(x: Any = None, y: Any = None, **kwargs: Any) -> None:
        sx, sy = _scale_xy(x, y)
        logger.info("VM left_click vnc=(%s,%s) screen=(%s,%s)", x, y, sx, sy)
        await _warp_cursor(sx, sy)
        await _orig_left_click(sx, sy, **kwargs)

    async def _right_click_scaled(x: Any = None, y: Any = None, **kwargs: Any) -> None:
        sx, sy = _scale_xy(x, y)
        logger.info("VM right_click vnc=(%s,%s) screen=(%s,%s)", x, y, sx, sy)
        await _warp_cursor(sx, sy)
        await _orig_right_click(sx, sy, **kwargs)

    async def _double_click_scaled(x: Any = None, y: Any = None, **kwargs: Any) -> None:
        sx, sy = _scale_xy(x, y)
        logger.info("VM double_click vnc=(%s,%s) screen=(%s,%s)", x, y, sx, sy)
        await _warp_cursor(sx, sy)
        await _orig_double_click(sx, sy, **kwargs)

    interface.left_click = _left_click_scaled
    interface.right_click = _right_click_scaled
    interface.double_click = _double_click_scaled
