# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architectural Invariants (read this first)

Soren has a non-negotiable architectural thesis documented in `docs/rfc.md`.
Past Claude sessions have drifted toward solutions that contradict it under
symptom-fixing pressure. Before making any architectural recommendation, you
**must** hold these invariants in mind:

1. **Vision-based execution only.** UI-TARS sees screenshots; a real mouse
   and keyboard drive the VM via VNC/OS-level events. DOM automation
   (Playwright, Selenium, BrowserMCP, JS injection, accessibility APIs for
   web browsers) is explicitly rejected in RFC §Alternatives Considered #1
   on bot-detection grounds.
2. **Local-only inference.** Reasoning, action, and verification models all
   run on-device (MLX or Ollama). No cloud LLM fallback in v1. No
   Anthropic/OpenAI/Together/HuggingFace hosted endpoints for task
   execution inference.
3. **VM-isolated execution.** Target apps run inside the Lume macOS VM, not
   on the host. Moving execution to the host punctures the bot-detection
   guarantee.
4. **"Operating like a human would use the app."** Credential delivery,
   click dispatch, typing — all must go through GUI simulation, not APIs
   or synthesized events. Bot-detection services distinguish real OS events
   (`event.isTrusted === true`) from synthetic ones.

**Before proposing any change that touches execution modality, inference
location, VM boundary, or introduces a new external dependency that drives
the VM, you MUST invoke the `vision-keeper` subagent**
(`.claude/agents/vision-keeper.md`) to audit the proposal against the
current RFC. The subagent will return `ALIGNED` / `CONTRADICTS` / `AMBIGUOUS`
with specific RFC section citations. If it returns `CONTRADICTS`, either
abandon the proposal or propose an RFC update FIRST (and get user approval)
before implementing. Do not work around the RFC while leaving the RFC
unchanged.

The Vision-Keeper hook at `scripts/claude-hooks/vision-keeper-reminder.sh`
runs on every prompt submission and re-injects the load-bearing invariants.
If you ever propose something that contradicts a section the hook mentions,
you are drifting — stop, consult the subagent, and course-correct.

## Project Overview

Soren is a vision-based autonomous desktop agent framework for macOS VMs. It discovers tasks from external sources (surveys, emails, forms), executes them using UI automation inside a macOS VM (CUA SDK + UI-TARS VLM), verifies results via a separate VLM, and learns reusable playbooks from successful executions.

**Target hardware**: Apple Silicon Macs with 32 GB RAM. Memory pressure is a first-class concern throughout the codebase.

## Build & Development Commands

```bash
uv sync --extra dev          # Install all dependencies
just test                    # Run tests (excludes e2e)
just test -k "test_name"     # Run single test
just test-e2e                # Run e2e tests (requires live VM + Ollama)
just lint                    # Ruff linter
just format                  # Ruff formatter
just --list                  # All available commands
```

There is no typecheck command configured. Pyright is listed in the workspace CLAUDE.md but not in this project's justfile or dev dependencies.

**Ollama**: Soren runs a dedicated Ollama instance on **port 11435** (not the default 11434). Start with `just ollama-serve` or `OLLAMA_HOST=http://127.0.0.1:11435 ollama serve`.

## Architecture

### Execution Cycle

`SorenMetaAgent.run_cycle()` orchestrates the full lifecycle:

1. **Pre-flight** — MemoryGuard checks memory pressure (abort >85%)
2. **Assess** — Query backlog for eligible tasks
3. **Discover** (if backlog empty) — Three-phase pipeline per adapter:
   - Phase 1: UI-TARS navigates to source via CUA `ComputerAgent` until `DISCOVERY_READY` sentinel
   - Phase 2: Qwen3-VL extracts structured data from screenshots
   - Phase 3: Gemma3 structures raw text into `DiscoveredTask` objects
4. **Select** — `TaskPriorityScorer` ranks eligible tasks (weighted: reward, persona fit, success rate, age)
5. **Execute** — `TaskExecutor` drives CUA `ComputerAgent` with step budget (default 50)
6. **Verify** — Two-tier: Tier 1 OpenCV baseline comparison, Tier 2 VLM semantic check
7. **Finalize** — Record results, write-back playbook via LLM, store memory, reset browser, optionally recreate VM

### Model Pipeline (Multi-Model, Memory-Constrained)

| Role | Default Model | Where Used |
|------|---------------|------------|
| Reasoning/structuring | `gemma3:27b-cloud` (fallback: `gemma3:4b`) | LLMService — discovery phase 3, playbook write-back |
| Vision/UI automation | `mlx-community/UI-TARS-1.5-7B-6bit` | TaskExecutor, discovery phase 1 (via CUA ComputerAgent) |
| Verification/extraction | `qwen3-vl:235b-cloud` | ActionVerifier tier 2, discovery phase 2 |

`MemoryGuard` selects primary vs fallback model based on four signals: Ollama VRAM, macOS `memory_pressure`, psutil %, and vm_stat pageouts. Models ending in `-cloud` bypass pressure checks.

All Ollama calls use `keep_alive=0` for immediate model unload.

### Key Abstractions

- **Personas** (`config/personas/*.yaml`) — YAML-defined agent identities with Big Five traits, demographics, preferences, and task source bindings
- **Task Sources** (`TaskSource` model) — Where tasks come from (survey platforms, email, forms). Each has a `type` that maps to a `DiscoveryAdapter`
- **Discovery Adapters** (`src/soren/adapters/`) — Protocol-based: `navigation_prompt()`, `extraction_prompt()`, `structuring_prompt()`. Self-registering via `register_adapter()` at import time
- **Playbooks** (`data/playbooks/*.md`) — Per-service instruction docs. LLM writes back successful execution steps as reusable sections. Human-edited content outside `<!-- soren-generated -->` markers is preserved
- **Credentials** — Fernet-encrypted at rest (`config/credentials.enc`), decrypted at execution time and injected into agent context

### VM Lifecycle

- Managed via **Lume HTTP API** (`LumeClient` wraps REST at `http://localhost:7777`)
- Browser reset between tasks: osascript closes Safari windows, opens fresh one, verifies window count
- Full VM recreation: every N cycles or on contamination detection (stop → delete → clone from pristine → start)
- VNC screenshot patch applied on `connect_computer()` to work around CUA SDK limitations

### Patches (`src/soren/patches/`)

CUA SDK and MLX require runtime monkey-patches. `apply_all()` is called before any `ComputerAgent` usage. Order matters:
1. Video processor stub (before transformers import)
2. MLX generate patch (before mlx_vlm import)
3. Enter key behavior patch (configurable per-service: `enter_only` vs `escape_enter`)

### Data Flow

```
Persona YAML + TaskSource config
    → DiscoveryAdapter (navigate → extract → structure)
    → TaskBacklog (SQLite via aiosqlite)
    → PriorityScorer selects task
    → TaskExecutor (CUA ComputerAgent + PersonaContext + PlaybookService + CredentialStore)
    → ActionVerifier (baseline + VLM)
    → PlaybookService.write_back() (LLM summarizes steps)
    → MemoryService (stores execution context for recall)
    → ReportWriter (JSON to data/reports/)
```

### Database

SQLite via `aiosqlite` at `data/soren.db`. Managed by `DatabaseManager`, accessed through `TaskBacklog` for all task CRUD and lifecycle operations.

## Testing

- `asyncio_mode = "auto"` — all async tests run automatically
- Markers: `@pytest.mark.integration` (live VM/models), `@pytest.mark.e2e` (full pipeline)
- `just test` excludes e2e by default (`-m "not e2e"`)
- Shared fixtures in `tests/conftest.py`: `tmp_data_dir`, `tmp_config_dir`

## Configuration

- `config/settings.yaml` — Primary config (scheduler, LLM models, VM settings, playbook paths)
- `config/personas/*.yaml` — Agent persona definitions
- `.env` — Ollama connection (port 11435), server tuning (OLLAMA_MAX_LOADED_MODELS=1, OLLAMA_KEEP_ALIVE=0)
- Loaded via `SorenSettings` Pydantic model (composed of `VMConfig`, `LLMConfig`, `VLMConfig`, `SchedulerConfig`, `VerificationConfig`, `PlaybooksConfig`)

## CLI Entry Point

`soren.cli:cli` — Click-based with groups: `cycle`, `discover`, `tasks`, `vm`, `credentials`, `playbooks`, `personas`, `resources`, `history`, `uat`. All async operations wrapped in `asyncio.run()`.
