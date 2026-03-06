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
| `just validate-personas` | Validate all persona YAML files |
| `just show-history` | Show recent cycle history |
| `just test` | Run the test suite |
| `just lint` | Run ruff linter |
| `just format` | Run ruff formatter |

Run `just --list` for the complete list.

## Architecture

Soren operates on a cycle-based execution model:

1. **Discover** — Scan configured task sources (social media, email, etc.) and populate a prioritized backlog
2. **Execute** — Pick the highest-priority task, launch a macOS VM, and execute it using vision-based UI automation (CUA SDK + UI-TARS)
3. **Verify** — Use a separate VLM to confirm the task was completed successfully
4. **Finalize** — Record results, clean up VM state, and prepare for the next cycle

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
