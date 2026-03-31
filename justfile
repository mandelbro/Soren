# Soren Desktop Agent Framework
set dotenv-load

# Default: run all quality gates (format, lint, typecheck, test)
default: format lint typecheck test

# Install all dependencies and pre-commit hooks
init:
    uv sync --extra dev
    uv run pre-commit install

# Install/sync — uv natively installs the project from pyproject.toml [build-system].
# Do NOT add `uv pip install -e .` here; it conflicts with `uv run`'s implicit sync
# and causes intermittent ModuleNotFoundError when asdf shims are present.
install:
    uv sync --extra dev

# Primary commands
begin-soren-cycle *ARGS:
    uv run soren cycle run {{ARGS}}

refresh-tasks *ARGS:
    uv run soren discover refresh {{ARGS}}

# Task management
list-tasks *ARGS:
    uv run soren tasks list {{ARGS}}

retry-failed *ARGS:
    uv run soren tasks retry-failed {{ARGS}}

retry-task ID:
    uv run soren tasks retry {{ID}}

clear-backlog *ARGS:
    uv run soren tasks clear {{ARGS}}

# VM management
prepare-vm *ARGS:
    uv run soren vm prepare {{ARGS}}

vm-status:
    uv run soren vm status

vm-stop:
    uv run soren vm stop

vm-reset:
    uv run soren vm reset

# Credential management
encrypt-credentials:
    uv run soren credentials encrypt

add-credential REF SERVICE_TYPE:
    uv run soren credentials add {{REF}} {{SERVICE_TYPE}}

list-credentials:
    uv run soren credentials list

# Playbook management
list-playbooks:
    uv run soren playbooks list

show-playbook SERVICE:
    uv run soren playbooks show {{SERVICE}}

# Persona management
validate-personas:
    uv run soren personas validate

list-personas:
    uv run soren personas list

# History
show-history *ARGS:
    uv run soren history show {{ARGS}}

# Development (use python -m pytest so venv's Python runs tests, not asdf shim)
test *ARGS:
    uv run python -m pytest {{ARGS}} -m "not e2e"

test-e2e *ARGS:
    uv run python -m pytest {{ARGS}} -m e2e

# Run tests with coverage
coverage:
    uv run python -m pytest -m "not e2e" --cov=soren --cov-report=term-missing --cov-fail-under=60

# Lint and auto-fix (python -m bypasses asdf shim conflicts)
lint:
    uv run python -m ruff check --fix src/ tests/

# Check formatting without modifying files
format-check:
    uv run python -m ruff format --check src/ tests/

# Format code
format:
    uv run python -m ruff format src/ tests/

# Type check
typecheck:
    uv run python -m pyright

# Run all quality gates (same as default)
all: default

# Clean build artifacts
clean:
    rm -rf .pytest_cache .ruff_cache .pyright __pycache__ dist build *.egg-info
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Ollama — Soren uses a DEDICATED instance on port 11435 (separate from default 11434)
# The OLLAMA_HOST env var is loaded from .env via dotenv-load and also set explicitly
# below so the server binds to the correct port regardless of shell env state.
ollama-serve:
    OLLAMA_HOST=http://127.0.0.1:11435 ollama serve

# Resource validation (32 GB memory safeguard)
check-resources:
    uv run soren resources check

# Validation (POC scripts)
validate-browser-reset *ARGS:
    uv run python scripts/validate_browser_reset.py {{ARGS}}

validate-memory-budget *ARGS:
    uv run python scripts/validate_memory_budget.py {{ARGS}}
