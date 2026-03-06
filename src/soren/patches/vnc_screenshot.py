"""VNC screenshot patch for CUA Computer — bypasses stale CGDisplayCreateImage in Lume VMs."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

VNC_URL_RE = re.compile(r"vnc://:([^@]+)@([^:]+):(\d+)")


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
    """Replace computer.interface.screenshot() with VNC capture.

    The CUA SDK's CGDisplayCreateImage returns stale framebuffer data in
    Apple Virtualization Framework VMs. This patch uses vncdotool to capture
    fresh screenshots from the guest OS.
    """
    import asyncio

    async def _vnc_screenshot() -> bytes:
        host, port, password = await _discover_vnc_url(lume_client, vm_name)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: _capture_vnc_screenshot(host, port, password),
        )

    # Replace the interface's screenshot method
    if hasattr(computer, "interface") and computer.interface is not None:
        computer.interface.screenshot = _vnc_screenshot
