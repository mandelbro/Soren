# Soren Desktop Agent Framework
set dotenv-load

# iCloud Drive evicts venv files to cloud storage, breaking editable installs.
# The .nosync suffix tells macOS to keep the directory local at all times.
# A .venv symlink is created by `just init` for IDE/pyright compatibility.
export UV_PROJECT_ENVIRONMENT := ".venv.nosync"

# Default: run all quality gates (format, lint, typecheck, test)
default: format lint typecheck test

# Install all dependencies and pre-commit hooks
init:
    @# Ensure UV_PROJECT_ENVIRONMENT is in .env so `uv run` (outside just) works.
    @# iCloud Drive re-materializes .venv as a directory after deletion, so the
    @# symlink trick is unreliable. The env var is the only robust solution.
    @grep -q '^UV_PROJECT_ENVIRONMENT=' .env 2>/dev/null || echo 'UV_PROJECT_ENVIRONMENT=.venv.nosync' >> .env
    uv sync --extra dev
    @# Attempt symlink for IDE compatibility (best-effort on iCloud).
    @if [ -d .venv ] && [ ! -L .venv ]; then rm -rf .venv 2>/dev/null; fi
    @[ -L .venv ] || [ ! -d .venv.nosync ] || ln -sf .venv.nosync .venv 2>/dev/null || true
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

# Recover from stale virtualization locks and start VM.
# Uses lsof -t (PIDs only): macOS lsof truncates COMMAND to "com.apple", so filtering on
# "Virtualization.VirtualMachine" never matched and stale helpers kept nvram.bin open
# ("Failed to lock auxiliary storage" from VZMacAuxiliaryStorage).
# Usage: just vm-recover [VM_NAME] (default: soren-vm)
vm-recover VM='soren-vm':
    @echo "Recovering VM '{{VM}}' from stale locks..."
    @lume stop {{VM}} --force >/dev/null 2>&1 || true
    @pids=$(lsof -t -nP +D "$HOME/.lume/{{VM}}" 2>/dev/null | sort -u); \
      if [ -n "$pids" ]; then \
        echo "Found process(es) holding VM files: $pids"; \
        kill -TERM $pids >/dev/null 2>&1 || true; \
        sleep 1; \
        alive=$(ps -o pid= -p $pids 2>/dev/null | awk '{print $1}'); \
        if [ -n "$alive" ]; then \
          echo "Force killing remaining process(es): $alive"; \
          kill -KILL $alive >/dev/null 2>&1 || true; \
        fi; \
      fi
    @sleep 2
    @echo "Starting VM '{{VM}}' in the background (VNC stays enabled)..."
    lume run {{VM}} --detach --display none

# Credential management
encrypt-credentials:
    uv run soren credentials encrypt

add-credential REF SERVICE_TYPE:
    uv run soren credentials add {{REF}} {{SERVICE_TYPE}}

list-credentials:
    uv run soren credentials list

# Training
# PYTHONWARNINGS suppresses the chronic Sentry/OTel "not installed" UserWarnings
# from CUA SDK callback modules that drown the training log on every step.
train *ARGS:
    PYTHONWARNINGS=ignore::UserWarning uv run soren train run {{ARGS}}

train-list *ARGS:
    uv run soren train list {{ARGS}}

train-show SOURCE *ARGS:
    uv run soren train show {{SOURCE}} {{ARGS}}

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
    env -u OLLAMA_MAX_LOADED_MODELS -u OLLAMA_KEEP_ALIVE OLLAMA_HOST=http://127.0.0.1:11435 ollama serve

# Resource validation (32 GB memory safeguard)
check-resources:
    uv run soren resources check

# Validation (POC scripts)
validate-browser-reset *ARGS:
    uv run python scripts/validate_browser_reset.py {{ARGS}}

validate-memory-budget *ARGS:
    uv run python scripts/validate_memory_budget.py {{ARGS}}
