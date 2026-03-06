# Discovery: Lume VM Window Management & Reset Strategy Validation

**Date**: February 23, 2026
**Updated**: February 22, 2026 — Strategy revised from data deletion to window management
**Status**: Complete (strategy revised)
**RFC Reference**: `docs/rfc.md` — VMManager interface, Environment Reset Invariants
**Phase Gate**: Phase 2 (VM Integration)

## The One-Liner

Validate that Safari windows can be reliably managed via osascript while preserving login sessions between cycles.

## The Problem

The RFC specifies a two-tier reset strategy — window-management reset (`reset_browser_state()`) after every cycle, periodic full VM recreation (`recreate_vm()`) every N cycles. The window-management approach closes all browser windows and opens a fresh one, while **intentionally preserving** cookies, localStorage, and login sessions. This avoids re-login overhead (critical for 2FA-protected services) and reduces human intervention. The strategy needs validation before Phase 2 begins.

## The Solution

A window-management approach using osascript to close all Safari windows and open a fresh one between cycles, combined with `lume clone` for periodic deep VM recreation when a truly clean slate is needed.

---

## 1. Current State: Lume CLI

### Version and Capabilities

The current Lume CLI is **v0.2.76** (per the official CLI reference at cua.ai). The desktop-agent framework was built against v0.2.80 — both are in the 0.2.x line, and the API surface is compatible.

**Snapshot support: Still absent.** There is no `lume snapshot`, `lume restore`, or equivalent command in the CLI reference or HTTP API. This constraint from the desktop-agent docs remains unchanged. The RFC's decision to use browser-level cleanup with periodic VM recreation was correct given this limitation.

### Available VM Lifecycle Commands

| Command | Purpose | Notes |
|---------|---------|-------|
| `lume create <name>` | Create a new VM | Supports `--os`, `--cpu`, `--memory`, `--disk-size`, `--display`, `--ipsw`, `--unattended` |
| `lume clone <new> <source>` | Full copy of a VM | Supports cross-storage cloning. **This is our recreation primitive.** |
| `lume run <name>` | Start a VM | `--no-display` for headless, `--vnc-port` for VNC, `--shared-dir` |
| `lume stop <name>` | Graceful shutdown | `--force` now available (was missing in v0.2.80) |
| `lume delete <name>` | Delete VM and files | `--force` to skip confirmation |
| `lume ls` | List all VMs | `-f json` returns structured data including `vncUrl` |
| `lume get <name>` | Detailed VM info | State, CPU, memory, disk, network, display |
| `lume set <name>` | Update VM config | CPU, memory, disk-size, display (VM must be stopped) |
| `lume pull <image>` | Pull prebuilt image | From ghcr.io registry (e.g., `macos-tahoe-vanilla:latest`) |
| `lume serve` | Start HTTP API server | Default port 7777, full REST API for all operations |
| `lume setup <name>` | Unattended Setup Assistant | Presets: `sequoia`, `tahoe` |

### Prebuilt Images Available

- `macos-sequoia-vanilla:latest` (macOS 15.2)
- `macos-sequoia-cua:latest` (macOS 15.3, CUA Computer interface compatible)
- `macos-tahoe-vanilla:latest`

The `macos-sequoia-cua` image is interesting — it's pre-configured for CUA Computer SDK usage, which could simplify our VM preparation step.

### HTTP API

The `lume serve` command exposes a full REST API on port 7777:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/lume/vms` | GET | List VMs |
| `/lume/vms/:name` | GET | VM details |
| `/lume/vms` | POST | Create VM |
| `/lume/vms/:name` | DELETE | Delete VM |
| `/lume/vms/clone` | POST | Clone VM |
| `/lume/vms/:name` | PATCH | Update VM settings |
| `/lume/vms/:name/run` | POST | Start VM |
| `/lume/vms/:name/stop` | POST | Stop VM |
| `/lume/pull` | POST | Pull image |

The HTTP API is a better fit than CLI subprocess calls for Soren's `VMManager`, since we're already async Python and can use `httpx`/`aiohttp` directly. The RFC's `VMConfig.lume_url` defaults to `http://localhost:3000` but should be updated to `http://localhost:7777` (Lume's actual default).

### VNC Access

VNC is built into Lume. When a VM is running, `lume ls --format json` returns a `vncUrl` field in the format:

```
vnc://:password@host:port
```

The existing `vnc_screenshot.py` from the desktop-agent parses this format and extracts `(host, port, password)`. This works and needs no changes.

---

## 2. Window Management Strategy

### Design Philosophy: Preserve Sessions, Manage Windows

The original approach (delete all cookies/localStorage/cache) forces re-login on every cycle. For services with 2FA (TOTP, SMS, security keys), this creates a hard dependency on human intervention for every single cycle — making autonomous operation impractical. The revised strategy:

1. **Keep** — Cookies, localStorage, login sessions, site data
2. **Clean** — Close all browser windows/tabs at cycle end
3. **Fresh** — Open a single new window for the next task
4. **Deep reset** — Periodic VM recreation via `lume clone` when a truly clean slate is needed

### Window Management via osascript

```applescript
-- Close all Safari windows (preserving cookies/sessions)
tell application "Safari"
    close every window
end tell

-- Open a single fresh window for the next task
tell application "Safari"
    make new document
    activate
end tell

-- Utility: count current windows
tell application "Safari"
    count of windows
end tell
```

These Apple Events require TCC permission for `python3 → Safari` (kTCCServiceAppleEvents). See Section 3.

### Complete `reset_browser_state()` Implementation

```python
CLOSE_WINDOWS_SCRIPT = """\
tell application "Safari"
    close every window
end tell
"""

OPEN_FRESH_WINDOW_SCRIPT = """\
tell application "Safari"
    make new document
    activate
end tell
"""

COUNT_WINDOWS_SCRIPT = """\
tell application "Safari"
    count of windows
end tell
"""

async def reset_browser_state(self) -> None:
    """Close all Safari windows and open a fresh one. Sessions preserved."""
    iface = self._computer.interface

    # Phase 1: Close all Safari windows
    await self._run_osascript(iface, CLOSE_WINDOWS_SCRIPT)
    await asyncio.sleep(2)

    # Phase 2: Open a single fresh window
    await self._run_osascript(iface, OPEN_FRESH_WINDOW_SCRIPT)
    await asyncio.sleep(1)

    # Phase 3: Verify exactly one window open
    count_raw = await self._run_osascript(iface, COUNT_WINDOWS_SCRIPT)
    try:
        count = int(count_raw.strip())
    except ValueError:
        count = -1

    if count != 1:
        logger.warning("Expected 1 window after reset, got %s", count)
```

### What Is Preserved (By Design)

- **Cookies** (`~/Library/Cookies/Cookies.binarycookies`) — login sessions stay alive
- **LocalStorage** (`~/Library/Safari/LocalStorage/`) — site preferences and auth tokens
- **WebSQL databases** (`~/Library/Safari/Databases/`) — app-level data
- **Safari preferences** (`~/Library/Preferences/com.apple.Safari.plist`) — browser settings
- **Keychain entries** — saved passwords (if any)

### What Is Cleaned

- **All open windows/tabs** — no visual carryover between tasks
- **LastSession.plist** is NOT deleted — Safari's session restore is harmless since we explicitly manage windows via osascript

### When Deep Cleanup Happens

Full data deletion (cookies, localStorage, cache, history) only occurs during `recreate_vm()`:

- Every N cycles (configurable, default 10)
- On persona change (different identity = different service accounts)
- On explicit user request (`just vm-reset`)
- On suspected corruption (VNC failures, unresponsive automation)

### Timing Estimate

| Step | Estimated Time |
|------|---------------|
| Close all windows (osascript) | ~1-2s |
| Open fresh window (osascript) | ~1s |
| Verification check | ~0.5s |
| **Total** | **~3-5s** |

This is well within the RFC's <5s budget for `reset_browser_state()`.

---

## 3. TCC/Permissions

### What's Needed

For `reset_browser_state()` to work, `osascript` running on the VM needs permission to send Apple Events to Safari. This requires a TCC entry for `kTCCServiceAppleEvents`:

| Source | Target | Service |
|--------|--------|---------|
| `python3` (all paths) | `com.apple.Safari` | `kTCCServiceAppleEvents` |
| `python3` (all paths) | `com.apple.systemevents` | `kTCCServiceAppleEvents` |

The existing desktop-agent grants `python3 → System Events` for the correction strategies. We additionally need `python3 → Safari` for the browser quit/tab-close commands.

### How to Set It Up

The existing `_grant_automation_permissions()` in `vm_manager.py` provides the pattern. Extend it to also grant Safari permissions:

```python
# Insert automation permission: python3 → Safari
sql_safari = (
    "INSERT OR REPLACE INTO access "
    "(service, client, client_type, auth_value, auth_reason, "
    "auth_version, indirect_object_identifier, "
    "indirect_object_identifier_type, flags) "
    "VALUES ('kTCCServiceAppleEvents', "
    f"'{python_path}', 1, 2, 4, 1, "
    "'com.apple.Safari', 0, 0)"
)
```

The TCC.db location is `~/Library/Application Support/com.apple.TCC/TCC.db` (user-level).

### SIP Considerations

Direct TCC.db manipulation requires SIP to be disabled or relaxed. In Lume macOS VMs:

- **SIP status in Lume VMs is unknown and needs testing.** Apple Virtualization Framework VMs may have SIP enabled by default.
- If SIP is enabled, the `sqlite3` INSERT will fail silently or with a permission error.
- **Recovery mode boot** is available via `lume run <name> --recovery-mode`, which could be used to disable SIP during VM preparation: `csrutil disable` from Terminal in recovery mode.
- The `lume create --unattended` setup may already configure SIP — needs verification.

### Fallback if TCC Manipulation Fails

If TCC.db writes fail (SIP enabled, database locked, etc.):

1. **Manual grant**: During `just prepare-vm`, the agent or user manually grants permissions in System Settings > Privacy & Security > Automation
2. **Skip osascript for Safari quit**: Use `pkill -f Safari` instead of `osascript -e 'tell application "Safari" to quit'`. This is a hard kill — Safari won't flush memory state cleanly, but since we're deleting all files anyway, any unflushed cookies are discarded.
3. **The desktop-agent's timeout-based fallback**: osascript calls are wrapped with 10-second timeouts, and correction strategies have multiple escalation paths.

### Recommendation

Grant both Safari and System Events permissions during `just prepare-vm`. If SIP blocks TCC.db manipulation, boot into recovery mode once to disable SIP as part of VM preparation. This is a one-time setup cost.

---

## 4. VM Recreation

### Strategy: Clone from Pristine Image

Since Lume has no snapshots, the recreation strategy uses `lume clone`:

1. **One-time setup** (`just prepare-vm`): Create a pristine VM with all permissions configured
2. **Clone for use**: Clone the pristine VM to the active VM name
3. **Recreation**: Stop active VM → Delete active VM → Clone from pristine again

### Step-by-Step Process

```bash
# One-time: Create and configure the pristine image
lume pull macos-sequoia-cua:latest --name soren-pristine
# OR: create from scratch
lume create soren-pristine --os macos --ipsw latest \
    --cpu 4 --memory 8GB --disk-size 64GB --display 1280x720 \
    --unattended sequoia

# Configure pristine: boot, grant TCC permissions, configure Safari, stop
lume run soren-pristine --no-display
# ... (grant permissions, configure settings via CUA Computer) ...
lume stop soren-pristine

# Create the active VM by cloning
lume clone soren-vm soren-pristine

# --- During recreate_vm() ---
lume stop soren-vm
lume delete soren-vm --force
lume clone soren-vm soren-pristine
lume run soren-vm --no-display
```

### Via HTTP API (Preferred for Async Python)

```python
async def recreate_vm(self) -> None:
    """Full VM teardown and recreation from pristine clone."""
    async with httpx.AsyncClient(base_url=self.lume_url) as client:
        # Stop the active VM
        await client.post(f"/lume/vms/{self.vm_name}/stop")

        # Delete the active VM
        await client.delete(f"/lume/vms/{self.vm_name}")

        # Clone from pristine
        await client.post("/lume/vms/clone", json={
            "name": self.pristine_name,
            "newName": self.vm_name,
        })

        # Start the fresh clone
        await client.post(f"/lume/vms/{self.vm_name}/run", json={
            "noDisplay": True,
        })

    # Wait for VM to become responsive
    await self._wait_for_ready()
```

### Timing Estimate

| Step | Estimated Time | Notes |
|------|---------------|-------|
| `lume stop` | 5-10s | Graceful shutdown |
| `lume delete` | 2-5s | File deletion, ~30-50GB with sparse files |
| `lume clone` | 15-40s | Full disk copy; sparse files help significantly |
| `lume run` + boot | 20-30s | Cold boot to responsive state |
| TCC permission grants | 3-5s | Via CUA Computer `run_command` |
| **Total** | **45-90s** |  |

The RFC estimated 60-90s, which appears realistic. The `lume clone` step is the wildcard — it depends on disk I/O speed and the sparse file compression ratio of the VM image. On an internal SSD (Apple Silicon Mac), this should be toward the lower end.

### Alternative: Pull Instead of Clone

If `lume clone` proves too slow (large VM image):

```bash
lume stop soren-vm
lume delete soren-vm --force
lume pull macos-sequoia-cua:latest --name soren-vm
```

Pulling from the local image cache (if caching is enabled via `lume config caching set true`) could be faster than cloning if the image is already cached. However, this loses any custom configuration (TCC permissions, Safari settings) applied during `just prepare-vm`.

**Recommendation**: Use `lume clone` from a locally maintained pristine VM. The clone preserves all configuration, and the timing is acceptable.

---

## 5. Post-Reset Verification

### What a Clean Reset Looks Like

Since the window-management strategy preserves session data by design, "contamination" is redefined. A successful reset means:

- All Safari windows from the previous task are closed
- Exactly one fresh/empty Safari window is open
- No task-related content is visible (no stale tabs, no previous service dashboards)

Login sessions visible in the cookie store are **expected and desired** — they're the whole point of preserving sessions.

### Verification Heuristics

#### Tier 1: Window Count Check (Fast, ~0.5s)

After `reset_browser_state()` completes, verify exactly one window:

```python
async def verify_reset(self) -> list[str]:
    """Return list of issues found after reset."""
    issues = []

    count_raw = await self._run_osascript(
        self.iface,
        'tell application "Safari" to count of windows'
    )
    try:
        count = int(count_raw.strip())
    except ValueError:
        count = -1

    if count != 1:
        issues.append(f"Expected 1 window, got {count}")

    return issues
```

#### Tier 2: URL Check (~0.5s)

Verify the fresh window is on a start/blank page, not showing content from the previous task:

```python
url_raw = await self._run_osascript(
    self.iface,
    'tell application "Safari" to URL of current tab of front window'
)
if url_raw and "previous-service.com" not in url_raw:
    pass  # Clean
```

#### Tier 3: Screenshot-Based Verification (Expensive, ~3-5s)

On the first screenshot of a new task, verify no stale content from the previous task is visible:

```python
async def verify_clean_window(self, screenshot: bytes) -> bool:
    """Ask the VLM if the browser window appears clean/empty."""
    prompt = (
        "Look at this Safari browser screenshot. Is this a fresh/empty "
        "browser window? It should show a blank page, start page, or "
        "favorites. There should be no content from a previous task. "
        "Respond CLEAN if it appears to be a fresh window, STALE if "
        "it shows content from a previous browsing session."
    )
    response = await self.vlm.verify(screenshot, prompt)
    return "CLEAN" in response
```

### Decision Flow

```
After reset_browser_state():
  ├─ Window count == 1? ──No──► Retry close + open sequence
  ├─ URL is start/blank? ──No──► Close window, open new one
  └─ All clear ──► Proceed to next task
      └─ On first screenshot:
          └─ Stale content visible? ──Yes──► Close window, open new one, retry
```

### When to Trigger Deep Reset (recreate_vm)

Deep reset is reserved for situations where session-level issues occur:

- Persona change (different identity requires different login sessions)
- Periodic hygiene (every N cycles)
- Safari becomes unresponsive to osascript commands
- VNC screenshot capture fails repeatedly

---

## 6. VNC Screenshot Compatibility

### Current Status: Fully Compatible

The existing `vnc_screenshot.py` from the desktop-agent framework is proven working with Lume and needs no changes for Soren. The implementation:

1. **Auto-discovers VNC connection** from `lume ls --format json` via the `vncUrl` field
2. **Parses VNC URL** format `vnc://:password@host:port`
3. **Captures screenshots** via `vncdotool --server host::port --password pw capture output.png`
4. **Returns PNG bytes** suitable for VLM inference

### Key Details

- **VNC port**: Auto-assigned by Lume (`--vnc-port 0` default). Discovered at runtime from `lume ls`.
- **Password**: Set by Lume, included in the VNC URL.
- **vncdotool**: Must be installed on the **host** machine (not in the VM). Already a dependency in `pyproject.toml`.
- **Screenshot freshness**: VNC reads the actual guest OS framebuffer, bypassing the stale `CGDisplayCreateImage` issue in Apple Virtualization Framework.

### What to Watch For

- If Lume changes the `vncUrl` field name or format in `lume ls` JSON output, the parser in `_parse_vnc_url()` will break. The regex is: `vnc://:([^@]+)@([^:]+):(\d+)`.
- The VNC port changes each time a VM is started (since it's auto-assigned). The `patch_computer_from_lume()` function handles this by re-querying `lume ls` each time.
- After `recreate_vm()`, a new `patch_computer_from_lume()` call is needed because the clone gets a new VNC port on boot.

### No Changes Required

The existing `vnc_screenshot.py` can be copied to `src/soren/patches/vnc_screenshot.py` as-is. The only adaptation needed is integrating the patch call into Soren's `VMManager.connect_computer()`.

---

## 7. Proof-of-Concept Outline

A validation script that exercises the window-management reset flow against a live Lume VM.

### Prerequisites

- Lume CLI installed, `lume serve` running
- A macOS VM running (e.g., `soren-vm`)
- `vncdotool` installed on host
- Python 3.12+ with `cua-computer` and `httpx`

### Script: `scripts/validate_browser_reset.py`

The full POC script is at `scripts/validate_browser_reset.py`. It validates:

1. Open Safari and navigate to sites that set cookies (simulating login sessions)
2. Verify cookies exist and multiple windows are open
3. Run window-management reset (close all windows, open fresh one)
4. Verify cookies are **PRESERVED** (critical assertion — sessions must survive)
5. Verify localStorage/Safari data directory is **PRESERVED**
6. Verify exactly one clean window is open on a blank/start page
7. Navigate to verify cookies are still sent to the server
8. Run 3 rapid reset cycles to test stability
9. Test VM recreation via `lume clone` (deep reset path)

### What This Validates

| Test | What It Proves |
|------|---------------|
| Cookie preservation | Login sessions survive window reset |
| Window close/open | osascript window management works (TCC permissions correct) |
| Single window state | Exactly one fresh window after reset |
| Stability over cycles | Reset is reliable across repeated rapid invocations |
| Clone recreation | `lume clone` works for deep VM reset |

### What It Doesn't Validate (Deferred to Phase 2 Integration Tests)

- Real login session persistence across reset (requires actual service login + 2FA)
- Multi-service session coexistence (cookies from service A don't interfere with service B)
- Long-running stability (N-cycle endurance test with real task execution)
- VLM-based clean-window verification

---

## 8. Risk Assessment

### 🟢 Low Risk: VNC Screenshot Compatibility

**Probability**: Very low
**Impact**: Medium (blocks all vision-based automation)
**Status**: Proven working in the desktop-agent framework. Same `vncdotool` + `lume ls` approach. Lume's VNC integration is stable across 0.2.x releases. The only risk is a JSON field name change in `lume ls` output.
**Mitigation**: The existing `vnc_screenshot.py` has a regex parser with a clear error message if the format changes.

### 🟢 Low Risk: Window Close/Open Reliability

**Probability**: Very low
**Impact**: Low (can retry or force-kill + reopen)
**Status**: `close every window` and `make new document` are basic Apple Events that Safari has supported since the earliest osascript versions. Far simpler and more reliable than the previous approach which required coordinating quit + file deletion + relaunch.
**Mitigation**: If osascript fails, fall back to `pkill -f Safari; sleep 1; open -a Safari`.

### 🟡 Medium Risk: TCC Permission Grants

**Probability**: Medium (SIP status in Lume VMs is unknown)
**Impact**: Medium (osascript commands for window management will fail)
**Status**: The desktop-agent's TCC manipulation works for System Events, but we haven't tested Safari-specific grants. SIP may block `sqlite3` writes to TCC.db.
**Mitigation**: Three fallbacks available:
1. Boot VM in recovery mode during `just prepare-vm` to disable SIP
2. Manual grant during VM preparation (one-time user action in System Settings)
3. Use `pkill + open` as a less graceful fallback (loses fine-grained window control)

### 🟡 Medium Risk: Session Cookie Accumulation

**Probability**: Medium
**Impact**: Low-medium (browser gradually accumulates cookies from many services)
**Status**: Over many cycles, cookies and localStorage from various services will accumulate. This could theoretically slow Safari or cause storage limits to be hit, though in practice the data volume is tiny.
**Mitigation**: Periodic `recreate_vm()` every N cycles provides a guaranteed cleanup. The N can be tuned based on observed behavior — start at 10, increase if sessions are stable.

### 🟡 Medium Risk: Cross-Service Cookie Interference

**Probability**: Low-medium
**Impact**: Medium (one service's cookies could affect another service's behavior)
**Status**: When session data from service A persists while the agent works on service B, there's a theoretical risk of interference. In practice, web cookies are domain-scoped and don't typically interfere cross-domain.
**Mitigation**: If interference is observed, the fallback is to trigger `recreate_vm()` when switching between services (rather than on a fixed schedule).

### 🟡 Medium Risk: Clone-Based Recreation Timing

**Probability**: Medium
**Impact**: Low (slower than expected is annoying, not blocking)
**Status**: `lume clone` copies the full VM disk image. With sparse file optimization (Lume v0.2.0+), actual disk I/O may be 15-25GB rather than the full 64GB. No benchmarks exist.
**Mitigation**: If clone is too slow (>2 minutes), alternatives include:
1. Reduce VM disk size (32GB instead of 64GB)
2. Keep the pristine VM on a fast external NVMe
3. Use `lume pull` from a locally cached image instead of clone

### 🟢 Low Risk: Strategy Simplicity (Improved vs. Previous)

**Probability**: N/A
**Impact**: Positive
**Status**: The window-management approach is *simpler* than the original file-deletion approach. It has fewer moving parts (no file paths to track, no quit-before-delete coordination, no memory-cache race conditions). The main validation needed is that osascript window commands work reliably in a Lume VM.
**Mitigation**: The POC script in Section 7 validates this. Budget 1-2 hours for the POC.

---

## 9. Recommendations

### Primary Strategy: Window Management + Periodic VM Recreation

The revised two-tier reset strategy prioritizes preserving login sessions:

1. **`reset_browser_state()`**: Close all Safari windows, open fresh window via osascript (Section 2). Target: <5s. Cookies/sessions preserved.
2. **`recreate_vm()`**: Clone from pristine via `lume clone` (Section 4). Target: <90s. Used periodically or on persona change.
3. **Post-reset verification**: Window count check (Tier 1) after every reset, screenshot-based (Tier 3) on first task (Section 5).
4. **TCC permissions**: Grant during `just prepare-vm`, disable SIP if needed (Section 3).

### Fallback 1: Hard Kill + Reopen

If TCC permissions prove unworkable for osascript:

- Replace osascript window management with `pkill -f Safari; sleep 1; open -a Safari`
- Less graceful (loses any unsaved form state) but functionally equivalent
- Cookies/sessions still preserved since we're not deleting any files

### Fallback 2: Service-Scoped Recreation

If cross-service cookie interference becomes a problem:

- Trigger `recreate_vm()` when switching between different services rather than on a fixed schedule
- The agent logs into the target service once per VM lifecycle
- Trade-off: more VM recreations if tasks alternate between services

### Fallback 3: Full Data Deletion (Original Strategy)

If session preservation causes unforeseen issues:

- Revert to the original file-deletion approach (quit Safari, delete all cookies/localStorage/cache)
- The 17 Safari data paths documented in the original discovery are still valid
- Accept the re-login cost and 2FA burden

### Action Items for Phase 2

| # | Action | Priority | Estimate |
|---|--------|----------|----------|
| 1 | Run POC script (Section 7) against a live Lume VM | **P0** | 1-2h |
| 2 | Verify SIP status in Lume VMs; disable if needed | **P0** | 1-2h |
| 3 | Grant TCC permissions for Safari + System Events | **P0** | 1h |
| 4 | Implement `reset_browser_state()` in VMManager (window management) | P1 | 1-2h |
| 5 | Implement `recreate_vm()` with `lume clone` | P1 | 2-3h |
| 6 | Implement post-reset verification (window count) | P1 | 1h |
| 7 | Benchmark `lume clone` timing on target hardware | P2 | 1h |
| 8 | Update `VMConfig.lume_url` default to port 7777 | P2 | 5min |
| 9 | Evaluate `macos-sequoia-cua` image vs custom build | P2 | 2h |
| 10 | Test real login session persistence after window reset | P1 | 2h |

---

## Existing Components & Reuse Plan

### What We Will Reuse

| Component | Source | How |
|-----------|--------|-----|
| `vnc_screenshot.py` | `desktop-agent/helpers/vnc_screenshot.py` | Copy to `src/soren/patches/vnc_screenshot.py`, no modifications |
| `_grant_automation_permissions()` | `desktop-agent/helpers/vm_manager.py` | Extend with Safari-specific TCC grants |
| `_run_lume()` helper | `desktop-agent/helpers/vm_manager.py` | Migrate to async HTTP calls via Lume API |
| `is_vm_running()` | `desktop-agent/helpers/vm_manager.py` | Adapt to use HTTP API instead of CLI |
| `_wait_for_ready()` | `desktop-agent/helpers/vm_manager.py` | Reuse pattern with CUA Computer readiness check |

### What We Will Not Reuse

| Component | Reason |
|-----------|--------|
| `restore_snapshot()` | Snapshots don't exist; replaced by `recreate_vm()` via clone |
| `_start_vm_background()` | Moving to HTTP API (`POST /lume/vms/:name/run`) instead of subprocess |

### Consolidation Opportunities

The VM management code in `vm_manager.py` currently uses CLI subprocess calls (`lume ls`, `lume run`, `lume stop`). Soren should use the HTTP API exclusively via `lume serve`, consolidating all Lume interactions into a single `httpx.AsyncClient` wrapper. This eliminates subprocess management, provides structured JSON responses, and matches the async architecture.
