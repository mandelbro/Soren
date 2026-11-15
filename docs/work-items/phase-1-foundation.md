# Phase 1: Foundation & Scaffolding — Work Items

**Phase Goal**: Project structure, data models, config loading, credential management, CLI skeleton.

**Estimated Phase Duration**: 2 weeks (Feb 24 – Mar 9, 2026)

**Total Story Points**: 26

**Exit Criteria (Phase-Level)**:
- `just validate-personas` successfully loads and validates persona YAML files
- `just list-credentials` shows encrypted credential references
- All model unit tests pass
- `just lint` and `just test` pass cleanly

---

## Dependency Graph

```
WI 1.1 (Scaffolding)
  ├── WI 1.2 (Domain Models) ──► WI 1.3 (Database Schema)
  ├── WI 1.4 (Persona Config)
  └── WI 1.5 (Settings System) ──► WI 1.6 (Credentials)
```

**Parallel tracks after WI 1.1**:
- **Track A** (data layer): WI 1.2 → WI 1.3
- **Track B** (persona system): WI 1.4
- **Track C** (config & security): WI 1.5 → WI 1.6

---

## Work Items

### WI 1.1: [Build System] Project Scaffolding & CLI Skeleton — Complete Implementation

- **Estimated Points**: 3
- **Dependencies**: None (all other WIs depend on this)
- **Completed**: Yes

**User Story**: As a developer, I want a fully scaffolded Python project with build tooling, CLI entry point, and test infrastructure, so that subsequent work items can focus on feature implementation without setup overhead.

**Description**: Bootstrap the entire Soren project from scratch. Create the directory structure per the RFC Appendix A, configure `pyproject.toml` with all dependencies pinned via `uv`, set up the `justfile` with all command stubs, and implement the Click CLI skeleton with all 8 subcommand groups. Each CLI command initially prints a "not yet implemented" message. This WI establishes the development loop: install → lint → test → run CLI.

**Deliverables:**

1. **Build Configuration**
   - `pyproject.toml` with all 14 runtime dependencies + 3 dev dependencies per RFC Appendix
   - Package configured as `soren` with `src/` layout
   - Entry point: `python -m soren.cli` and `uv run soren`
   - Python `>=3.12` requirement

2. **justfile with All Command Stubs**
   - Primary: `begin-soren-cycle`, `refresh-tasks`
   - Task management: `list-tasks`, `retry-failed`, `retry-task`, `clear-backlog`
   - VM: `prepare-vm`, `vm-status`, `vm-stop`, `vm-reset`
   - Credentials: `encrypt-credentials`, `add-credential`, `list-credentials`
   - Playbooks: `list-playbooks`, `show-playbook`
   - Personas: `validate-personas`, `list-personas`
   - History: `show-history`
   - Dev: `test`, `lint`, `format`

3. **Directory Structure**
   - Full `src/soren/` tree: `models/`, `services/`, `adapters/`, `verification/`, `patches/`
   - All `__init__.py` files
   - `config/` with placeholder `settings.yaml` and `personas/` directory
   - `data/` with subdirectories: `playbooks/`, `baselines/`, `screenshots/`, `reports/`, `logs/`
   - `tests/conftest.py` with `tmp_path`-based fixtures for isolated test databases and config dirs
   - `.gitignore` covering `.credentials.enc`, `data/`, `*.db`, `__pycache__`, `.venv/`
   - `README.md` with project overview, prerequisites, and quickstart

4. **Click CLI Skeleton**
   - Main CLI group at `src/soren/cli.py`
   - 8 subcommand groups: `cycle`, `discover`, `tasks`, `vm`, `credentials`, `playbooks`, `personas`, `history`
   - Each subcommand registered with help text describing its purpose
   - Stub implementations that print "Not yet implemented" and exit cleanly

5. **Test Suite** — minimum 8 tests
   - `uv sync` installs without errors (verified manually, documented in AC)
   - CLI entry point is invocable and returns help text
   - All 8 subcommand groups are registered and reachable
   - `--help` renders for every registered command
   - `just test` exits 0 with no collection errors
   - `just lint` exits 0

**Acceptance Criteria:**
- [ ] `uv sync` completes without dependency resolution errors
- [ ] `uv run soren --help` prints the top-level help with all 8 subcommand groups listed
- [ ] `uv run soren credentials --help` (and all other groups) prints group-specific help
- [ ] `just test` runs pytest and exits 0 (collecting tests from `tests/`)
- [ ] `just lint` runs ruff and exits 0
- [ ] All directories from the RFC project structure exist with `__init__.py` files where appropriate
- [ ] `.gitignore` excludes `.credentials.enc`, `data/soren.db`, and `data/` artifacts
- [ ] Test suite includes minimum 8 tests, 100% coverage for `cli.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **Dependency conflicts**: Low risk. All dependencies are well-established PyPI packages. `uv` lock file pins exact versions. **Mitigation**: Run `uv sync` early and resolve any conflicts before proceeding.
- 🟡 **CUA SDK availability**: `cua-computer` and `cua-agent` are newer packages. **Mitigation**: Pin to `>=0.7` per RFC; verify installation succeeds. If unavailable, add as optional dependency and gate imports.

**References:** RFC §Implementation Plan Phase 1; PRD §Appendix A (Project Structure); PRD §9 (Justfile Commands)

---

### WI 1.2: [Models] Task & Execution Domain Models — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 1.1
- **Completed**: Yes

**User Story**: As a developer, I want validated Pydantic models for every domain entity in the task lifecycle, so that all downstream services (database, backlog, reporting) share a single source of truth for data shapes and constraints.

**Description**: Implement all Pydantic v2 models that represent the core domain: tasks, task steps, cycle reports, memory entries, and their supporting types. Each model enforces field constraints (enums, ranges, nullability, defaults) and supports JSON serialization/deserialization for SQLite storage. The `Task` model includes all RFC-specified fields: `dedup_key`, `provider_task_id`, `next_eligible_at`, `last_failure_reason`, and `dead_letter` as a valid status. Includes comprehensive unit tests for validation logic and edge cases.

**Deliverables:**

1. **Type Definitions** — `src/soren/models/`
   - `task.py`: `Task`, `TaskStep`, `TaskType` enum (`survey`, `email_reply`, `form_submission`, `proposal`), `TaskStatus` enum (`queued`, `in_progress`, `completed`, `dead_letter`), `StepStatus` enum (`pending`, `in_progress`, `completed`, `failed`, `skipped`), `ActionType` enum (`click`, `type`, `scroll`, `key_press`, `screenshot`, `navigate`, `verify`)
   - `session.py`: `CycleReport`, `CycleType` enum (`execute`, `discover`, `discover_and_execute`), `BacklogSummary`, `InsertResult`, `DiscoverySummary`, `PlaybookUpdate`, `PlaybookInfo`
   - `memory.py`: `MemoryEntry`, `MemoryType` enum (`session`, `cycle`, `long_term`)
   - `models/__init__.py`: re-exports all public types

2. **Implementation Details**
   - `Task.dedup_key`: TEXT field, validated as non-empty, used for UNIQUE constraint in DB
   - `Task.provider_task_id`: Optional TEXT, nullable
   - `Task.next_eligible_at`: Optional `datetime`, nullable, ISO 8601
   - `Task.last_failure_reason`: Optional TEXT, nullable
   - `Task.status`: Includes `dead_letter` as terminal state
   - `Task.priority`: Constrained integer 0–10 via `Field(ge=0, le=10)`
   - `Task.max_retries`: Default 3
   - `MemoryEntry.importance`: Constrained float 0.0–1.0
   - `CycleReport.discovery_ran`: Boolean (maps to INTEGER in SQLite)
   - JSON-serializable fields (`result_data`, `action_data`, `value`, `personas_used`, etc.) typed as `dict | None` or `list` with Pydantic JSON serialization
   - All datetime fields use ISO 8601 string format for SQLite compatibility
   - `model_config` with `from_attributes=True` for ORM-style instantiation

3. **Test Suite** — minimum 18 tests (`tests/test_models.py`)
   - Valid `Task` construction with all fields
   - `Task` rejects invalid `priority` (negative, >10)
   - `Task` rejects invalid `status` enum value
   - `Task` defaults: `max_retries=3`, `attempt_count=0`
   - `TaskStep` rejects invalid `action_type`
   - `MemoryEntry` rejects `importance` outside 0.0–1.0
   - `CycleReport` JSON roundtrip serialization/deserialization
   - `BacklogSummary` construction with all status counts
   - `InsertResult` and `DiscoverySummary` validation
   - `PlaybookUpdate` and `PlaybookInfo` validation
   - Enum `.value` access and iteration for all enums
   - `Task` with `dead_letter` status accepted
   - `Task` with `next_eligible_at` set to future datetime
   - `Task` with `last_failure_reason` populated
   - `Task` JSON serialization preserves all nullable fields
   - `TaskStep` with `vlm_reasoning` populated
   - Edge case: `Task` with empty `dedup_key` rejected
   - Edge case: `CycleReport` with `discovery_ran=False` and `discovery_summary=None`

**Acceptance Criteria:**
- [ ] All Pydantic models instantiate with valid data and raise `ValidationError` on invalid data
- [ ] `Task` model enforces `priority` range 0–10, `status` enum including `dead_letter`, non-empty `dedup_key`
- [ ] `MemoryEntry` model enforces `importance` range 0.0–1.0
- [ ] All enum types are `str` enums with lowercase values matching the RFC schema
- [ ] JSON roundtrip: `Model.model_validate_json(instance.model_dump_json())` produces equivalent instance for every model
- [ ] `from_attributes=True` config enables dict-based construction (for database row mapping)
- [ ] Test suite includes minimum 18 tests, 100% coverage for all model files
- [ ] Build/lint/type-check pass
- [ ] `models/__init__.py` re-exports all public types

**Technical Risks:**
- 🟢 **Pydantic v2 migration patterns**: Well-documented. Use `model_config` instead of `class Config`.
- 🟢 **DateTime serialization**: Use `str` type for datetime fields (ISO 8601 strings) to avoid SQLite adapter complexity. Parse to `datetime` objects via utility functions when needed.

**References:** RFC §Data Model (ER diagram); PRD §7 (Data Models); RFC §Operational Contracts (Task Retry State Machine)

---

### WI 1.3: [Database] SQLite Schema & Migration System — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 1.1, WI 1.2 (models define the table shapes)
- **Completed**: Yes

**User Story**: As a developer, I want a version-tracked SQLite schema with all four tables and a reliable migration system, so that the database is automatically initialized and can evolve across releases without data loss.

**Description**: Implement the SQLite database layer: schema definition for all 4 tables (tasks, task_steps, memory_entries, cycle_reports), a version-tracked migration system using embedded SQL files, and a database manager that handles initialization and migration execution. The `tasks.dedup_key` column carries a UNIQUE constraint. Includes roundtrip tests that verify Pydantic models can be written to and read from the database correctly.

**Deliverables:**

1. **Schema Definition** — `src/soren/services/database.py` (or `db.py`)
   - `tasks` table: all columns per RFC ER diagram, `dedup_key TEXT UNIQUE NOT NULL`, `id TEXT PRIMARY KEY`, appropriate defaults (`max_retries DEFAULT 3`, `attempt_count DEFAULT 0`, `status DEFAULT 'queued'`)
   - `task_steps` table: `id TEXT PRIMARY KEY`, `task_id TEXT REFERENCES tasks(id)`, all columns per RFC
   - `memory_entries` table: `id TEXT PRIMARY KEY`, all columns per RFC
   - `cycle_reports` table: `cycle_id TEXT PRIMARY KEY`, `task_executed_id TEXT REFERENCES tasks(id)`, all columns per RFC
   - Indexes: `idx_tasks_status` on `tasks(status)`, `idx_tasks_persona` on `tasks(persona_id)`, `idx_tasks_dedup` on `tasks(dedup_key)`, `idx_memory_type` on `memory_entries(entry_type)`

2. **Migration System** — `src/soren/services/migrations/`
   - `migrations/` directory with numbered SQL files: `001_initial_schema.sql`
   - `schema_version` table tracking applied migrations
   - `DatabaseManager` class: `async initialize()` creates DB and runs pending migrations, `async get_connection()` returns `aiosqlite` connection, `async close()`
   - Migrations run in order, skip already-applied, record version after success
   - WAL mode enabled for concurrent read safety
   - Foreign key enforcement enabled via `PRAGMA foreign_keys = ON`

3. **Test Suite** — minimum 14 tests (`tests/test_database.py`)
   - Schema creation on fresh database
   - All 4 tables exist with correct columns
   - `dedup_key` UNIQUE constraint enforced (duplicate insert raises `IntegrityError`)
   - Foreign key constraint: `task_steps.task_id` references valid `tasks.id`
   - Foreign key constraint: `cycle_reports.task_executed_id` references valid `tasks.id`
   - Migration system runs `001_initial_schema.sql` on empty DB
   - Migration system skips already-applied migrations (idempotent)
   - `schema_version` table records applied migration with timestamp
   - Roundtrip: insert `Task` model data → query → reconstruct `Task` model
   - Roundtrip: insert `TaskStep` model data → query → reconstruct `TaskStep` model
   - Roundtrip: insert `MemoryEntry` model data → query → reconstruct
   - Roundtrip: insert `CycleReport` model data → query → reconstruct
   - `INSERT ... ON CONFLICT(dedup_key) DO NOTHING` behavior verified
   - WAL mode and foreign keys enabled after initialization

**Acceptance Criteria:**
- [ ] `DatabaseManager.initialize()` creates all 4 tables and indexes from a fresh state
- [ ] `tasks.dedup_key` UNIQUE constraint prevents duplicate inserts and `INSERT ... ON CONFLICT(dedup_key) DO NOTHING` silently skips
- [ ] Foreign key constraints are enforced (`task_steps.task_id`, `cycle_reports.task_executed_id`)
- [ ] Migration system applies SQL files in order, tracks versions, and is idempotent on re-run
- [ ] Pydantic model instances roundtrip through INSERT → SELECT with no data loss
- [ ] WAL mode is enabled (`PRAGMA journal_mode=wal`)
- [ ] All tests use `tmp_path` for isolated test databases (no shared state)
- [ ] Test suite includes minimum 14 tests, 100% coverage for database module
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **aiosqlite compatibility**: Mature library, well-tested with SQLite. No known issues.
- 🟡 **Migration ordering**: Embedded SQL migrations must be applied in strict numeric order. **Mitigation**: Sort migration files lexicographically; validate no gaps in sequence numbers during initialization.
- 🟢 **JSON column handling**: SQLite stores JSON as TEXT. Use `json.dumps()` on write, `json.loads()` on read. Pydantic handles the rest.

**References:** RFC §Data Model (ER diagram, all 4 tables); RFC §TaskDiscovery (dedup_key, `INSERT ... ON CONFLICT`); RFC §Cross-cutting Concerns (Testing Strategy)

---

### WI 1.4: [Config] Persona & TaskSource Models with YAML Loading — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 1.1 (CLI skeleton for persona commands)
- **Completed**: Yes

**User Story**: As a user, I want to define personas as YAML files with validated task source configurations, so that I can manage agent identities and their service connections through simple file editing and verify them with `just validate-personas`.

**Description**: Implement the Persona and TaskSource Pydantic models matching the RFC persona YAML schema, build a PersonaLoader that discovers, parses, and validates all persona files in `config/personas/`, and wire the `just validate-personas` and `just list-personas` commands through the CLI. TaskSource includes per-service rate-limiting fields (`typing_delay_ms`, `action_delay_ms`, `cooldown_between_tasks_s`). Ships with two example persona files (researcher, professional) that serve as both documentation and validation test fixtures.

**Deliverables:**

1. **Type Definitions** — `src/soren/models/persona.py`, `src/soren/models/task_source.py`
   - `Persona`: `name`, `id`, `demographics` (age, occupation, education, location), `traits` (Big Five: conscientiousness, openness, agreeableness, extraversion, neuroticism — each 0.0–1.0), `preferences` (response_style, formality, decision_speed), `task_sources` (list of `TaskSource`), `enabled` (default `True`)
   - `TaskSource`: `name`, `type` (survey_platform, email, generic_form), `url` (validated URL), `credential_ref`, `discovery_hint` (optional), `typing_delay_ms` (default 50), `action_delay_ms` (default 500), `cooldown_between_tasks_s` (default 30), `enabled` (default `True`)

2. **PersonaLoader** — `src/soren/services/persona_loader.py`
   - `load_persona(path: Path) -> Persona`: Parse and validate a single YAML file
   - `load_all(directory: Path) -> list[Persona]`: Discover and load all `*.yaml` files in directory
   - `validate_all(directory: Path) -> list[ValidationResult]`: Validate all personas, return per-file pass/fail with error details
   - Clear error messages: missing required fields, invalid trait ranges, malformed task sources, duplicate persona IDs across files

3. **Example Persona Files** — `config/personas/`
   - `researcher.yaml`: Per RFC example (Research Analyst with deltapanel and acmemail sources)
   - `professional.yaml`: A second persona with different traits, demographics, and task sources

4. **CLI Integration**
   - `just validate-personas` → `uv run soren personas validate`: loads all persona files, reports validation results with pass/fail per file, exits non-zero if any fail
   - `just list-personas` → `uv run soren personas list`: displays a table (via `rich`) of persona name, ID, enabled status, number of task sources, and source names

5. **Test Suite** — minimum 14 tests (`tests/test_persona_loader.py`)
   - Valid persona loads from well-formed YAML
   - Missing required field (`name`) raises `ValidationError`
   - Invalid trait value (>1.0 or <0.0) raises `ValidationError`
   - Missing `credential_ref` on task source raises `ValidationError`
   - Invalid task source `type` raises `ValidationError`
   - `typing_delay_ms` defaults to 50 when not specified
   - `load_all()` discovers and loads multiple YAML files
   - `load_all()` skips non-YAML files in directory
   - `validate_all()` returns per-file validation results
   - `validate_all()` reports errors for invalid files without crashing on valid ones
   - Duplicate persona `id` across files detected and reported
   - Persona with no task sources is valid (empty list)
   - Persona with disabled task source loads correctly
   - CLI `personas validate` exits 0 with valid fixtures, non-zero with invalid

**Acceptance Criteria:**
- [ ] `just validate-personas` exits 0 when all persona files in `config/personas/` are valid
- [ ] `just validate-personas` exits non-zero and prints specific errors when a persona file is invalid
- [ ] `just list-personas` prints a formatted table showing persona name, ID, enabled, source count, and source names
- [ ] `Persona` model validates Big Five traits are in 0.0–1.0 range
- [ ] `TaskSource` model validates `type` against allowed enum values and defaults rate-limiting fields
- [ ] `PersonaLoader.load_all()` loads both example persona files without errors
- [ ] Invalid YAML files produce actionable error messages (file name, line number if possible, field name, constraint violated)
- [ ] Test suite includes minimum 14 tests, 100% coverage for persona models and loader
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **YAML parsing edge cases**: PyYAML handles standard YAML well. **Mitigation**: Use `yaml.safe_load()` to prevent code execution. Wrap parse errors in user-friendly messages.
- 🟢 **Persona schema evolution**: The persona YAML format may change as later phases add fields. **Mitigation**: Pydantic's `model_config` with `extra='ignore'` allows forward-compatible parsing. New required fields added with defaults.

**References:** RFC §Persona YAML Example; PRD §6.2 (Persona System); RFC §Resolved Decision 7 (per-service rate limiting in TaskSource)

---

### WI 1.5: [Config] Application Settings System — Complete Implementation

- **Estimated Points**: 3
- **Dependencies**: WI 1.1
- **Completed**: Yes

**User Story**: As a developer, I want a validated settings system that parses `settings.yaml` with sensible defaults for all configuration sections, so that every component can access its configuration through a single typed interface without ad-hoc YAML parsing.

**Description**: Implement a hierarchical Pydantic settings model that maps all sections of `settings.yaml` (scheduler, backlog, playbooks, llm, vlm, verification, vm) to typed, validated Python objects. Includes the VMConfig model. The settings loader reads `config/settings.yaml`, merges with defaults, and validates all fields. Ships with a complete default `settings.yaml` template. All Phase 1-critical config keys are present: `scheduler.max_task_retries`, `scheduler.retry_backoff_minutes`, `llm.fallback_model`, `vm.recreate_every_n_cycles`, `playbooks.base_dir`, `backlog.max_age_days`, `backlog.max_tasks_per_persona`.

**Deliverables:**

1. **Type Definitions** — `src/soren/models/vm.py`, `src/soren/models/settings.py`
   - `VMConfig`: `name` (default "soren-vm"), `memory_gb` (default 8), `recreate_every_n_cycles` (default 10), plus other VM fields per RFC
   - `SchedulerConfig`: `max_task_retries` (default 3), `retry_backoff_minutes` (default `[5, 30, 120]`)
   - `BacklogConfig`: `max_age_days` (default 30), `max_tasks_per_persona` (default 100)
   - `PlaybookConfig`: `base_dir` (default "data/playbooks"), `write_back` (default `True`)
   - `LLMConfig`: `primary_model` (default "gemma3:12b"), `fallback_model` (default "gemma3:4b")
   - `VLMConfig`: `provider` (default "mlx"), `model` (default "mlx-community/UI-TARS-1.5-7B-6bit")
   - `VerificationConfig`: `provider` (default "ollama"), `model` (default "qwen3-vl:8b")
   - `SorenSettings`: Top-level model composing all section configs

2. **Settings Loader** — `src/soren/services/config_loader.py`
   - `load_settings(path: Path | None = None) -> SorenSettings`: Load from YAML, merge with defaults
   - Falls back to `config/settings.yaml` if no path provided
   - Missing file → use all defaults (with warning log)
   - Partial YAML → merge with defaults (only override what's specified)
   - Invalid values → raise with clear error message

3. **Default Settings Template** — `config/settings.yaml`
   - Complete template with all sections and defaults, matching RFC specification
   - Commented to explain each setting's purpose

4. **Test Suite** — minimum 10 tests (`tests/test_config.py`)
   - Load from valid complete YAML
   - Load from partial YAML (missing sections use defaults)
   - Load from missing file uses all defaults
   - `scheduler.retry_backoff_minutes` validates as list of positive integers
   - `vm.recreate_every_n_cycles` validates as positive integer
   - `backlog.max_age_days` validates as positive integer
   - `llm.fallback_model` defaults to "gemma3:4b"
   - Invalid YAML syntax produces clear error
   - Invalid field value (e.g., negative `max_task_retries`) rejected
   - Settings object provides typed access to all nested sections

**Acceptance Criteria:**
- [ ] `load_settings()` returns a fully populated `SorenSettings` with defaults when no YAML file exists
- [ ] `load_settings(path)` merges YAML overrides with defaults (unspecified fields keep defaults)
- [ ] All Phase 1-critical config keys are present and typed: `scheduler.max_task_retries`, `scheduler.retry_backoff_minutes`, `llm.fallback_model`, `vm.recreate_every_n_cycles`, `playbooks.base_dir`, `backlog.max_age_days`, `backlog.max_tasks_per_persona`
- [ ] `config/settings.yaml` template is valid YAML and loads without errors
- [ ] Invalid configuration values produce actionable error messages identifying the field and constraint
- [ ] Test suite includes minimum 10 tests, 100% coverage for settings models and loader
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **Config schema drift**: Settings may evolve across phases. **Mitigation**: All fields have defaults; new fields added with defaults maintain backward compatibility.
- 🟢 **YAML type coercion**: PyYAML may parse `true`/`false` as booleans, numbers as ints/floats. **Mitigation**: Pydantic validators handle type coercion; explicit types in model prevent surprises.

**References:** RFC §Settings.yaml Key Sections; PRD §9 (Configuration, settings.yaml)

---

### WI 1.6: [Security] Credential Management (IdentityManager) — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 1.1, WI 1.5 (settings provide credential file path)
- **Completed**: Yes

**User Story**: As a user, I want to securely store and manage service credentials encrypted at rest, so that I can configure login details for task sources without exposing secrets in plaintext on disk or in version control.

**Description**: Implement the `IdentityManager` service that handles Fernet symmetric encryption/decryption of a JSON credentials file. The encryption key is derived from a user-provided passphrase via PBKDF2 (sourced from the `SOREN_CREDENTIALS_KEY` environment variable or interactive prompt). Credentials are stored as a single encrypted file at `config/.credentials.enc`. Implements three CLI commands: encrypt an existing plaintext file, interactively add a single credential, and list credential references without exposing secrets. Credentials never appear in logs.

**Deliverables:**

1. **IdentityManager Service** — `src/soren/services/identity_manager.py`
   - `IdentityManager(credentials_path: Path)`: manages the encrypted credentials file
   - `derive_key(passphrase: str) -> bytes`: PBKDF2-HMAC-SHA256 key derivation with a stable salt (stored alongside the encrypted file or embedded)
   - `encrypt_file(plaintext_path: Path, passphrase: str) -> None`: Encrypt a JSON file → `.credentials.enc`
   - `decrypt(passphrase: str) -> dict[str, dict]`: Decrypt and return credential entries
   - `add_credential(ref: str, service_type: str, username: str, password: str, passphrase: str, metadata: dict | None = None) -> None`: Add or update a single credential entry, re-encrypt file
   - `list_credentials(passphrase: str) -> list[CredentialRef]`: Return reference names + service types without secrets
   - `get_credential(ref: str, passphrase: str) -> dict`: Retrieve a single credential by reference name
   - Passphrase sourcing: `SOREN_CREDENTIALS_KEY` env var → interactive prompt via `click.prompt(hide_input=True)`

2. **CredentialRef Model** — `src/soren/models/credential.py`
   - `CredentialRef`: `ref` (str), `service_type` (str), `has_username` (bool), `has_password` (bool)
   - No secret values in this model (safe for display/logging)

3. **CLI Integration**
   - `just encrypt-credentials` → `soren credentials encrypt`: prompts for passphrase, encrypts `config/credentials.json` → `config/.credentials.enc`, deletes plaintext after confirmation
   - `just add-credential <ref> <service_type>` → `soren credentials add`: prompts for username, password, passphrase; adds to encrypted file
   - `just list-credentials` → `soren credentials list`: prompts for passphrase, displays table of credential refs (name, service type) without secrets

4. **Security Measures**
   - Passphrase never stored on disk or logged
   - Decrypted credentials held in memory only for the duration of the operation
   - `structlog` configured to redact any field named `password`, `passphrase`, or `secret`
   - `.credentials.enc` file permissions set to `0600` on write

5. **Test Suite** — minimum 12 tests (`tests/test_identity_manager.py`)
   - Encrypt → decrypt roundtrip preserves all credential data
   - Decrypt with wrong passphrase raises clear error (not a generic crash)
   - `add_credential` to empty file creates new encrypted file
   - `add_credential` to existing file preserves other credentials
   - `list_credentials` returns refs without secret values
   - `get_credential` retrieves correct entry by ref
   - `get_credential` with non-existent ref raises `KeyError` or returns `None`
   - PBKDF2 key derivation produces consistent keys from same passphrase
   - PBKDF2 key derivation produces different keys from different passphrases
   - File permissions set to `0600` after write
   - Missing `SOREN_CREDENTIALS_KEY` env var triggers interactive prompt (tested via mock)
   - Credential file with no entries (empty JSON object) handled gracefully

**Acceptance Criteria:**
- [ ] `just encrypt-credentials` encrypts a plaintext JSON file and produces `config/.credentials.enc`
- [ ] `just add-credential researcher_acmemail survey_platform` prompts for username/password/passphrase, adds credential to encrypted file
- [ ] `just list-credentials` displays credential references (name, service type) in a formatted table without exposing secrets
- [ ] Decryption with incorrect passphrase produces a clear "invalid passphrase" error, not a stack trace
- [ ] Encrypted file permissions are `0600` (owner-only)
- [ ] Credentials never appear in log output (verified by checking structlog configuration)
- [ ] `SOREN_CREDENTIALS_KEY` environment variable is used when present, interactive prompt when absent
- [ ] Test suite includes minimum 12 tests, 100% coverage for `identity_manager.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **Fernet encryption**: Well-documented in the `cryptography` library. Standard pattern for symmetric encryption.
- 🟡 **Salt management for PBKDF2**: The salt must be consistent across encrypt/decrypt calls. **Mitigation**: Store the salt as the first 16 bytes of the encrypted file, or use a deterministic project-level salt. Document the choice.
- 🟡 **Interactive prompts in CLI**: `click.prompt(hide_input=True)` works in terminals but not in piped/CI contexts. **Mitigation**: Always check `SOREN_CREDENTIALS_KEY` env var first; fail with clear message if neither env var nor TTY is available.

**References:** RFC §Cross-cutting Concerns (Security — Credentials at rest); PRD §6.7 (Identity and Credential Manager); PRD §11 (Security Considerations)

---

## Summary

| WI | Title | Points | Dependencies | Track |
|----|-------|--------|-------------|-------|
| 1.1 | Project Scaffolding & CLI Skeleton | 3 | None | — |
| 1.2 | Task & Execution Domain Models | 5 | 1.1 | A (Data) |
| 1.3 | SQLite Database Schema & Migration System | 5 | 1.1, 1.2 | A (Data) |
| 1.4 | Persona & TaskSource Models with YAML Loading | 5 | 1.1 | B (Persona) |
| 1.5 | Application Settings System | 3 | 1.1 | C (Config) |
| 1.6 | Credential Management (IdentityManager) | 5 | 1.1, 1.5 | C (Config) |
| | **Total** | **26** | | |

### Velocity Assumptions

- **Solo developer, greenfield project**: 13 points/week is aggressive but achievable given no legacy code, no code review queue, and well-defined specifications.
- **Week 1 target** (16 pts): WI 1.1 (3) + WI 1.2 (5) + WI 1.4 (5) + WI 1.5 (3) — scaffolding and all models/config
- **Week 2 target** (10 pts): WI 1.3 (5) + WI 1.6 (5) — database layer and credential management

### Test Coverage Summary

| WI | Minimum Tests | Coverage Target |
|----|--------------|-----------------|
| 1.1 | 8 | 100% for `cli.py` |
| 1.2 | 18 | 100% for `models/task.py`, `models/session.py`, `models/memory.py` |
| 1.3 | 14 | 100% for `services/database.py` |
| 1.4 | 14 | 100% for `models/persona.py`, `models/task_source.py`, `services/persona_loader.py` |
| 1.5 | 10 | 100% for `models/vm.py`, `models/settings.py`, `services/config_loader.py` |
| 1.6 | 12 | 100% for `services/identity_manager.py` |
| **Total** | **76** | |

### Exit Criteria Traceability

| Phase Exit Criterion | Verified By |
|---------------------|-------------|
| `just validate-personas` loads and validates persona YAML files | WI 1.4 AC |
| `just list-credentials` shows encrypted credential references | WI 1.6 AC |
| All model unit tests pass | WI 1.2 + 1.4 + 1.5 AC |
| `just lint` passes | WI 1.1 AC |
| `just test` passes | All WIs |
