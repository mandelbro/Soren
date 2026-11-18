"""VMManager — facade for the Soren VM: lifecycle via the Lume HTTP API, plus in-guest operations.

Callers use ``VMManager`` for everything VM-related. Lifecycle (start, health,
connect, recreate) lives here; in-guest work is delegated to components that
reach the VM through this manager as a ``GuestShell``:

- ``soren.services.vm_session_prep.SessionPreparer``: apps allowlist, Spaces, required apps
- ``soren.services.vm_browser.SafariBrowser``: Safari window reset and tab introspection
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from soren.models.reset import ResetResult, SessionPrepResult
from soren.models.vm import VMConfig
from soren.services.lume_client import LumeAPIError, LumeClient, LumeConnectionError
from soren.services.vm_browser import SafariBrowser
from soren.services.vm_session_prep import SessionPreparer

logger = logging.getLogger(__name__)


# --- VMManager ---


class VMManager:
    """Facade: VM lifecycle via the Lume HTTP API, plus session prep and Safari operations (delegated)."""

    def __init__(self, vm_config: VMConfig):
        self._config = vm_config
        self._client = LumeClient(base_url=vm_config.lume_url)
        self._vm_name = vm_config.name
        self._computer: Any = None
        self._cycle_count = 0
        self._contamination_detected = False
        self._session_prep = SessionPreparer(self, vm_config)
        self._browser = SafariBrowser(self)

    @property
    def vm_name(self) -> str:
        return self._vm_name

    async def list_vms(self) -> list[dict[str, Any]]:
        """List all VMs."""
        return await self._client.list_vms()

    async def get_vm(self, name: str | None = None) -> dict[str, Any]:
        """Get VM details."""
        return await self._client.get_vm(name or self._vm_name)

    async def is_running(self, name: str | None = None) -> bool:
        """Return True if VM state is "running"."""
        try:
            info = await self.get_vm(name or self._vm_name)
            status = info.get("status", "").lower()
            return "running" in status or status == "running"
        except LumeConnectionError:
            return False

    async def is_healthy(self) -> bool:
        """Check VM responsiveness via screenshot attempt (placeholder until WI 2.2)."""
        if not await self.is_running():
            return False
        try:
            info = await self.get_vm(self._vm_name)
            return bool(info.get("vncUrl") or info.get("status"))
        except Exception:
            return False

    async def ensure_ready(self, timeout_s: int = 120) -> None:
        """Start VM if not running, wait until responsive."""
        if await self.is_running():
            await self._wait_for_ready(timeout_s)
            return

        await self._client.start_vm(self._vm_name)
        await self._wait_for_ready(timeout_s)

    async def _wait_for_ready(self, timeout_s: int = 120) -> None:
        """Poll VM state with exponential backoff until responsive."""
        start = time.monotonic()
        delay = 2.0
        max_delay = 15.0

        while time.monotonic() - start < timeout_s:
            if await self.is_healthy():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, max_delay)

        raise TimeoutError(f"VM {self._vm_name} did not become ready within {timeout_s}s. Check Lume logs and VM boot.")

    async def stop(self) -> None:
        """Graceful shutdown via HTTP API."""
        await self._client.stop_vm(self._vm_name)
        self._computer = None

    async def connect_computer(self) -> Any:
        """Return CUA Computer connected to running VM (with VNC patch applied)."""
        from computer import Computer

        from soren.patches.vnc_screenshot import patch_computer_screenshot

        computer = Computer(
            os_type="macos",
            provider_type="lume",
            name=self._vm_name,
            display=self._config.display,
            memory=f"{self._config.memory_gb}GB",
        )
        await computer.run()
        patch_computer_screenshot(computer, self._client, self._vm_name)
        self._computer = computer
        return computer

    # --- GuestShell (used by SessionPreparer / SafariBrowser) ---

    @property
    def computer(self) -> Any:
        return self._computer

    async def ensure_connected(self) -> None:
        await self.ensure_ready()
        if not self._computer:
            await self.connect_computer()

    async def run_osascript(self, script: str) -> str:
        return await self._run_osascript(script)

    async def run_command(self, command: str) -> Any:
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        return await self._computer.interface.run_command(command)

    async def _run_osascript(self, script: str) -> str:
        """Execute osascript inside the VM. Raises on failure."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        escaped = script.replace("'", "'\\''")
        result = await self._computer.interface.run_command(f"osascript -e '{escaped}'")
        return (result.stdout or "").strip()

    # --- Session preparation (delegated to SessionPreparer) ---

    async def prepare_session_environment(self, required_apps: list[str] | None = None) -> SessionPrepResult:
        """Best-effort desktop isolation for a fresh automation session."""
        return await self._session_prep.prepare_session_environment(required_apps)

    async def open_required_apps(self, apps: list[str]) -> None:
        """Open ``apps`` in the VM without session-prep side effects (no Space, no quitting apps)."""
        await self._session_prep.open_required_apps(apps)

    # --- Safari windows and tabs (delegated to SafariBrowser) ---

    async def reset_browser_state(self) -> ResetResult:
        """Close all Safari windows and open a fresh one. Sessions preserved."""
        return await self._browser.reset_browser_state()

    async def get_tab_count(self) -> int:
        """Count open Safari tabs in window 1. Returns 0 on error."""
        return await self._browser.get_tab_count()

    async def get_active_tab_url(self) -> str:
        """URL of the currently active Safari tab. Returns empty string on error."""
        return await self._browser.get_active_tab_url()

    async def switch_to_tab(self, index: int) -> bool:
        """Switch to Safari tab by 1-based index. Returns True on success."""
        return await self._browser.switch_to_tab(index)

    async def find_tab_by_domain(self, domain: str) -> int | None:
        """Find Safari tab whose URL contains the given domain. Returns 1-based index or None."""
        return await self._browser.find_tab_by_domain(domain)

    async def wait_for_new_tab(self, initial_count: int, timeout_s: float = 5.0) -> bool:
        """Poll the tab count until it exceeds initial_count or timeout (0.5s interval)."""
        return await self._browser.wait_for_new_tab(initial_count, timeout_s)

    # --- VM recreation ---

    def _should_recreate(self) -> bool:
        """Return True if full VM recreation is due (N-cycle interval or contamination)."""
        if self._contamination_detected:
            return True
        n = self._config.recreate_every_n_cycles
        return n > 0 and self._cycle_count > 0 and self._cycle_count % n == 0

    def _increment_cycle_count(self) -> None:
        """Increment cycle count (called after each cycle). Persisted in memory only."""
        self._cycle_count += 1

    def notify_cycle_complete(self) -> bool:
        """Increment cycle count and return True if VM recreation is due."""
        self._increment_cycle_count()
        return self._should_recreate()

    def mark_contamination(self) -> None:
        """Mark VM as contaminated; next check will trigger recreation."""
        self._contamination_detected = True

    async def recreate_vm(self, timeout_s: int = 120) -> None:
        """Stop active VM, delete, clone from pristine, start, wait for ready, re-patch VNC."""
        # Force-stop: the VM is deleted next, so the graceful shutdown wait buys nothing.
        try:
            await self._client.stop_vm(self._vm_name, force=True)
        except LumeAPIError as e:
            if "not running" not in e.message.lower():
                raise
            logger.info("VM %s already stopped before recreation", self._vm_name)
        self._computer = None
        await asyncio.sleep(2)

        # Delete active VM
        await self._client.delete_vm(self._vm_name)
        await asyncio.sleep(1)

        # Clone from pristine
        await self._client.clone_vm(
            source=self._config.pristine_name,
            target=self._vm_name,
        )
        await asyncio.sleep(2)

        # Start the fresh clone
        await self._client.start_vm(self._vm_name)
        await self._wait_for_ready(timeout_s)

        # Re-patch VNC (new port on boot)
        await self.connect_computer()
        await self.prepare_session_environment()
        self._contamination_detected = False
