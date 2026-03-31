## Summary (tasks-1.md)

- **Tasks in this file**: 5
- **Task IDs**: 001 - 005
- **Total Points**: 11

### Phase 5: Adapter Prompt Enhancement (2 pts) + Phase 1a: Playbook Data Models + Config (9 pts)

Phase 5 has no dependencies and can be implemented in parallel with Phase 1. Phase 1a builds the Pydantic data models for all three playbook tiers, schema versioning infrastructure, and configuration extensions.

---

## Tasks

### Task ID: 001

- **Title**: Wire discovery_hint into adapter navigation prompts
- **File**: src/soren/adapters/survey_platform.py
- **Complete**: [x]
- **Sprint Points**: 2

- **User Story (business-facing)**: As a Soren operator, I want the discovery_hint field from my persona YAML to be injected into adapter navigation prompts, so that the CUA agent receives site-specific guidance even before playbooks exist.
- **Outcome (what this delivers)**: The existing but unused `discovery_hint` field on `TaskSource` is wired into all three adapter navigation prompts, providing immediate improvement to first-contact navigation success.

#### Prompt:

```markdown
**Objective:** Wire the `discovery_hint` field from `TaskSource` into the `navigation_prompt()` method of all three discovery adapters. This field exists on the model (see `src/soren/models/task_source.py` line 23) but is never referenced in adapter prompts -- confirmed by the discovery document Section 2 and the technical review (M2).

**Files to Modify:**
- `src/soren/adapters/survey_platform.py` -- primary
- `src/soren/adapters/email_adapter.py`
- `src/soren/adapters/generic_form.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Read all three adapter files and `src/soren/models/task_source.py` to understand the current prompt structure
3. Read existing tests in `tests/` for adapter coverage patterns

**Detailed Instructions:**
1. In each adapter's `navigation_prompt()` method, check if `source.discovery_hint` is truthy
2. If present, inject it as a dedicated section between the LOGIN and DASHBOARD/INBOX/FORM steps:
   ```
   SITE HINT: {source.discovery_hint}
   Use the above hint to guide your navigation on this specific site.
   ```
3. The hint should appear after cookie banner dismissal but before the main navigation goal
4. Do not change the prompt structure when `discovery_hint` is None or empty
5. Write tests in `tests/adapters/test_discovery_hint_injection.py`:
   - Test each adapter produces prompt WITHOUT hint section when `discovery_hint` is None
   - Test each adapter produces prompt WITH hint section when `discovery_hint` is set
   - Verify hint text appears verbatim in the output

**Acceptance Criteria:**
- [ ] All three adapters inject `discovery_hint` when present on the TaskSource
- [ ] Prompts remain unchanged when `discovery_hint` is None or empty string
- [ ] Tests pass for all three adapters with and without hints
- [ ] `just all` passes (lint, typecheck, test)

**Session Awareness Instructions:**
1. Update session context if you discover patterns in how adapters construct prompts
2. Search for any other unreferenced fields on TaskSource that might be relevant
3. At session end, record the adapter prompt structure pattern for future reference
```

---

### Task ID: 002

- **Title**: Create SitePlaybook Pydantic models with SCHEMA_VERSION
- **File**: src/soren/models/site_playbook.py
- **Complete**: [ ]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren developer, I want well-typed Pydantic models for site playbooks so that playbook YAML files are validated on load and the data structure is documented in code.
- **Outcome (what this delivers)**: A complete set of Pydantic v2 models representing the site playbook schema defined in discovery document Section 6.3, including PlaybookStep, FlowPlaybook, LoginFlow, DiscoveryFlow, ExecutionFlow, SiteFlows, and SitePlaybook, with a SCHEMA_VERSION constant for future migration support.

#### Prompt:

```markdown
**Objective:** Create the Pydantic v2 models for the site playbook data structure as specified in the discovery document Section 6.3. These models represent per-source playbooks with three flow types (login, discovery, execution).

**File to Create:** `src/soren/models/site_playbook.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Read the discovery document Section 6.3 for the complete model specification
3. Review existing Pydantic models in `src/soren/models/` for project conventions (Field usage, model_config, etc.)
4. Use context7 for Pydantic v2 BaseModel and Field documentation if needed

**Detailed Instructions:**
1. Define `SCHEMA_VERSION = 1` as a module-level constant
2. Create the following models per Section 6.3:

   - `PlaybookStep(BaseModel)`:
     - `seq: int` -- step sequence number
     - `instruction: str` -- imperative instruction text
     - `expected_after: str` -- description of expected state after step
     - `field: str | None = None` -- credential placeholder (e.g., "credential:username")
     - `screenshot_ref: str | None = None` -- reference to training screenshot
     - `triggers_new_tab: bool = False` -- signals upcoming tab transition
     - `host_domain: str | None = None` -- expected host domain for new tab
     - `returns_to_platform: bool = False` -- signals return to original tab

   - `FlowPlaybook(BaseModel)`:
     - `verified_at: datetime | None = None`
     - `verified_by: str | None = None` -- "auto" or "human"
     - `confidence: float = Field(default=0.0, ge=0.0, le=1.0)`
     - `consecutive_successes: int = 0`
     - `max_steps: int = Field(default=20, ge=1)`
     - `steps: list[PlaybookStep]`

   - `LoginFlow(FlowPlaybook)`:
     - `auth_complete_indicator: str = ""`
     - `fallback_on_failure: Literal["stop_and_report", "generic"] = "stop_and_report"`

   - `DiscoveryFlow(FlowPlaybook)`:
     - `discovery_ready_indicator: str = ""`

   - `ExecutionFlow(FlowPlaybook)`:
     - `completion_indicator: str = ""`

   - `SiteFlows(BaseModel)`:
     - `login: LoginFlow | None = None`
     - `discovery: DiscoveryFlow | None = None`
     - `execution: dict[str, ExecutionFlow] | None = None` -- keyed by task_type

   - `SitePlaybook(BaseModel)`:
     - `source_name: str`
     - `source_url: str`
     - `created_at: datetime = Field(default_factory=...)` -- UTC now
     - `version: int = SCHEMA_VERSION`
     - `flows: SiteFlows`

3. Add `model_config = {"from_attributes": True}` where appropriate for YAML loading compatibility
4. Use `from __future__ import annotations` for forward reference support

**Test File:** `tests/models/test_site_playbook.py`
- Test round-trip: construct each model, serialize to dict, validate from dict
- Test SitePlaybook with all three flow types populated
- Test SitePlaybook with minimal data (only login flow)
- Test PlaybookStep with credential field placeholder
- Test FlowPlaybook confidence bounds (reject <0.0 and >1.0)
- Test version defaults to SCHEMA_VERSION
- Test execution flow keyed by task_type ("survey", "email_reply")

**Acceptance Criteria:**
- [ ] All models defined per Section 6.3 specification
- [ ] SCHEMA_VERSION = 1 constant exported
- [ ] Models validate correctly from YAML-like dict input
- [ ] Confidence field enforces 0.0-1.0 bounds
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the model structure and field names for use in subsequent tasks
2. Note any Pydantic v2 patterns specific to this project (model_config, Field usage)
3. Search for datetime handling patterns in existing models
```

---

### Task ID: 003

- **Title**: Create schema versioning infrastructure for playbook YAML files
- **File**: src/soren/models/site_playbook.py
- **Complete**: [ ]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren operator, I want my playbook YAML files to be automatically migrated when Soren updates its schema, so that learned playbook knowledge is never lost due to format changes.
- **Outcome (what this delivers)**: A migration registry, version validation on load, and auto-migration capability as specified in discovery document Section 17.2. Includes credential placeholder validation (S5) and step sequence contiguity checks.

#### Prompt:

```markdown
**Objective:** Add schema versioning infrastructure to the site playbook models. This implements the design from discovery document Section 17.2 including the migration registry, version validation, auto-migrate on load, and content validation beyond schema structure.

**File to Modify:** `src/soren/models/site_playbook.py` (created in Task 002)

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Task 002 (SitePlaybook models must exist)
3. Read discovery document Section 17.2 for full versioning specification
4. Read discovery document Section 16 technical review items S5 (credential validation)

**Detailed Instructions:**
1. Add exception classes:
   - `PlaybookVersionError(Exception)` -- version mismatch or missing migration
   - `PlaybookValidationError(Exception)` -- content validation failure (include file path + field)

2. Add the migration registry per Section 17.2:
   ```python
   _MIGRATIONS: dict[tuple[int, int], Callable[[dict], dict]] = {}

   def _register_migration(from_v: int, to_v: int) -> Callable:
       """Decorator to register a migration function."""
       ...

   def _migrate(raw: dict, from_version: int, to_version: int) -> dict:
       """Apply sequential migrations from from_version to to_version."""
       ...
   ```

3. Add content validation functions:
   - `validate_credential_placeholders(raw: dict) -> list[str]` -- check that credential placeholders (strings matching `credential:*`) only appear in `field` attributes, never in `instruction` or `expected_after`. Return list of violation descriptions.
   - `validate_step_sequences(raw: dict) -> list[str]` -- check that `seq` values within each flow's steps are contiguous (no gaps). Return list of issues.

4. Add the load-and-validate function:
   ```python
   def load_and_validate(path: Path) -> SitePlaybook:
       """Load YAML, check version, migrate if needed, validate content, return model."""
   ```
   - File version > SCHEMA_VERSION: raise PlaybookVersionError
   - File version < SCHEMA_VERSION: auto-migrate, write back to disk
   - Run credential placeholder and step sequence validation
   - Return validated SitePlaybook

**Test File:** `tests/models/test_site_playbook_versioning.py`
- Test load_and_validate with current version (happy path)
- Test load_and_validate with future version raises PlaybookVersionError
- Test load_and_validate with old version triggers migration (mock a v0->v1 migration for testing)
- Test credential placeholder in `instruction` field raises PlaybookValidationError
- Test credential placeholder in `field` attribute is accepted
- Test non-contiguous step sequences raise PlaybookValidationError
- Test migration writes updated file back to disk (use tmp_path fixture)

**Acceptance Criteria:**
- [ ] PlaybookVersionError and PlaybookValidationError defined
- [ ] Migration registry with _register_migration decorator works
- [ ] _migrate applies sequential migrations correctly
- [ ] load_and_validate handles version > current, version < current, and version == current
- [ ] Credential placeholders validated per S5
- [ ] Step sequence contiguity validated
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the versioning pattern for reuse if host/base playbooks need similar treatment
2. Note the tmp_path fixture usage pattern for file-based tests
```

---

### Task ID: 004

- **Title**: Create HostPlaybook and BasePlaybook Pydantic models
- **File**: src/soren/models/host_playbook.py
- **Complete**: [ ]
- **Sprint Points**: 2

- **User Story (business-facing)**: As a Soren developer, I want typed models for host-level and base-level playbooks so that the cascading resolution chain can load and validate all three playbook tiers.
- **Outcome (what this delivers)**: Pydantic models for HostPlaybook (host-specific navigation patterns) and BasePlaybook (generic task-type patterns), completing the three-tier model hierarchy described in discovery document Section 4.

#### Prompt:

```markdown
**Objective:** Create Pydantic v2 models for the host and base tiers of the playbook hierarchy as described in discovery document Section 4.

**File to Create:** `src/soren/models/host_playbook.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Read discovery document Section 4 (Cascading Playbook Architecture) for model specifications
3. Review `data/playbooks/base/survey.yaml` for the base playbook structure already in use
4. Review Task 002's SitePlaybook models for consistency

**Detailed Instructions:**
1. Create `HostPlaybook(BaseModel)`:
   - `host_domain: str` -- e.g., "qualtrics.com"
   - `url_patterns: list[str] = []` -- e.g., ["qualtrics.com/jfe/form/*"]
   - `version: int = 1`
   - `learned_from: Literal["auto", "human"] = "auto"`
   - `navigation: dict[str, Any] = {}` -- host-specific navigation overrides
   - `quirks: list[str] = []` -- host-specific quirks (e.g., "animation delay after each page")
   - `verified_at: datetime | None = None`
   - `confidence: float = Field(default=0.0, ge=0.0, le=1.0)`

2. Create `BasePlaybook(BaseModel)`:
   - `task_type: str` -- e.g., "survey", "email_reply", "form"
   - `version: int = 1`
   - `patterns: dict[str, Any] = {}` -- nested pattern structure (navigation, question_types, completion, etc.)
   This model mirrors the structure of `data/playbooks/base/survey.yaml`.

3. Add a `matches_url(url: str) -> bool` method to `HostPlaybook` that checks if a URL matches any of the `url_patterns` using fnmatch-style glob matching.

**Test File:** `tests/models/test_host_playbook.py`
- Test HostPlaybook construction with all fields
- Test HostPlaybook with minimal fields (just host_domain)
- Test matches_url with matching and non-matching URLs
- Test BasePlaybook construction from survey.yaml-like data
- Test BasePlaybook with empty patterns
- Test confidence bounds on HostPlaybook

**Acceptance Criteria:**
- [ ] HostPlaybook and BasePlaybook models defined
- [ ] HostPlaybook.matches_url() works with glob patterns
- [ ] Models load from dict structures matching existing YAML format
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the relationship between these models and the cascade resolution logic (Task 007)
2. Note the existing survey.yaml structure for BasePlaybook compatibility
```

---

### Task ID: 005

- **Title**: Extend PlaybooksConfig with tier directories and cascade settings
- **File**: src/soren/models/playbooks.py
- **Complete**: [ ]
- **Sprint Points**: 1

- **User Story (business-facing)**: As a Soren operator, I want to configure playbook directory locations and cascade behavior in my settings.yaml, so that the playbook system is configurable without code changes.
- **Outcome (what this delivers)**: Extended PlaybooksConfig model with fields for base, host, and site playbook directories plus cascade control flags. Backward compatible with existing configurations.

#### Prompt:

```markdown
**Objective:** Extend the existing `PlaybooksConfig` model with new fields to support the three-tier playbook directory structure and cascade behavior settings.

**File to Modify:** `src/soren/models/playbooks.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Read the current `src/soren/models/playbooks.py` (11 lines, very simple)
3. Read discovery document Section 4 for the tier directory structure
4. Check `src/soren/services/config_loader.py` for how PlaybooksConfig is loaded

**Detailed Instructions:**
1. Add the following fields to `PlaybooksConfig`:
   - `site_playbooks_dir: str = Field(default="data/playbooks/site", description="Directory for per-source site playbooks")`
   - `host_playbooks_dir: str = Field(default="data/playbooks/host", description="Directory for host-specific playbooks")`
   - `base_playbooks_dir: str = Field(default="data/playbooks/base", description="Directory for built-in base playbooks")`
   - `cascade_enabled: bool = Field(default=True, description="Enable cascading resolution (site > host > base)")`
   - `auto_learn_host: bool = Field(default=True, description="Auto-generate host playbooks from successful runs")`
2. Preserve existing fields (`base_dir`, `write_back`) for backward compatibility
3. Ensure default values match the directory structure already in `data/playbooks/`

**Test File:** `tests/models/test_playbooks_config.py`
- Test default values match expected directory paths
- Test existing fields (base_dir, write_back) unchanged
- Test PlaybooksConfig can be constructed with no arguments (all defaults)
- Test PlaybooksConfig can override tier directories

**Acceptance Criteria:**
- [ ] New fields added with sensible defaults
- [ ] Existing fields preserved (backward compatible)
- [ ] Default directories match existing `data/playbooks/` structure
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Verify that config_loader.py does not need changes for these new fields (Pydantic handles new optional fields)
2. Record the directory structure convention for use in PlaybookService (Task 006)
```
