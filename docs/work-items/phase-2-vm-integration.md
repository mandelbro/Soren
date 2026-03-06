# Phase 2: VM Integration — Work Items

**Phase Goal**: Reliable VM lifecycle management with CUA Computer connection, VNC screenshot patching, browser state reset, and VM recreation from pristine images.

**Estimated Phase Duration**: 2 weeks (Mar 10 – Mar 23, 2026)

**Total Story Points**: 18

**Exit Criteria (Phase-Level)**:
- `just prepare-vm` creates a VM with a pristine image and grants TCC permissions
- `just vm-status` reports VM state via Lume HTTP API
- VNC screenshot returns fresh framebuffer data (not stale `CGDisplayCreateImage`)
- `reset_browser_state()` closes all Safari windows and opens a fresh one (sessions preserved)
- `recreate_vm()` restores a clean OS environment from pristine clone
- All integration tests pass against a live Lume VM

---

## Dependency Graph

```
WI 2.1 (VMManager Core)
  ├── WI 2.2 (VNC Screenshot & Computer) ──► WI 2.3 (Browser Reset)
  └── WI 2.4 (VM Recreation & Pristine Image)
```

**Parallel tracks after WI 2.1**:
- **Track A** (computer connection): WI 2.2 → WI 2.3
- **Track B** (image management): WI 2.4

**Phase dependency**: Phase 1 must be complete (models, config, CLI skeleton).

---

## Work Items

### WI 2.1: [VM] VMManager Core & Lume HTTP API Client — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phase 1 (WI 1.1 CLI skeleton, WI 1.5 settings with VMConfig)
- **Completed**: Yes

**User Story**: As a developer, I want a VMManager service that communicates with Lume via its HTTP API to manage VM lifecycle, so that all downstream VM operations (screenshot, reset, recreation) have a reliable async foundation without subprocess calls.

**Description**: Implement the core VMManager service that wraps the Lume HTTP API (port 7777) using httpx AsyncClient. The manager handles VM boot, shutdown, health checking, and readiness detection. All Lume interactions go through the HTTP API exclusively — no CLI subprocess calls. The VMConfig model from Phase 1 provides configuration (VM name, memory, display resolution, Lume URL). Implements `just vm-status` and `just vm-stop` CLI commands.

**Deliverables:**

1. **Lume HTTP API Client** — `src/soren/services/vm_manager.py`
   - `LumeClient` async wrapper around Lume's REST API at `http://localhost:7777`
   - Methods: `list_vms()`, `get_vm(name)`, `create_vm(config)`, `delete_vm(name)`, `clone_vm(source, target)`, `start_vm(name, opts)`, `stop_vm(name)`, `pull_image(image)`
   - JSON response parsing with typed return values
   - Connection error handling with clear messages ("Is `lume serve` running?")
   - Configurable base URL via `VMConfig.lume_url` (default `http://localhost:7777`)

2. **VMManager Service** — `src/soren/services/vm_manager.py`
   - `VMManager(settings: SorenSettings)`: manages the active VM lifecycle
   - `async ensure_ready() -> None`: start VM if not running, wait until responsive (screenshot succeeds)
   - `async stop() -> None`: graceful shutdown via HTTP API
   - `async is_running() -> bool`: query Lume API for VM state
   - `async is_healthy() -> bool`: check VM responsiveness via screenshot attempt
   - `_wait_for_ready(timeout_s: int = 120)`: poll VM state with exponential backoff until responsive
   - Internal state tracking: `_vm_name`, `_computer`, `_cycle_count`

3. **CLI Integration**
   - `just vm-status` → `soren vm status`: queries Lume API, displays VM name, state, memory, VNC URL in a `rich` table
   - `just vm-stop` → `soren vm stop`: graceful shutdown with confirmation

4. **Test Suite** — minimum 12 tests (`tests/test_vm_manager.py`)
   - `LumeClient` constructs correct HTTP requests for each endpoint
   - `LumeClient` handles connection refused with clear error message
   - `LumeClient` parses JSON response for `list_vms()` and `get_vm()`
   - `VMManager.ensure_ready()` starts VM when not running
   - `VMManager.ensure_ready()` is a no-op when VM is already running
   - `VMManager.stop()` sends stop request via HTTP API
   - `VMManager.is_running()` returns True when VM state is "running"
   - `VMManager.is_running()` returns False when VM state is "stopped"
   - `VMManager._wait_for_ready()` retries on transient failures
   - `VMManager._wait_for_ready()` raises `TimeoutError` after timeout
   - CLI `vm status` displays formatted output (mock Lume API)
   - CLI `vm stop` sends stop request (mock Lume API)

**Acceptance Criteria:**
- [ ] `VMManager` communicates exclusively via Lume HTTP API (no subprocess calls)
- [ ] `VMManager.ensure_ready()` starts a stopped VM and waits until it responds to screenshot requests
- [ ] `VMManager.stop()` gracefully shuts down the VM
- [ ] `VMManager.is_running()` and `is_healthy()` return correct state from Lume API
- [ ] Connection failures produce actionable error messages mentioning `lume serve`
- [ ] `just vm-status` displays VM state in a formatted table
- [ ] `just vm-stop` stops the running VM
- [ ] VMConfig default `lume_url` is `http://localhost:7777` (not port 3000)
- [ ] Test suite includes minimum 12 tests, 100% coverage for `vm_manager.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **Lume HTTP API stability**: API surface is documented and stable across 0.2.x. **Mitigation**: Pin Lume version; test against live instance in integration tests.
- 🟡 **Lume serve availability**: `lume serve` must be running before Soren starts. **Mitigation**: Clear error message on connection refused; document prerequisite in README.

**References:** Discovery §Lume VM Browser Reset (Section 1: HTTP API); RFC §VMManager interface; RFC §Performance targets (VM boot < 45s)

---

### WI 2.2: [VM] VNC Screenshot Patch & CUA Computer Connection — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 2.1 (VMManager provides VM lifecycle)
- **Completed**: Yes

**User Story**: As a developer, I want a CUA Computer connected to the running VM with VNC-based screenshots replacing the stale framebuffer, so that the agent execution layer receives fresh, accurate screenshots for vision-based automation.

**Description**: Implement `VMManager.connect_computer()` which creates a CUA `Computer` instance using the v0.5.14 constructor API and monkey-patches its `interface.screenshot()` method to use VNC capture via vncdotool. The VNC screenshot patch is adapted from `desktop-agent/helpers/vnc_screenshot.py` — it auto-discovers the VNC connection from the Lume API, parses the VNC URL, and captures screenshots via vncdotool subprocess. This addresses the known issue where `CGDisplayCreateImage` returns stale framebuffer data in Apple Virtualization Framework VMs.

**Deliverables:**

1. **VNC Screenshot Patch** — `src/soren/patches/vnc_screenshot.py`
   - `patch_computer_screenshot(computer, lume_client)`: replaces `computer.interface.screenshot()` with VNC capture
   - `_discover_vnc_url(lume_client, vm_name) -> tuple[str, int, str]`: parse VNC host, port, password from Lume API response
   - `_capture_vnc_screenshot(host, port, password) -> bytes`: capture via vncdotool, return PNG bytes
   - VNC URL regex parser for `vnc://:password@host:port` format
   - Temporary file management for vncdotool capture output
   - Error handling: VNC connection refused, timeout, stale capture

2. **CUA Computer Connection** — additions to `src/soren/services/vm_manager.py`
   - `async connect_computer() -> Computer`: creates `Computer(os_type="macos", provider_type="lume", name=vm_name, display=resolution, memory=memory)` with v0.5.14 API
   - Applies VNC screenshot patch after Computer initialization
   - Stores `_computer` reference for reuse within a cycle
   - Re-patches after VM recreation (new VNC port on boot)

3. **Test Suite** — minimum 10 tests (`tests/test_vnc_screenshot.py`, additions to `tests/test_vm_manager.py`)
   - VNC URL parser extracts host, port, password from standard format
   - VNC URL parser handles missing password gracefully
   - VNC URL parser raises clear error on malformed URL
   - `_capture_vnc_screenshot()` invokes vncdotool with correct arguments
   - `_capture_vnc_screenshot()` returns PNG bytes on success
   - `_capture_vnc_screenshot()` raises on vncdotool timeout
   - `patch_computer_screenshot()` replaces the screenshot method
   - Patched screenshot method returns VNC capture (not original)
   - `connect_computer()` creates Computer with correct constructor args
   - `connect_computer()` applies VNC patch before returning
   - @pytest.mark.integration: full VNC screenshot against live VM

**Acceptance Criteria:**
- [ ] `connect_computer()` returns a `Computer` instance with VNC-patched screenshot method
- [ ] VNC screenshot returns fresh PNG bytes from the guest OS framebuffer
- [ ] VNC URL auto-discovered from Lume API (no hardcoded port)
- [ ] Computer constructor uses v0.5.14 API: `os_type="macos"`, `provider_type="lume"`, explicit `name`, `display`, `memory`
- [ ] Screenshot method works after VM recreation (re-discovers new VNC port)
- [ ] vncdotool errors produce clear messages (connection refused, timeout)
- [ ] Test suite includes minimum 10 tests, 100% coverage for VNC patch module
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **VNC screenshot approach**: Proven working in desktop-agent framework. Same vncdotool + lume approach. **Mitigation**: Direct code reuse from desktop-agent.
- 🟡 **VNC port auto-assignment**: Port changes on each VM start. **Mitigation**: Always re-query Lume API; never cache VNC port across restarts.
- 🟡 **vncdotool host dependency**: Must be installed on host, not VM. **Mitigation**: Validated as existing dependency in `pyproject.toml`; check at startup.

**References:** Discovery §Lume VM Browser Reset (Section 6: VNC Compatibility); RFC §VMManager.connect_computer(); RFC §Appendix: Prior Art (VNC screenshot patch)

---

### WI 2.3: [VM] Window Management Reset & Post-Reset Verification — Complete Implementation

- **Estimated Points**: 3
- **Dependencies**: WI 2.1 (VMManager), WI 2.2 (Computer connection for running commands in VM)
- **Completed**: Yes

**User Story**: As a system operator, I want all browser windows closed and a fresh window opened between task cycles, while preserving login sessions, so that tasks are visually isolated without requiring re-login (especially on 2FA-protected services).

**Description**: Implement `VMManager.reset_browser_state()` using a window-management approach: close all Safari windows via osascript, then open a single fresh window. Cookies, localStorage, and login sessions are **intentionally preserved** to avoid re-login overhead. Post-reset verification confirms exactly one clean window is open. The `just vm-reset` command exposes this for manual use. Deep data cleanup happens only during `recreate_vm()`.

**Deliverables:**

1. **Window Management Reset** — additions to `src/soren/services/vm_manager.py`
   - `async reset_browser_state() -> ResetResult`: window management sequence
   - Phase 1: Close all Safari windows via osascript (`close every window`)
   - Phase 2: Open a single fresh window (`make new document`, `activate`)
   - Phase 3: Verify window count == 1
   - `ResetResult` model: `success: bool`, `duration_seconds: float`, `window_count: int`, `warnings: list[str]`

2. **Post-Reset Verification** — `src/soren/services/reset_verification.py`
   - `async verify_reset(computer) -> list[str]`: returns issues found
   - Tier 1 — Window count check (~0.5s): verify exactly one Safari window open
   - Tier 2 — URL check (~0.5s): verify fresh window is on start/blank page
   - Decision logic: issues found → retry close/open; if repeated failure → log warning

3. **CLI Integration**
   - `just vm-reset` → `soren vm reset`: runs `reset_browser_state()` and displays results

4. **Test Suite** — minimum 10 tests (`tests/test_window_reset.py`)
   - Reset sequence sends osascript close-all-windows command
   - Reset sequence sends osascript open-new-window command
   - Reset verifies window count after close/open
   - `ResetResult` captures duration and window count
   - Verification detects multiple windows open (failure case)
   - Verification detects zero windows (Safari not running)
   - Verification passes with exactly one window on blank page
   - Reset with failed osascript falls back to pkill + reopen
   - Cookies/localStorage confirmed preserved after reset (not deleted)
   - CLI `vm reset` displays results with status indicators
   - @pytest.mark.integration: full window reset against live VM

**Acceptance Criteria:**
- [ ] `reset_browser_state()` closes all windows and opens one fresh window
- [ ] Reset completes within 5 seconds (RFC budget: <5s)
- [ ] Cookies, localStorage, and login sessions are **NOT** deleted
- [ ] Post-reset verification confirms exactly one clean window
- [ ] `just vm-reset` displays reset outcome with timing
- [ ] Failed osascript gracefully falls back to `pkill -f Safari; open -a Safari`
- [ ] Test suite includes minimum 10 tests, 100% coverage for reset and verification modules
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **osascript TCC permissions**: `python3 → Safari` Apple Events permission required. **Mitigation**: Granted during `just prepare-vm`; fallback to `pkill + open` if osascript fails.
- 🟢 **Simpler than previous approach**: Window management is simpler than the original file-deletion strategy — fewer moving parts, no race conditions with memory-cached state.

**References:** Discovery §Lume VM Window Management (Section 2: Window Management Strategy); RFC §Environment Reset Invariants; RFC §VMManager.reset_browser_state()

---

### WI 2.4: [VM] VM Recreation & Pristine Image Management — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 2.1 (LumeClient for API calls)
- **Completed**: Yes

**User Story**: As a system operator, I want the VM recreated from a pristine clone periodically or on persona change, so that accumulated session data is cleaned up and a fresh identity environment is available when needed.

**Description**: Implement `VMManager.recreate_vm()` using `lume clone` from a pristine image, and `just prepare-vm` which creates the pristine base VM with all necessary configuration (TCC permissions, Safari settings, display resolution). TCC automation permissions for `python3 → Safari` and `python3 → System Events` are granted during preparation. The recreation trigger is configurable via `vm.recreate_every_n_cycles` in settings (default 10) or on contamination detection.

**Deliverables:**

1. **VM Recreation** — additions to `src/soren/services/vm_manager.py`
   - `async recreate_vm() -> None`: stop active VM → delete → clone from pristine → start → wait for ready → re-patch VNC screenshot
   - Uses Lume HTTP API exclusively: `POST /lume/vms/:name/stop`, `DELETE /lume/vms/:name`, `POST /lume/vms/clone`, `POST /lume/vms/:name/run`
   - `_should_recreate() -> bool`: returns True if `cycle_count % recreate_every_n_cycles == 0` or contamination detected
   - `_increment_cycle_count()`: called after each cycle; persisted in memory (resets on process restart)

2. **Pristine Image Preparation** — `src/soren/services/vm_prepare.py`
   - `async prepare_pristine_vm(settings) -> None`: full VM preparation sequence
   - Step 1: Pull or create base VM (`macos-sequoia-cua:latest` or custom)
   - Step 2: Boot VM, wait for ready
   - Step 3: Grant TCC permissions via `sqlite3` INSERT into `~/Library/Application Support/com.apple.TCC/TCC.db`
   - Permissions: `python3 → com.apple.Safari` (kTCCServiceAppleEvents), `python3 → com.apple.systemevents` (kTCCServiceAppleEvents)
   - Step 4: Configure Safari defaults if needed
   - Step 5: Stop VM, save as pristine
   - SIP fallback: if TCC.db manipulation fails, log warning with instructions for recovery mode SIP disable

3. **CLI Integration**
   - `just prepare-vm` → `soren vm prepare`: creates and configures pristine image (one-time setup)

4. **Test Suite** — minimum 10 tests (`tests/test_vm_recreation.py`)
   - `recreate_vm()` calls stop → delete → clone → start in correct order
   - `recreate_vm()` waits for VM readiness after clone boot
   - `recreate_vm()` re-patches VNC screenshot after recreation
   - `_should_recreate()` returns True at N-cycle intervals
   - `_should_recreate()` returns True when contamination flag is set
   - `_should_recreate()` returns False mid-interval with no contamination
   - TCC permission SQL constructs correct INSERT statement
   - `prepare_pristine_vm()` follows the correct preparation sequence
   - `prepare_pristine_vm()` handles SIP-blocked TCC.db with warning
   - CLI `vm prepare` invokes preparation sequence
   - @pytest.mark.integration: full recreation against live Lume VM (clone timing benchmark)

**Acceptance Criteria:**
- [ ] `recreate_vm()` completes full stop → delete → clone → start cycle in under 90 seconds
- [ ] Pristine VM has TCC permissions for `python3 → Safari` and `python3 → System Events`
- [ ] `just prepare-vm` creates a configured pristine image (one-time setup)
- [ ] Recreation triggered automatically every N cycles (configurable via `vm.recreate_every_n_cycles`)
- [ ] Recreation triggered immediately when contamination is detected
- [ ] VNC screenshot patch re-applied after recreation (new VNC port discovered)
- [ ] SIP-blocked TCC manipulation produces clear fallback instructions
- [ ] Test suite includes minimum 10 tests, 100% coverage for recreation and preparation
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **TCC permission grants blocked by SIP**: SIP status in Lume VMs is unknown. **Mitigation**: Recovery mode boot (`lume run --recovery-mode`) to disable SIP during preparation; manual grant fallback.
- 🟡 **Clone timing variability**: `lume clone` copies full VM disk image; timing depends on disk I/O and sparse file compression. **Mitigation**: Benchmark during integration test; consider reduced disk size (32 GB) if >2 minutes.
- 🟢 **Lume clone API**: Well-documented HTTP endpoint. **Mitigation**: Direct API call, structured response parsing.

**References:** Discovery §Lume VM Browser Reset (Sections 3, 4: TCC/Permissions, VM Recreation); RFC §VMManager.recreate_vm(); RFC §Trade-offs: Browser-Level Reset

---

## Summary

| WI | Title | Points | Dependencies | Track |
|----|-------|--------|-------------|-------|
| 2.1 | VMManager Core & Lume HTTP API Client | 5 | Phase 1 | — |
| 2.2 | VNC Screenshot Patch & CUA Computer Connection | 5 | 2.1 | A (Computer) |
| 2.3 | Window Management Reset & Post-Reset Verification | 3 | 2.1, 2.2 | A (Computer) |
| 2.4 | VM Recreation & Pristine Image Management | 5 | 2.1 | B (Image) |
| | **Total** | **18** | | |

### Velocity Assumptions

- **Solo developer**: 10 points/week accounts for integration test overhead (real VM required for many tests).
- **Week 1 target** (10 pts): WI 2.1 (5) + WI 2.4 (5) — VMManager core and pristine image setup
- **Week 2 target** (8 pts): WI 2.2 (5) + WI 2.3 (3) — VNC patch, computer connection, window reset

### Test Coverage Summary

| WI | Minimum Tests | Coverage Target |
|----|--------------|-----------------|
| 2.1 | 12 | 100% for `vm_manager.py` (core methods) |
| 2.2 | 10 | 100% for `patches/vnc_screenshot.py`, `connect_computer()` |
| 2.3 | 10 | 100% for `reset_browser_state()`, `reset_verification.py` |
| 2.4 | 10 | 100% for `recreate_vm()`, `vm_prepare.py` |
| **Total** | **42** | |

### Exit Criteria Traceability

| Phase Exit Criterion | Verified By |
|---------------------|-------------|
| `just prepare-vm` creates VM with pristine image | WI 2.4 AC |
| `just vm-status` reports VM state | WI 2.1 AC |
| VNC screenshot returns fresh framebuffer data | WI 2.2 AC |
| `reset_browser_state()` closes windows, opens fresh one | WI 2.3 AC |
| `recreate_vm()` restores clean environment | WI 2.4 AC |
| All integration tests pass | All WIs |
