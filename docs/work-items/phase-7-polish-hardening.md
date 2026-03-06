# Phase 7: Polish & Hardening — Work Items

**Phase Goal**: Production-ready CLI experience, structured logging, performance validation against 32 GB target, failure-mode testing, and comprehensive documentation.

**Estimated Phase Duration**: 2 weeks (May 26 – Jun 8, 2026)

**Total Story Points**: 18

**Exit Criteria (Phase-Level)**:
- All `just` commands produce clean, formatted output via `rich`
- Structured logging captures events to `data/logs/soren.log` in JSON format
- Memory usage stays under 30 GB during a cycle on a 32 GB machine
- Failure-mode test matrix passes for all documented failure scenarios
- End-to-end test suite covers the full lifecycle
- README covers installation through first successful cycle

---

## Dependency Graph

```
WI 7.1 (Logging & CLI) ──►
                           WI 7.4 (Documentation & E2E)
WI 7.2 (Performance)  ──►
WI 7.3 (Failure Modes) ──►
```

**Parallel tracks**: WI 7.1, 7.2, and 7.3 are independent and can be developed in parallel. WI 7.4 depends on all three being complete (E2E tests exercise the hardened system; docs reference final behavior).

**Phase dependency**: Phases 1–6 must be complete (all features implemented).

---

## Work Items

### WI 7.1: [Polish] Structured Logging & Rich CLI Output — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phases 1–6 (all CLI commands implemented)
- **Completed**: No

**User Story**: As a system operator, I want structured JSON logging for machine analysis and rich terminal output for human readability, so that I can debug cycle failures from log files and monitor ongoing operations from the terminal.

**Description**: Replace ad-hoc print statements and basic logging across all services with `structlog` JSON logging and `rich` terminal output. Every service gets a bound logger with contextual fields (cycle_id, task_id, persona_id). Logs output to both `data/logs/soren.log` (JSON, machine-parseable) and stdout (human-readable via `rich` console). All CLI commands get `rich` tables, progress bars, and colored status indicators. Implement `just show-history` for viewing past cycle reports. Add configuration validation with clear, actionable error messages at startup.

**Deliverables:**

1. **Structured Logging** — `src/soren/services/logging.py`
   - `configure_logging(settings: SorenSettings) -> None`: set up structlog with JSON renderer for file output and `rich` renderer for console
   - File output: `data/logs/soren.log`, JSON format, append mode, rotated daily
   - Console output: human-readable with timestamps, log levels, colored key-value pairs
   - Bound loggers: each service creates a logger bound with service name; cycle-level context (cycle_id, task_id, persona_id) added at cycle start
   - Credential redaction: structlog processor that redacts any field named `password`, `passphrase`, `secret`, `credential`, or `token`
   - Log levels: DEBUG for step-by-step details, INFO for cycle progress, WARNING for recoverable issues, ERROR for failures

2. **Rich CLI Output** — updates across all CLI commands
   - `just list-tasks`: `rich.table.Table` with colored status (green=completed, yellow=queued, red=dead_letter)
   - `just list-personas`: `rich.table.Table` with persona details
   - `just list-playbooks`: `rich.table.Table` with file metadata
   - `just vm-status`: `rich.panel.Panel` with VM details
   - `just begin-soren-cycle`: `rich.progress.Progress` bar for cycle phases
   - `just show-history` → `soren history show`: display past cycle reports in a table with `--limit` flag (default 10); columns: cycle_id, started_at, task_title, result, duration, steps

3. **Configuration Validation** — `src/soren/services/config_validator.py`
   - `validate_runtime_config(settings) -> list[ConfigError]`: check all runtime prerequisites
   - Checks: Ollama reachable, required models pulled, Lume serve running, personas valid, credentials file exists, data directories writable
   - `ConfigError`: `field: str`, `message: str`, `severity: "error" | "warning"`
   - Validation runs at startup before cycle begins; errors block execution; warnings logged

4. **Test Suite** — minimum 12 tests (`tests/test_logging.py`, `tests/test_config_validator.py`)
   - structlog configured with JSON file renderer and rich console renderer
   - Credential redaction processor masks `password` field values
   - Credential redaction processor masks `secret` field values
   - Bound logger includes service name in output
   - Cycle context (cycle_id, task_id) appears in log entries
   - Log file created at `data/logs/soren.log`
   - Config validator detects unreachable Ollama
   - Config validator detects missing Lume serve
   - Config validator detects invalid persona files
   - Config validator returns empty list when all checks pass
   - Config errors block cycle execution
   - Config warnings allow cycle execution with logged warnings

**Acceptance Criteria:**
- [ ] All log output goes through structlog (no raw `print()` or `logging.info()` calls)
- [ ] JSON log entries written to `data/logs/soren.log` with timestamps, levels, and structured fields
- [ ] Console output uses `rich` for human-readable formatting with colors
- [ ] Credentials never appear in log output (password, secret, token fields redacted)
- [ ] `just show-history` displays past cycle reports in a formatted table
- [ ] Configuration validation runs at startup with clear, actionable error messages
- [ ] All CLI commands use `rich` tables/panels for output
- [ ] Test suite includes minimum 12 tests, 100% coverage for logging and config validator
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **structlog integration**: Well-documented library with native `rich` support. **Mitigation**: Follow structlog's recommended configuration pattern.
- 🟢 **Rich CLI output**: `rich` is already a dependency. Tables and panels are straightforward.
- 🟡 **Log rotation**: File-based log rotation may require `logging.handlers.RotatingFileHandler`. **Mitigation**: Use structlog's file handler with daily rotation; cap at 30 days.

**References:** RFC §Cross-cutting Concerns (Observability — structlog, rich, cycle reports); RFC §Performance targets (logging)

---

### WI 7.2: [Performance] Memory Profiling & Optimization — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phase 3 (WI 3.2 MemoryGuard), Phase 5 (full cycle for profiling)
- **Completed**: No

**User Story**: As a system operator, I want validated proof that the system stays within the 32 GB memory budget during real cycles, with optimized model loading and calibrated admission control thresholds, so that I can trust the system won't OOM on my machine.

**Description**: Profile actual memory consumption during a full cycle, validate the RFC's memory budget assumptions, and optimize model loading strategy. Implement a `just check-resources` command that validates available memory before starting a cycle. Benchmark model swap latency and calibrate MemoryGuard thresholds based on empirical data. Optimize the model loading sequence: pre-load UI-TARS before VM boot to overlap startup costs. Validate `OLLAMA_MAX_LOADED_MODELS` behavior with `keep_alive: 0`.

**Deliverables:**

1. **Memory Budget Benchmark** — `scripts/validate_memory_budget.py`
   - Adapted from the memory budget discovery's benchmark script outline
   - Measure actual memory consumption of each component: host OS, Lume VM, UI-TARS (MLX), Gemma3:12B (Ollama), Qwen3-VL (Ollama)
   - Measure model swap latency: Gemma3:12B ↔ Qwen3-VL, Gemma3:12B → gemma3:4b
   - Capture all four MemoryGuard signals at each checkpoint
   - Output: `memory-benchmark.json` with PASS/WARN/FAIL verdict
   - `just validate-memory-budget` → `uv run python scripts/validate_memory_budget.py`

2. **Model Loading Optimization** — updates to `src/soren/services/meta_agent.py`
   - Pre-load UI-TARS into MLX during VM boot (overlap startup costs)
   - Verify `OLLAMA_MAX_LOADED_MODELS=1` behavior: only one Ollama model loaded at a time
   - Verify `keep_alive: 0` causes immediate unload after each response
   - Batch reasoning calls to minimize model swaps (strategy from memory budget discovery)
   - Log model swap count and cumulative swap time in CycleReport

3. **Resource Check Command** — `src/soren/services/resource_checker.py`
   - `just check-resources` → `soren vm check-resources`: validate pre-cycle resource availability
   - Checks: total system memory ≥ 32 GB (warn if less), available memory ≥ 10 GB, Ollama responsive, UI-TARS model available, no other Ollama models loaded
   - Output: `rich` panel with resource status (green/yellow/red indicators)

4. **MemoryGuard Threshold Calibration**
   - Run benchmark during development to capture actual psutil vs `memory_pressure` divergence
   - Adjust trigger thresholds if empirical data shows the defaults are too aggressive (false positives) or too lenient (OOM risk)
   - Document calibrated thresholds in code comments

5. **Test Suite** — minimum 10 tests (`tests/test_resource_checker.py`, `tests/test_memory_benchmark.py`)
   - Resource checker detects insufficient memory
   - Resource checker detects Ollama unavailability
   - Resource checker produces correct output format
   - Benchmark script captures all four memory signals
   - Benchmark script measures model swap latency
   - Benchmark script produces PASS verdict on adequate hardware
   - Benchmark script produces WARN verdict on marginal hardware
   - Model swap count logged in CycleReport
   - Pre-load UI-TARS overlaps with VM boot
   - `OLLAMA_MAX_LOADED_MODELS=1` verified via `/api/ps` (at most one model loaded)

**Acceptance Criteria:**
- [ ] Memory benchmark validates peak usage under 30 GB on a 32 GB machine during a full cycle
- [ ] Model swap latency measured and logged: target < 8s per swap
- [ ] `just check-resources` validates pre-cycle resource availability with clear output
- [ ] UI-TARS pre-loaded during VM boot (startup time overlap)
- [ ] Model swap count and cumulative swap time appear in CycleReport
- [ ] MemoryGuard thresholds calibrated against empirical measurements
- [ ] Benchmark produces reproducible PASS/WARN/FAIL verdict
- [ ] Test suite includes minimum 10 tests
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Memory profiling accuracy**: psutil over-reports on Apple Silicon. **Mitigation**: Use all four signals; prioritize `memory_pressure` and `/api/ps` for ground truth.
- 🟡 **Hardware variability**: Results on one machine may not transfer. **Mitigation**: Benchmark outputs all raw data; document machine specs in results.
- 🟢 **Model pre-loading**: MLX model loading is well-understood. No known issues with early loading.

**References:** Discovery §Model Memory Budget (Sections 5, 6, 8: Budget Validation, Recommendations, Benchmark Script); RFC §Performance targets; RFC §Operational Contracts: Model Admission Control

---

### WI 7.3: [Hardening] Failure-Mode Testing & Retry Refinement — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phases 1–6 (all features for failure scenarios)
- **Completed**: No

**User Story**: As a system operator, I want confidence that every documented failure mode is handled gracefully — no crashes, no data loss, no orphaned state — so that I can run the agent unattended knowing it will degrade gracefully.

**Description**: Implement a failure-mode test matrix covering every documented failure scenario from the RFC. Each scenario is a test case that injects a specific failure condition and verifies the system's recovery behavior. Also refine retry logic for operational failures: exponential backoff for Ollama timeouts, VM boot retries, and browser reset retries. This WI is primarily a testing and hardening effort — the code changes are in error handling paths across existing services.

**Deliverables:**

1. **Failure-Mode Test Matrix** — `tests/test_failure_modes.py`
   - **VM boot failure**: VM doesn't start within timeout → cycle aborts with clear error, no orphaned state
   - **VNC stale screenshot**: vncdotool returns stale/empty image → retry screenshot, escalate to VM recreation
   - **VLM verification mismatch**: Qwen3-VL reports false negative → correction strategy escalation
   - **Auth challenge**: Service requires unexpected 2FA → cycle pauses, prompts user via CLI, resumes
   - **Partial form submission**: Agent submits form but verification fails → log partial state, mark task failed
   - **Dead-letter promotion**: Task fails max_retries times → moved to dead_letter, visible in `just list-tasks`
   - **Ollama timeout**: Ollama takes >60s to respond → retry with exponential backoff (3 attempts)
   - **Ollama OOM**: Ollama crashes due to memory → detect, trigger MemoryGuard fallback, retry
   - **Browser reset failure**: File deletion fails → contamination detection triggers VM recreation
   - **Discovery navigation failure**: UI-TARS can't reach dashboard → empty result, no crash, logged
   - **Model not found**: Requested Ollama model not pulled → clear error message with `ollama pull` instruction

2. **Operational Retry Logic** — updates to existing services
   - `LLMService`: add retry with exponential backoff (1s, 2s, 4s) for Ollama timeouts, max 3 attempts
   - `VMManager.ensure_ready()`: add retry for VM boot (2 attempts with 30s gap)
   - `VMManager.reset_browser_state()`: add retry on file deletion failure (1 retry after 2s sleep)
   - All retries logged with attempt number and backoff duration

3. **Test Suite** — minimum 12 tests (the failure matrix IS the test suite)
   - VM boot timeout handled gracefully
   - VNC screenshot retry on stale capture
   - VLM false negative triggers correction escalation
   - Partial form submission captured in task result
   - Dead-letter promotion after max retries
   - Ollama timeout retried with backoff
   - Ollama OOM triggers MemoryGuard fallback
   - Browser reset failure triggers VM recreation
   - Discovery navigation failure returns empty result
   - Model not found produces clear error with instructions
   - All failure scenarios leave the system in a recoverable state
   - No failure scenario causes data loss in SQLite

**Acceptance Criteria:**
- [ ] All 11 failure scenarios in the test matrix pass
- [ ] No failure scenario crashes the process or leaves orphaned state
- [ ] Ollama timeouts retried with exponential backoff (3 attempts)
- [ ] VM boot retried once before aborting
- [ ] Browser reset retried once before triggering VM recreation
- [ ] All retry attempts logged with attempt number and backoff duration
- [ ] Dead-letter tasks surfaced in `just list-tasks --status dead_letter` with failure reason
- [ ] SQLite database integrity maintained across all failure scenarios
- [ ] Test suite includes minimum 12 tests covering all documented failure modes
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Failure injection**: Some failures (Ollama OOM, VM boot failure) are difficult to simulate in unit tests. **Mitigation**: Use mock objects for external dependencies; reserve real failure injection for manual testing.
- 🟢 **Retry logic**: Exponential backoff is a well-understood pattern. **Mitigation**: Use `asyncio.sleep()` with configurable base and max.
- 🟡 **Test flakiness**: Failure-mode tests may be timing-sensitive. **Mitigation**: Use generous timeouts in tests; mock time-dependent operations.

**References:** RFC §Cross-cutting Concerns (Observability — Error classification); RFC §Performance targets (retry budgets); RFC §Operational Contracts (Environment Reset Invariants)

---

### WI 7.4: [Documentation] End-to-End Test Suite & Documentation — Complete Implementation

- **Estimated Points**: 3
- **Dependencies**: WI 7.1, 7.2, 7.3 (hardened system)
- **Completed**: No

**User Story**: As a developer or new user, I want comprehensive documentation and an end-to-end test suite so that I can set up and run Soren from scratch and trust that the full lifecycle is validated.

**Description**: Write the README.md covering installation through first successful cycle, create inline documentation for all public interfaces, and build the end-to-end test suite that validates the full lifecycle. The E2E suite covers: fresh setup → configure personas → add credentials → prepare VM → run discovery → execute a task → verify playbook generated → verify memory stored → check cycle report.

**Deliverables:**

1. **README.md** — project root
   - Prerequisites: macOS (Apple Silicon), Python 3.12+, Lume, Ollama, required models
   - Installation: `uv sync`, model pulls (`ollama pull gemma3:12b`, `ollama pull qwen3-vl:8b`), `lume serve`
   - Configuration: `settings.yaml` overview, persona YAML format, credential setup
   - Quickstart: step-by-step from install to first successful cycle
   - Commands reference: all `just` commands with descriptions
   - Architecture overview: brief description with link to RFC
   - Troubleshooting: common issues (Lume not running, models not pulled, memory pressure)
   - Memory requirements: 32 GB minimum, 64 GB recommended, model budget breakdown

2. **Inline Documentation**
   - All public classes and methods have docstrings describing purpose, parameters, return values, and exceptions
   - Module-level docstrings for all service modules
   - Type annotations complete and consistent across all public interfaces

3. **End-to-End Test Suite** — `tests/test_e2e.py`
   - @pytest.mark.e2e marker for selective execution
   - Test: full cycle with discovery → backlog population → task execution → report generation
   - Test: discovery-only cycle (`just refresh-tasks`)
   - Test: retry flow — task fails → retried with backoff → succeeds on retry
   - Test: dead-letter flow — task fails max_retries → quarantined → `just retry-task` resurrects
   - Test: playbook write-back — successful task generates playbook file
   - Test: memory persistence — memory stored in cycle N is recalled in cycle N+1
   - All E2E tests require @pytest.mark.e2e and real VM + models

4. **Test Suite** — minimum 8 tests (`tests/test_e2e.py`)
   - Full cycle completes without errors
   - Discovery populates backlog
   - Task execution produces CycleReport
   - Retry with backoff works across cycles
   - Dead-letter + resurrection flow works
   - Playbook generated after successful execution
   - Memory persists across cycles
   - All `just` commands exit 0 with valid configuration

**Acceptance Criteria:**
- [ ] README.md covers: prerequisites, installation, configuration, quickstart, command reference, troubleshooting
- [ ] A new developer can follow README from clone to first successful cycle
- [ ] All public classes and methods have docstrings
- [ ] E2E test suite covers full lifecycle, retry flow, dead-letter flow, playbook write-back, and memory persistence
- [ ] `just test` runs unit tests (no external deps); `just test-e2e` runs E2E tests (requires VM + models)
- [ ] Test suite includes minimum 8 E2E tests
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **E2E test reliability**: Tests against real VM and models are inherently flaky. **Mitigation**: Generous timeouts; retry decorator on flaky assertions; separate marker for CI exclusion.
- 🟢 **Documentation scope**: Well-defined by RFC deliverables. No research required.
- 🟢 **Inline documentation**: Mechanical work with clear patterns from existing code.

**References:** RFC §Cross-cutting Concerns (Testing Strategy); RFC §Phase 7 deliverables; RFC §Appendix: Dependency Manifest

---

## Summary

| WI | Title | Points | Dependencies | Track |
|----|-------|--------|-------------|-------|
| 7.1 | Structured Logging & Rich CLI Output | 5 | Phases 1–6 | Polish |
| 7.2 | Memory Profiling & Optimization | 5 | Phase 3, Phase 5 | Performance |
| 7.3 | Failure-Mode Testing & Retry Refinement | 5 | Phases 1–6 | Hardening |
| 7.4 | Documentation & End-to-End Test Suite | 3 | 7.1, 7.2, 7.3 | Documentation |
| | **Total** | **18** | | |

### Velocity Assumptions

- **Solo developer**: 9 points/week. Velocity recovers slightly from Phase 5 because most work is testing, profiling, and documentation rather than new feature implementation.
- **Week 1 target** (10 pts): WI 7.1 (5) + WI 7.2 (5) — logging/CLI and performance profiling (parallelizable)
- **Week 2 target** (8 pts): WI 7.3 (5) + WI 7.4 (3) — failure testing and documentation

### Test Coverage Summary

| WI | Minimum Tests | Coverage Target |
|----|--------------|-----------------|
| 7.1 | 12 | 100% for `services/logging.py`, `services/config_validator.py` |
| 7.2 | 10 | 100% for `services/resource_checker.py`; benchmark script validates empirically |
| 7.3 | 12 | All 11 failure modes + 1 data integrity check |
| 7.4 | 8 | E2E coverage of full lifecycle |
| **Total** | **42** | |

### Exit Criteria Traceability

| Phase Exit Criterion | Verified By |
|---------------------|-------------|
| All `just` commands produce clean formatted output | WI 7.1 AC |
| Structured logging to `data/logs/soren.log` | WI 7.1 AC |
| Memory usage under 30 GB on 32 GB machine | WI 7.2 AC (benchmark) |
| Failure-mode test matrix passes | WI 7.3 AC |
| E2E test suite covers full lifecycle | WI 7.4 AC |
| README covers install through first cycle | WI 7.4 AC |
