"""VMManager and Lume HTTP API client for Soren VM lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from soren.models.reset import ResetResult, SessionPrepResult
from soren.models.vm import VMConfig
from soren.services.reset_verification import verify_reset

logger = logging.getLogger(__name__)


class LumeConnectionError(Exception):
    """Raised when Lume HTTP API is unreachable."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


# --- Lume API response models ---


class VMInfo(BaseModel):
    """VM details from Lume API."""

    name: str
    status: str = ""
    memory: str = ""
    display: str = ""
    vncUrl: str = ""
    ip: str = ""


# --- LumeClient ---


class LumeClient:
    """Async wrapper around Lume's REST API at http://localhost:7777."""

    def __init__(self, base_url: str = "http://localhost:7777", timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self._base_url}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Execute HTTP request, handling connection errors."""
        url = self._url(path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(method, url, json=json, params=params)
                resp.raise_for_status()
                return resp.json() if resp.content else {}
        except httpx.ConnectError as e:
            raise LumeConnectionError(
                f"Cannot connect to Lume API at {self._base_url}. Is `lume serve` running?",
                cause=e,
            ) from e
        except httpx.TimeoutException as e:
            raise LumeConnectionError(
                f"Lume API request timed out after {self._timeout}s.",
                cause=e,
            ) from e

    async def _request_dict(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute request for endpoints that return a JSON object."""
        result = await self._request(method, path, json=json, params=params)
        return result if isinstance(result, dict) else {}

    async def list_vms(self, storage: str | None = None) -> list[dict[str, Any]]:
        """List all VMs."""
        params = {"storage": storage} if storage else None
        result = await self._request("GET", "/lume/vms", params=params)
        return result if isinstance(result, list) else []

    async def get_vm(self, name: str, storage: str | None = None) -> dict[str, Any]:
        """Get VM details by name."""
        params = {"storage": storage} if storage else None
        return await self._request_dict("GET", f"/lume/vms/{name}", params=params)

    async def create_vm(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new VM (async, returns 202)."""
        return await self._request_dict("POST", "/lume/vms", json=config)

    async def delete_vm(self, name: str, storage: str | None = None) -> dict[str, Any]:
        """Delete a VM and its files."""
        params = {"storage": storage} if storage else None
        url = f"/lume/vms/{name}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        return await self._request_dict("DELETE", f"/lume/vms/{name}", params=params)

    async def clone_vm(
        self,
        source: str,
        target: str,
        *,
        source_location: str | None = None,
        dest_location: str | None = None,
    ) -> dict[str, Any]:
        """Clone a VM."""
        payload: dict[str, Any] = {"name": source, "newName": target}
        if source_location:
            payload["sourceLocation"] = source_location
        if dest_location:
            payload["destLocation"] = dest_location
        return await self._request_dict("POST", "/lume/vms/clone", json=payload)

    async def start_vm(
        self,
        name: str,
        *,
        no_display: bool = False,
        storage: str | None = None,
    ) -> dict[str, Any]:
        """Start a VM."""
        payload: dict[str, Any] = {"noDisplay": no_display}
        if storage:
            payload["storage"] = storage
        return await self._request_dict("POST", f"/lume/vms/{name}/run", json=payload)

    async def stop_vm(self, name: str, storage: str | None = None) -> dict[str, Any]:
        """Stop a running VM."""
        payload: dict[str, Any] = {}
        if storage:
            payload["storage"] = storage
        return await self._request_dict("POST", f"/lume/vms/{name}/stop", json=payload)

    async def pull_image(self, image: str, name: str | None = None) -> dict[str, Any]:
        """Pull a VM image from registry."""
        payload: dict[str, Any] = {"image": image}
        if name:
            payload["name"] = name
        return await self._request_dict("POST", "/lume/pull", json=payload)


# --- VMManager ---


class VMManager:
    """Manages the active VM lifecycle via Lume HTTP API."""

    def __init__(self, vm_config: VMConfig):
        self._config = vm_config
        self._client = LumeClient(base_url=vm_config.lume_url)
        self._vm_name = vm_config.name
        self._computer: Any = None
        self._cycle_count = 0
        self._contamination_detected = False

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

    # --- Browser reset (osascript window management) ---

    OSASCRIPT_CLOSE_WINDOWS = """\
tell application "Safari"
    close every window
end tell
"""

    OSASCRIPT_OPEN_FRESH_WINDOW = """\
tell application "Safari"
    make new document
    activate
end tell
"""

    OSASCRIPT_CREATE_OR_SWITCH_SPACE = """\
tell application "System Events"
    key code 126 using control down
    delay 0.7
    try
        tell process "Dock"
            click (first button whose description is "add desktop")
        end tell
    on error
        key code 124 using control down
    end try
    delay 0.4
    key code 53
end tell
"""

    OSASCRIPT_LIST_FOREGROUND_APPS = """\
tell application "System Events"
    set appNames to name of every process whose background only is false
    return appNames as string
end tell
"""

    OSASCRIPT_FRONTMOST_APP = """\
tell application "System Events"
    return name of first application process whose frontmost is true
end tell
"""

    PROTECTED_APPS = {
        "finder",
        "dock",
        "systemuiserver",
        "controlcenter",
        "loginwindow",
        "notificationcenter",
        "windowserver",
    }

    async def _run_osascript(self, script: str) -> str:
        """Execute osascript inside the VM. Raises on failure."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        escaped = script.replace("'", "'\\''")
        result = await self._computer.interface.run_command(f"osascript -e '{escaped}'")
        return (result.stdout or "").strip()

    async def _run_pkill_fallback(self) -> None:
        """Fallback: pkill Safari and reopen."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        await self._computer.interface.run_command("pkill -f Safari 2>/dev/null; sleep 1; open -a Safari")
        await asyncio.sleep(2)

    def _normalize_allowed_apps(self, required_apps: list[str] | None = None) -> set[str]:
        """Return lowercase allowlist merged with required apps and built-in defaults."""
        configured = {a.strip().lower() for a in self._config.allowed_apps if a and a.strip()}
        required = {a.strip().lower() for a in (required_apps or []) if a and a.strip()}
        # Safari is required for Soren's core browser workflows.
        return configured | required | {"safari"} | self.PROTECTED_APPS

    def _parse_app_list(self, raw: str) -> list[str]:
        """Parse osascript app-name output into a normalized list."""
        if not raw:
            return []
        return [chunk.strip() for chunk in raw.split(",") if chunk.strip()]

    async def _list_foreground_apps(self) -> list[str]:
        raw = await self._run_osascript(self.OSASCRIPT_LIST_FOREGROUND_APPS)
        return self._parse_app_list(raw)

    async def _close_disallowed_apps(self, allowed_lower: set[str]) -> list[str]:
        """Quit non-whitelisted GUI apps. Returns list of app names attempted."""
        closed: list[str] = []
        running = await self._list_foreground_apps()
        for app in running:
            app_lower = app.lower()
            if app_lower in allowed_lower:
                continue
            try:
                safe_app = app.replace('"', "")
                await self._run_osascript(f'tell application "{safe_app}" to quit')
                closed.append(app)
            except Exception as exc:
                logger.warning("Failed to quit app '%s' during session prep: %s", app, exc)
        if closed and self._config.app_quit_grace_period_s > 0:
            await asyncio.sleep(self._config.app_quit_grace_period_s)
        return closed

    async def _ensure_required_apps_open(self, required_apps: list[str] | None = None) -> None:
        apps_to_open: set[str] = {*(required_apps or []), "Safari"}
        for app in sorted(apps_to_open):
            safe = app.replace('"', "").strip()
            if not safe:
                continue
            await self._computer.interface.run_command(f'open -a "{safe}"')

    async def _attempt_new_space(self, warnings: list[str]) -> bool:
        if not self._config.create_new_space_per_session:
            return False
        retries = max(1, self._config.space_switch_retries)
        for attempt in range(1, retries + 1):
            try:
                await self._run_osascript(self.OSASCRIPT_CREATE_OR_SWITCH_SPACE)
                return True
            except Exception as exc:
                warnings.append(f"space attempt {attempt}/{retries} failed: {exc}")
                await asyncio.sleep(0.5)
        return True  # attempted, but not successful

    async def prepare_session_environment(self, required_apps: list[str] | None = None) -> SessionPrepResult:
        """Best-effort desktop isolation for a fresh automation session."""
        start = time.monotonic()
        warnings: list[str] = []
        closed_apps: list[str] = []
        remaining_disallowed: list[str] = []
        frontmost = ""

        await self.ensure_ready()
        if not self._computer:
            await self.connect_computer()

        attempted_new_space = False
        if self._config.session_isolation_enabled:
            attempted_new_space = await self._attempt_new_space(warnings)

            allowed_lower = self._normalize_allowed_apps(required_apps)
            try:
                closed_apps = await self._close_disallowed_apps(allowed_lower)
            except Exception as exc:
                warnings.append(f"app whitelist enforcement failed: {exc}")

            try:
                await self._ensure_required_apps_open(required_apps)
            except Exception as exc:
                warnings.append(f"failed opening required apps: {exc}")

            try:
                running_after = await self._list_foreground_apps()
                remaining_disallowed = [app for app in running_after if app.lower() not in allowed_lower]
            except Exception as exc:
                warnings.append(f"verification list apps failed: {exc}")

            try:
                frontmost = await self._run_osascript(self.OSASCRIPT_FRONTMOST_APP)
                if frontmost and frontmost.lower() in {a.lower() for a in remaining_disallowed}:
                    warnings.append(f"frontmost app is disallowed: {frontmost}")
            except Exception as exc:
                warnings.append(f"frontmost app check failed: {exc}")

        duration = time.monotonic() - start
        success = not remaining_disallowed
        return SessionPrepResult(
            success=success,
            duration_seconds=duration,
            attempted_new_space=attempted_new_space,
            frontmost_app=frontmost,
            closed_apps=closed_apps,
            remaining_disallowed_apps=remaining_disallowed,
            warnings=warnings,
        )

    async def reset_browser_state(self) -> ResetResult:
        """Close all Safari windows and open a fresh one. Sessions preserved."""
        start = time.monotonic()
        warnings: list[str] = []
        window_count = -1

        await self.ensure_ready()
        if not self._computer:
            await self.connect_computer()

        try:
            # Phase 1: Close all Safari windows
            try:
                await self._run_osascript(self.OSASCRIPT_CLOSE_WINDOWS)
            except Exception as e:
                warnings.append(f"osascript close failed: {e}, using pkill fallback")
                await self._run_pkill_fallback()
            await asyncio.sleep(1)

            # Phase 2: Open a single fresh window
            try:
                await self._run_osascript(self.OSASCRIPT_OPEN_FRESH_WINDOW)
            except Exception as e:
                warnings.append(f"osascript open failed: {e}, using pkill fallback")
                await self._run_pkill_fallback()
            await asyncio.sleep(1)

            # Phase 3: Verify window count
            issues = await verify_reset(self._computer)
            if issues:
                warnings.extend(issues)
                # Retry close/open once
                try:
                    await self._run_osascript(self.OSASCRIPT_CLOSE_WINDOWS)
                    await asyncio.sleep(5)
                    await self._run_osascript(self.OSASCRIPT_OPEN_FRESH_WINDOW)
                    await asyncio.sleep(1)
                    issues = await verify_reset(self._computer)
                    if issues:
                        warnings.append(f"Retry still had issues: {issues}")
                except Exception as e:
                    warnings.append(f"Retry failed: {e}")

            # Get final window count
            try:
                count_raw = await self._run_osascript('tell application "Safari" to count of windows')
                window_count = int(count_raw) if count_raw else -1
            except (ValueError, TypeError):
                window_count = -1

        except Exception as e:
            warnings.append(str(e))

        duration = time.monotonic() - start
        success = window_count == 1
        return ResetResult(
            success=success,
            duration_seconds=duration,
            window_count=window_count,
            warnings=warnings,
        )

    # --- Tab introspection (Task 018) ---

    async def get_tab_count(self) -> int:
        """Count open Safari tabs in window 1. Returns 0 on error."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        try:
            raw = await self._run_osascript('tell application "Safari" to count of tabs of window 1')
            return int(raw) if raw else 0
        except Exception:
            return 0

    async def get_active_tab_url(self) -> str:
        """URL of the currently active Safari tab. Returns empty string on error."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        try:
            return await self._run_osascript('tell application "Safari" to URL of current tab of window 1')
        except Exception:
            return ""

    async def switch_to_tab(self, index: int) -> bool:
        """Switch to Safari tab by 1-based index. Returns True on success."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        try:
            await self._run_osascript(
                f'tell application "Safari" to set current tab of window 1 to tab {index} of window 1'
            )
            return True
        except Exception:
            return False

    async def find_tab_by_domain(self, domain: str) -> int | None:
        """Find Safari tab whose URL contains the given domain. Returns 1-based index or None."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        # Sanitize domain to prevent AppleScript injection via double-quotes
        safe_domain = domain.replace('"', "").replace("\\", "")
        script = (
            f'tell application "Safari"\n'
            f"  set tabCount to count of tabs of window 1\n"
            f"  repeat with i from 1 to tabCount\n"
            f"    set tabURL to URL of tab i of window 1\n"
            f'    if tabURL contains "{safe_domain}" then\n'
            f"      return i\n"
            f"    end if\n"
            f"  end repeat\n"
            f"  return 0\n"
            f"end tell"
        )
        try:
            raw = await self._run_osascript(script)
            idx = int(raw) if raw else 0
            return idx if idx > 0 else None
        except Exception:
            return None

    async def wait_for_new_tab(self, initial_count: int, timeout_s: float = 5.0) -> bool:
        """Poll get_tab_count() until it exceeds initial_count or timeout. 0.5s interval."""
        if not self._computer:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            current = await self.get_tab_count()
            if current > initial_count:
                return True
            await asyncio.sleep(0.5)
        return False

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
        # Stop active VM
        await self._client.stop_vm(self._vm_name)
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
