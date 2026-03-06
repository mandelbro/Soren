# Soren Desktop Agent Framework
set dotenv-load

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

lint:
    uv run ruff check src/ tests/

format:
    uv run ruff format src/ tests/

# Ollama — Soren uses port 11435 (separate from default 11434)
# Requires .env with OLLAMA_HOST; just loads it via dotenv-load
ollama-serve:
    ollama serve

# Resource validation (32 GB memory safeguard)
check-resources:
    uv run soren resources check

# Validation (POC scripts)
validate-browser-reset *ARGS:
    uv run python scripts/validate_browser_reset.py {{ARGS}}

validate-memory-budget *ARGS:
    uv run python scripts/validate_memory_budget.py {{ARGS}}
