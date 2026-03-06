"""Post-reset verification for browser state."""

from __future__ import annotations

from typing import Any

# AppleScript snippets for Safari window verification
OSASCRIPT_COUNT_WINDOWS = """\
tell application "Safari"
    count of windows
end tell
"""

OSASCRIPT_WINDOW_URLS = """\
tell application "Safari"
    set urlList to {}
    repeat with w in windows
        repeat with t in tabs of w
            set end of urlList to URL of t
        end repeat
    end repeat
    return urlList
end tell
"""


async def _run_osascript(iface: Any, script: str) -> str:
    """Execute an osascript snippet inside the VM via Computer interface."""
    escaped = script.replace("'", "'\\''")
    result = await iface.run_command(f"osascript -e '{escaped}'")
    return (result.stdout or "").strip()


async def verify_reset(computer: Any) -> list[str]:
    """Verify browser reset state. Returns list of issues found (empty if OK).

    Tier 1: Window count check (~0.5s) — exactly one Safari window open.
    Tier 2: URL check (~0.5s) — fresh window on start/blank page.
    """
    issues: list[str] = []
    iface = computer.interface

    # Tier 1: Window count
    try:
        count_raw = await _run_osascript(iface, OSASCRIPT_COUNT_WINDOWS)
        count = int(count_raw) if count_raw else -1
    except (ValueError, TypeError):
        count = -1

    if count != 1:
        issues.append(f"Expected 1 Safari window, got {count}")

    # Tier 2: URL check (only if window count passed)
    if not issues:
        try:
            urls_raw = await _run_osascript(iface, OSASCRIPT_WINDOW_URLS)
            # Fresh/blank: empty, favorites, or missing value
            is_clean = (
                not urls_raw or "favorites" in (urls_raw or "").lower() or "missing value" in (urls_raw or "").lower()
            )
            if not is_clean:
                issues.append(f"Window not on start/blank page: {urls_raw[:80]}")
        except Exception as e:
            issues.append(f"URL check failed: {e}")

    return issues
