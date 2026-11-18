"""SessionPreparer — per-session desktop isolation inside the VM (apps allowlist, Spaces, required apps).

Part of the ``VMManager`` facade: reached through ``VMManager.prepare_session_environment()``
and ``open_required_apps()``, and runs commands through the manager's ``GuestShell``.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time

from soren.models.reset import SessionPrepResult
from soren.models.vm import VMConfig
from soren.services.vm_guest import GuestShell

logger = logging.getLogger(__name__)


class SessionPreparer:
    """Best-effort desktop isolation for automation sessions."""

    def __init__(self, shell: GuestShell, config: VMConfig) -> None:
        self._shell = shell
        self._config = config

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
        raw = await self._shell.run_osascript(self.OSASCRIPT_LIST_FOREGROUND_APPS)
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
                await self._shell.run_osascript(f'tell application "{safe_app}" to quit')
                closed.append(app)
            except Exception as exc:
                logger.warning("Failed to quit app '%s' during session prep: %s", app, exc)
        if closed and self._config.app_quit_grace_period_s > 0:
            await asyncio.sleep(self._config.app_quit_grace_period_s)
        return closed

    async def _ensure_required_apps_open(self, required_apps: list[str] | None = None) -> None:
        apps_to_open: set[str] = {*(required_apps or []), "Safari"}
        for app in sorted(apps_to_open):
            await self._open_app(app)

    async def open_required_apps(self, apps: list[str]) -> None:
        """Open ``apps`` in the VM (``open -a``) without session-prep side effects.

        Used after task selection for a service's ``required_apps``: unlike
        ``prepare_session_environment`` it does not create a Space or quit apps.
        """
        if self._shell.computer is None:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        for app in apps:
            await self._open_app(app)

    async def _open_app(self, app: str) -> None:
        """Run ``open -a`` with the app name quoted as one literal shell argument (names come from config)."""
        name = app.strip()
        if name:
            await self._shell.run_command(f"open -a {shlex.quote(name)}")

    async def _attempt_new_space(self, warnings: list[str]) -> bool:
        if not self._config.create_new_space_per_session:
            return False
        retries = max(1, self._config.space_switch_retries)
        for attempt in range(1, retries + 1):
            try:
                await self._shell.run_osascript(self.OSASCRIPT_CREATE_OR_SWITCH_SPACE)
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

        await self._shell.ensure_connected()

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
                frontmost = await self._shell.run_osascript(self.OSASCRIPT_FRONTMOST_APP)
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
