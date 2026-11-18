# Soren Desktop Agent Framework

Vision-based autonomous task execution for macOS virtual machines.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager
- **macOS with Apple Silicon** (M1/M2/M3/M4)
- **[Lume](https://github.com/trycua/lume)** — macOS VM management
- **[Ollama](https://ollama.com)** — local LLM inference

## Quickstart

```bash
# Install dependencies (include dev for tests/lint)
uv sync --extra dev

# Run tests
just test

# Lint
just lint

# See all commands
just --list
```

## Available Commands

All commands are defined in the `justfile`. Key operations:

| Command | Description |
|---|---|
| `just begin-soren-cycle` | Run a full discover → execute → finalize cycle |
| `just refresh-tasks` | Scan task sources and populate the backlog |
| `just list-tasks` | List tasks in the backlog |
| `just prepare-vm` | Create and configure the pristine VM image |
| `just vm-status` | Show VM status |
| `just encrypt-credentials` | Encrypt a plaintext credentials file |
| `just list-playbooks` | List all service playbooks |
| `just list-services` | List service definitions (`config/services/`) and whether each is valid |
| `just show-service NAME` | Show a service's effective config and where each value comes from (`--persona ID`, `--prompt`) |
| `just validate-services` | Validate service files and persona `service:` references; list deprecated settings |
| `just migrate-services` | Create service files from inline persona values (`--dry-run` to preview) |
| `just validate-personas` | Validate all persona YAML files |
| `just show-history` | Show recent cycle history |
| `just test` | Run the test suite |
| `just lint` | Run ruff linter |
| `just format` | Run ruff formatter |

Run `just --list` for the complete list.

## Privacy: third-party telemetry

The CUA SDK that Soren uses to drive the VM sends anonymized usage telemetry
by default. Soren turns it off (`CUA_TELEMETRY=off`) unless you opt in by
setting `CUA_TELEMETRY=on` in your environment or `.env`.

## Architecture

Soren operates on a cycle-based execution model:

1. **Discover** — Scan configured task sources (social media, email, etc.) and populate a prioritized backlog
2. **Execute** — Pick the highest-priority task, launch a macOS VM, and execute it using vision-based UI automation (CUA SDK + UI-TARS)
3. **Verify** — Use a separate VLM to confirm the task was completed successfully
4. **Finalize** — Record results, clean up VM state, and prepare for the next cycle

## Service Configuration

Each website or desktop app an agent uses can have a definition in
`config/services/<name>.yaml`. It holds the service's URL, how much window
chrome sits above the usable area (`chrome_height_px`, which replaces
exporting `SOREN_CHROME_CROP_Y`), keyboard shortcuts the agent may use,
tactical instructions, notes, Enter-key behavior, step budget, and pacing.
Start from the example:

```bash
cp config/services/acmepanel.yaml config/services/<name>.yaml
just validate-services
just show-service <name> --prompt   # effective values + what the agent will be told
```

During `just train <source>`, you can also teach Soren about the service
itself: `/shortcut w Week view | no text field is focused`, `/instruction …`,
`/note …`, `/crop 100` (applies immediately), and `/service` (review). At
`/done` you confirm the changes before Soren writes them to the service file.
The previous version is backed up to `config/services/.backups/` (newest 3
kept).

### Migrating existing personas

Personas that keep `url`/`type`/`discovery_hint`/pacing inline keep working. To
move that knowledge into shared service files:

```bash
just migrate-services --dry-run   # preview; never edits personas or existing service files
just migrate-services             # create config/services/<name>.yaml
just validate-services            # lists deprecated/redundant settings (never fails on them)
```

Personas that disagree on a value are reported and not migrated. Inline
persona values keep taking precedence, so behavior does not change. Remove
them from the persona once `just show-service <name>` looks right. The
top-level `enter_key_behavior` in `settings.yaml` is deprecated in favor of
`service_defaults.enter_key_behavior`. `SOREN_CHROME_CROP_Y` remains a
debug-only override of every service's `chrome_height_px`.

Personas reference a service with `task_sources: [{service: <name>, credential_ref: ...}]`.
Existing personas with inline `url`/`type` keep working unchanged.
`settings.yaml` can set defaults for every service (`service_defaults:`) and
per-service overrides (`services:`). Files in `config/services/` other than
the example are local and gitignored. The precedence rules are in
[ADR-0002](docs/adr/0002-service-configuration.md).

For full technical details, see:
- [RFC](docs/rfc.md) — Technical specification and operational contracts
- [PRD](docs/prd.md) — Product requirements and phased delivery plan

## Project Structure

```
src/soren/          # Main package (src layout)
  cli.py            # Click CLI entry point
  models/           # Pydantic domain models
  services/         # Core business logic
  adapters/         # External service integrations
  verification/     # Task verification logic
  patches/          # Runtime patches and workarounds
config/             # Configuration files
  settings.yaml     # Primary settings
  personas/         # Agent persona definitions
  services/         # Per-service definitions (ADR-0002)
data/               # Runtime data (gitignored)
  playbooks/        # Service interaction playbooks
  baselines/        # Screenshot baselines
  screenshots/      # Captured screenshots
  reports/          # Execution reports
  logs/             # Structured logs
scripts/            # POC validation scripts
tests/              # Test suite
docs/               # Documentation
```

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests with coverage
just test --cov

# Format and lint
just format
just lint
```
