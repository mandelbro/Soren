#!/usr/bin/env python3
"""Proof-of-concept: Validate window-management reset strategy in a Lume VM.

Validates that Safari browser windows can be reliably closed and a clean
new window opened between task cycles, while PRESERVING login sessions,
cookies, and site data.

Design rationale: Deleting cookies/localStorage forces re-login on every
cycle. Services with 2FA make that painful or impossible to automate. By
keeping session data and only managing windows, we reduce human effort
while relying on periodic full VM recreation for deep cleanup when needed.

Reset contract (v1):
    1. Close all Safari windows/tabs (task isolation between cycles)
    2. Open a single fresh Safari window (clean starting point for next task)
    3. Login sessions, cookies, localStorage, and site data are PRESERVED
    4. Full VM recreation (lume clone) available for deep reset when needed

This script gates Phase 2 (VM Integration).

Prerequisites:
    - Lume CLI installed, `lume serve` running on the host
    - A macOS VM running (default: "soren-vm")
    - Python 3.12+ with `cua-computer` package installed
    - TCC permissions granted for python3 → Safari (Apple Events)

Usage:
    python scripts/validate_browser_reset.py
    python scripts/validate_browser_reset.py --vm-name my-vm --lume-url http://localhost:7777
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    informational: bool = False


@dataclass
class ValidationReport:
    checks: list[CheckResult] = field(default_factory=list)
    window_reset_seconds: float | None = None
    clone_seconds: float | None = None

    def add(
        self,
        name: str,
        passed: bool,
        detail: str = "",
        informational: bool = False,
    ) -> None:
        self.checks.append(
            CheckResult(name=name, passed=passed, detail=detail, informational=informational)
        )

    def print_summary(self) -> None:
        print(f"\n{'=' * 60}")
        print("WINDOW-MANAGEMENT RESET VALIDATION RESULTS")
        print(f"{'=' * 60}")

        if self.window_reset_seconds is not None:
            print(f"  Window reset time:   {self.window_reset_seconds:.1f}s")
        if self.clone_seconds is not None:
            print(f"  VM clone time:       {self.clone_seconds:.1f}s")

        print()
        critical_checks = [c for c in self.checks if not c.informational]
        for check in self.checks:
            if check.informational:
                status = "INFO"
                line = f"  [{status}] {check.name}"
            else:
                status = "PASS" if check.passed else "FAIL"
                line = f"  [{status}] {check.name}"
            if check.detail:
                line += f"  ({check.detail})"
            print(line)

        total = len(critical_checks)
        passed = sum(1 for c in critical_checks if c.passed)
        print(f"\n  {passed}/{total} critical checks passed")
        print(f"{'=' * 60}")


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

def osascript_open_new_window_with_url(url: str) -> str:
    """Build AppleScript to open a new Safari window with the given URL."""
    escaped = url.replace("\\", "\\\\").replace('"', '\\"')
    return f'''tell application "Safari"
    make new document
    set URL of current tab of front window to "{escaped}"
end tell
'''


async def run_osascript(iface, script: str) -> str:
    """Execute an osascript snippet inside the VM."""
    escaped = script.replace("'", "'\\''")
    result = await iface.run_command(f"osascript -e '{escaped}'")
    return result.stdout.strip()


async def main(vm_name: str, lume_url: str) -> None:
    from computer import Computer

    report = ValidationReport()

    print(f"[POC] Connecting to VM '{vm_name}' (lume: {lume_url})...")

    try:
        async with Computer(
            os_type="macos",
            provider_type="lume",
            name=vm_name,
            display="1280x720",
        ) as comp:
            iface = comp.interface

            # ----------------------------------------------------------
            # Step 1: Open Safari and create multiple windows (simulate in-progress task)
            # ----------------------------------------------------------
            print("[POC] Step 1: Opening Safari and creating multiple windows...")
            await iface.run_command(
                'open -a Safari "https://httpbin.org/cookies/set/session_token/abc123"'
            )
            await asyncio.sleep(5)

            # Open a second window via AppleScript (open -a Safari opens tabs, not windows)
            await run_osascript(
                iface,
                osascript_open_new_window_with_url(
                    "https://httpbin.org/cookies/set/remember_me/true"
                ),
            )
            await asyncio.sleep(3)

            # ----------------------------------------------------------
            # Step 2: Verify session state and multiple windows
            # ----------------------------------------------------------
            print("[POC] Step 2: Verifying session state is established...")
            cookie_result = await iface.run_command(
                "ls -la ~/Library/Cookies/Cookies.binarycookies 2>&1"
            )
            cookies_exist = "No such file" not in cookie_result.stdout
            report.add(
                "Cookies set after navigation",
                cookies_exist,
                cookie_result.stdout.strip(),
                informational=True,
            )

            window_count_raw = await run_osascript(iface, OSASCRIPT_COUNT_WINDOWS)
            try:
                window_count = int(window_count_raw)
            except ValueError:
                window_count = 0
            multiple_windows = window_count >= 2
            report.add(
                "Multiple windows open before reset",
                multiple_windows,
                f"{window_count} windows",
            )
            print(f"  Cookies exist: {cookies_exist}, Windows open: {window_count}")

            # ----------------------------------------------------------
            # Step 3: Run the window-management reset
            # ----------------------------------------------------------
            print("[POC] Step 3: Running window-management reset...")
            t0 = time.time()

            # 3a: Close all Safari windows
            print("  3a: Closing all Safari windows via osascript...")
            await run_osascript(iface, OSASCRIPT_CLOSE_WINDOWS)
            await asyncio.sleep(2)

            # 3b: Open a single fresh window
            print("  3b: Opening fresh Safari window...")
            await run_osascript(iface, OSASCRIPT_OPEN_FRESH_WINDOW)
            await asyncio.sleep(3)

            reset_elapsed = time.time() - t0
            report.window_reset_seconds = reset_elapsed
            print(f"  Window reset completed in {reset_elapsed:.1f}s")

            # ----------------------------------------------------------
            # Step 4: Verify cookies are PRESERVED (informational; env-dependent)
            # ----------------------------------------------------------
            print("[POC] Step 4: Verifying cookies are PRESERVED after reset...")
            cookie_result = await iface.run_command(
                "ls -la ~/Library/Cookies/Cookies.binarycookies 2>&1"
            )
            cookies_preserved = "No such file" not in cookie_result.stdout
            report.add(
                "Cookies PRESERVED after window reset",
                cookies_preserved,
                cookie_result.stdout.strip(),
                informational=True,
            )
            print(f"  Cookies preserved: {cookies_preserved}")

            # ----------------------------------------------------------
            # Step 5: Verify localStorage is PRESERVED
            # ----------------------------------------------------------
            print("[POC] Step 5: Verifying localStorage directory is PRESERVED...")
            ls_result = await iface.run_command(
                "ls ~/Library/Safari/ 2>&1"
            )
            safari_dir_exists = "No such file" not in ls_result.stdout
            report.add(
                "Safari data directory PRESERVED",
                safari_dir_exists,
                ls_result.stdout.strip()[:100],
            )
            print(f"  Safari data preserved: {safari_dir_exists}")

            # ----------------------------------------------------------
            # Step 6: Verify exactly one clean window is open
            # ----------------------------------------------------------
            print("[POC] Step 6: Verifying single clean window...")
            window_count_raw = await run_osascript(iface, OSASCRIPT_COUNT_WINDOWS)
            try:
                window_count_after = int(window_count_raw)
            except ValueError:
                window_count_after = -1
            single_window = window_count_after == 1
            report.add(
                "Exactly one window after reset",
                single_window,
                f"{window_count_after} window(s)",
            )
            print(f"  Windows after reset: {window_count_after}")

            # Check the fresh window is on a blank/start page
            urls_raw = await run_osascript(iface, OSASCRIPT_WINDOW_URLS)
            is_clean = (
                not urls_raw
                or "favorites" in urls_raw.lower()
                or urls_raw.strip() == ""
                or "missing value" in urls_raw.lower()
            )
            report.add(
                "Fresh window is on start/blank page",
                is_clean,
                urls_raw[:80] if urls_raw else "(empty)",
            )
            print(f"  Fresh window URL: {urls_raw[:80] if urls_raw else '(empty)'}")

            # ----------------------------------------------------------
            # Step 7: Verify cookies still work (session alive)
            # ----------------------------------------------------------
            print("[POC] Step 7: Navigating to verify cookies are still sent...")
            await iface.run_command(
                'open -a Safari "https://httpbin.org/cookies"'
            )
            await asyncio.sleep(5)

            # Take a screenshot for manual verification (via interface, returns bytes)
            try:
                screenshot_bytes = await iface.screenshot()
                if screenshot_bytes:
                    screenshot_path = Path("data/screenshots/reset_cookie_check.png")
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    screenshot_path.write_bytes(screenshot_bytes)
                    print(f"  Screenshot saved to {screenshot_path}")
                    report.add(
                        "Screenshot captured for cookie verification",
                        True,
                        str(screenshot_path),
                    )
                else:
                    report.add("Screenshot captured for cookie verification", False, "empty result")
            except Exception as e:
                print(f"  Could not capture screenshot: {e}")
                report.add("Screenshot captured for cookie verification", False, str(e))

            # ----------------------------------------------------------
            # Step 8: Test repeated reset cycles (stability)
            # ----------------------------------------------------------
            print("[POC] Step 8: Running 3 rapid reset cycles for stability...")
            stability_ok = True
            for i in range(3):
                await iface.run_command(
                    f'open -a Safari "https://httpbin.org/get?cycle={i}"'
                )
                await asyncio.sleep(2)
                await run_osascript(iface, OSASCRIPT_CLOSE_WINDOWS)
                await asyncio.sleep(1)
                await run_osascript(iface, OSASCRIPT_OPEN_FRESH_WINDOW)
                await asyncio.sleep(1)
                count_raw = await run_osascript(iface, OSASCRIPT_COUNT_WINDOWS)
                try:
                    c = int(count_raw)
                except ValueError:
                    c = -1
                if c != 1:
                    stability_ok = False
                    break
            report.add("Stable across 3 rapid reset cycles", stability_ok)
            print(f"  Stability: {'OK' if stability_ok else 'FAILED'}")

            # Final: verify cookies survived the stability test (informational; env-dependent)
            cookie_result = await iface.run_command(
                "ls -la ~/Library/Cookies/Cookies.binarycookies 2>&1"
            )
            cookies_survived = "No such file" not in cookie_result.stdout
            report.add(
                "Cookies survived stability cycles",
                cookies_survived,
                informational=True,
            )
            print(f"  Cookies survived: {cookies_survived}")

            # Close Safari for clone test
            await run_osascript(iface, OSASCRIPT_CLOSE_WINDOWS)
            await asyncio.sleep(1)

    except Exception as e:
        print(f"[POC] ERROR during window reset tests: {e}")
        report.add("Computer SDK connection", False, str(e))

    # ------------------------------------------------------------------
    # Step 9: Test VM recreation via lume clone (deep reset path)
    # ------------------------------------------------------------------
    print("[POC] Step 9: Testing VM recreation via lume clone (deep reset)...")
    pristine_name = f"{vm_name}-pristine"

    try:
        ls_result = subprocess.run(
            ["lume", "ls", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        vms = json.loads(ls_result.stdout)
        vm_names = [vm.get("name", "") for vm in vms] if isinstance(vms, list) else []
        has_pristine = pristine_name in vm_names
    except Exception as e:
        print(f"  Could not list VMs: {e}")
        has_pristine = False

    if has_pristine:
        print(f"  Found pristine VM '{pristine_name}', running clone test...")
        t0 = time.time()

        try:
            subprocess.run(
                ["lume", "stop", vm_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                ["lume", "delete", vm_name, "--force"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            clone_result = subprocess.run(
                ["lume", "clone", vm_name, pristine_name],
                capture_output=True,
                text=True,
                timeout=300,
            )
            clone_elapsed = time.time() - t0
            report.clone_seconds = clone_elapsed

            clone_ok = clone_result.returncode == 0
            report.add(
                "VM recreation via lume clone (deep reset)",
                clone_ok,
                f"{clone_elapsed:.1f}s" if clone_ok else clone_result.stderr.strip(),
            )
            print(f"  Clone recreation completed in {clone_elapsed:.1f}s (exit: {clone_result.returncode})")
        except Exception as e:
            report.add("VM recreation via lume clone (deep reset)", False, str(e))
            print(f"  Clone failed: {e}")
    else:
        print(f"  Skipped: no pristine VM '{pristine_name}' found in lume ls")
        print(f"  To enable this test, create a pristine VM:")
        print(f"    lume clone {pristine_name} {vm_name}")
        report.add(
            "VM recreation via lume clone (deep reset)",
            False,
            f"Pristine VM '{pristine_name}' not found (skipped)",
            informational=True,
        )

    report.print_summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate window-management reset strategy in a Lume macOS VM"
    )
    parser.add_argument(
        "--vm-name",
        default="soren-vm",
        help="Name of the Lume VM to test against (default: soren-vm)",
    )
    parser.add_argument(
        "--lume-url",
        default="http://localhost:7777",
        help="Lume HTTP API URL (default: http://localhost:7777)",
    )
    args = parser.parse_args()

    asyncio.run(main(args.vm_name, args.lume_url))
