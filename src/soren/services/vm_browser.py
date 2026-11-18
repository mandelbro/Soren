"""SafariBrowser — Safari window reset and tab introspection inside the VM (osascript).

Part of the ``VMManager`` facade: reached through ``VMManager.reset_browser_state()``
and the tab methods, and runs commands through the manager's ``GuestShell``.
"""

from __future__ import annotations

import asyncio
import time

from soren.models.reset import ResetResult
from soren.services.reset_verification import verify_reset
from soren.services.vm_guest import GuestShell


class SafariBrowser:
    """Safari window/tab operations for the connected VM."""

    def __init__(self, shell: GuestShell) -> None:
        self._shell = shell

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

    async def _run_pkill_fallback(self) -> None:
        """Fallback: pkill Safari and reopen."""
        if self._shell.computer is None:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        await self._shell.run_command("pkill -f Safari 2>/dev/null; sleep 1; open -a Safari")
        await asyncio.sleep(2)

    async def reset_browser_state(self) -> ResetResult:
        """Close all Safari windows and open a fresh one. Sessions preserved."""
        start = time.monotonic()
        warnings: list[str] = []
        window_count = -1

        await self._shell.ensure_connected()

        try:
            # Phase 1: Close all Safari windows
            try:
                await self._shell.run_osascript(self.OSASCRIPT_CLOSE_WINDOWS)
            except Exception as e:
                warnings.append(f"osascript close failed: {e}, using pkill fallback")
                await self._run_pkill_fallback()
            await asyncio.sleep(1)

            # Phase 2: Open a single fresh window
            try:
                await self._shell.run_osascript(self.OSASCRIPT_OPEN_FRESH_WINDOW)
            except Exception as e:
                warnings.append(f"osascript open failed: {e}, using pkill fallback")
                await self._run_pkill_fallback()
            await asyncio.sleep(1)

            # Phase 3: Verify window count
            issues = await verify_reset(self._shell.computer)
            if issues:
                warnings.extend(issues)
                # Retry close/open once
                try:
                    await self._shell.run_osascript(self.OSASCRIPT_CLOSE_WINDOWS)
                    await asyncio.sleep(5)
                    await self._shell.run_osascript(self.OSASCRIPT_OPEN_FRESH_WINDOW)
                    await asyncio.sleep(1)
                    issues = await verify_reset(self._shell.computer)
                    if issues:
                        warnings.append(f"Retry still had issues: {issues}")
                except Exception as e:
                    warnings.append(f"Retry failed: {e}")

            # Get final window count
            try:
                count_raw = await self._shell.run_osascript('tell application "Safari" to count of windows')
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

    async def get_tab_count(self) -> int:
        """Count open Safari tabs in window 1. Returns 0 on error."""
        if self._shell.computer is None:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        try:
            raw = await self._shell.run_osascript('tell application "Safari" to count of tabs of window 1')
            return int(raw) if raw else 0
        except Exception:
            return 0

    async def get_active_tab_url(self) -> str:
        """URL of the currently active Safari tab. Returns empty string on error."""
        if self._shell.computer is None:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        try:
            return await self._shell.run_osascript('tell application "Safari" to URL of current tab of window 1')
        except Exception:
            return ""

    async def switch_to_tab(self, index: int) -> bool:
        """Switch to Safari tab by 1-based index. Returns True on success."""
        if self._shell.computer is None:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        try:
            await self._shell.run_osascript(
                f'tell application "Safari" to set current tab of window 1 to tab {index} of window 1'
            )
            return True
        except Exception:
            return False

    async def find_tab_by_domain(self, domain: str) -> int | None:
        """Find Safari tab whose URL contains the given domain. Returns 1-based index or None."""
        if self._shell.computer is None:
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
            raw = await self._shell.run_osascript(script)
            idx = int(raw) if raw else 0
            return idx if idx > 0 else None
        except Exception:
            return None

    async def wait_for_new_tab(self, initial_count: int, timeout_s: float = 5.0) -> bool:
        """Poll get_tab_count() until it exceeds initial_count or timeout. 0.5s interval."""
        if self._shell.computer is None:
            raise RuntimeError("Computer not connected. Call connect_computer() first.")
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            current = await self.get_tab_count()
            if current > initial_count:
                return True
            await asyncio.sleep(0.5)
        return False
