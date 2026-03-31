## Summary (tasks-2.md)

- **Tasks in this file**: 5
- **Task IDs**: 006 - 010
- **Total Points**: 17

### Phase 1b: PlaybookService Evolution + Integration (14 pts) + Phase 2 Start: StuckDetector (3 pts)

Phase 1b evolves the existing PlaybookService with YAML CRUD, cascading resolution, base playbook authoring, and wires the cascade into TaskDiscovery, PersonaContext, and TaskExecutor. The final task begins Phase 2 with StuckDetector.

---

## Tasks

### Task ID: 006

- **Title**: Evolve PlaybookService with YAML site playbook CRUD methods
- **File**: src/soren/services/playbook_service.py
- **Complete**: [x]
- **Sprint Points**: 5

- **User Story (business-facing)**: As a Soren developer, I want the PlaybookService to support loading, saving, listing, and deleting structured YAML site playbooks, so that the training session and autonomous learner can persist and retrieve playbook data.
- **Outcome (what this delivers)**: The existing PlaybookService is evolved with YAML-based CRUD methods (load_site, save_site, save_flow, list_site_playbooks, delete_site) alongside the existing markdown methods. Legacy methods are marked deprecated per the migration path in discovery document Section 17.1.

#### Prompt:

```markdown
**Objective:** Evolve the existing `PlaybookService` to support structured YAML site playbooks. This implements the CRUD portion of the unified service described in discovery document Sections 6.4 and 17.1. The existing markdown methods (`load()`, `get_relevant_sections()`, `write_back()`) remain functional but are marked deprecated.

**File to Modify:** `src/soren/services/playbook_service.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 002, 003, 004, 005
3. Read the current `src/soren/services/playbook_service.py` thoroughly (243 lines)
4. Read discovery document Sections 6.4 and 17.1 for the unified service interface
5. Use context7 for PyYAML documentation if needed

**Detailed Instructions:**
1. Add imports for the new models:
   - `SitePlaybook`, `FlowPlaybook`, `load_and_validate` from `soren.models.site_playbook`
   - `HostPlaybook`, `BasePlaybook` from `soren.models.host_playbook`
   - `yaml` (PyYAML -- verify it is in project dependencies; add if missing via `uv add pyyaml`)

2. Extend `__init__` to set up tier directories from `PlaybooksConfig`:
   - `self._site_dir = Path(settings.playbooks.site_playbooks_dir)`
   - `self._host_dir = Path(settings.playbooks.host_playbooks_dir)`
   - `self._base_dir_path = Path(settings.playbooks.base_playbooks_dir)`
   - Create directories if they don't exist (`mkdir(parents=True, exist_ok=True)`)

3. Add YAML site playbook CRUD methods:
   - `load_site(self, source_name: str) -> SitePlaybook | None` -- load from `site/<source>.yaml` using `load_and_validate()`
   - `save_site(self, playbook: SitePlaybook) -> Path` -- serialize to YAML, write to `site/<source>.yaml`
   - `save_flow(self, source_name: str, flow_type: str, flow: FlowPlaybook, task_type: str | None = None) -> Path` -- load existing site playbook (or create new), update the specific flow, save
   - `list_site_playbooks(self) -> list[SitePlaybook]` -- enumerate and load all site playbook YAML files
   - `delete_site(self, source_name: str) -> bool` -- delete site playbook file, return True if existed

4. Add host playbook methods:
   - `load_host(self, host_domain: str) -> HostPlaybook | None` -- load from `host/<domain>.yaml`
   - `save_host(self, playbook: HostPlaybook) -> Path` -- serialize to YAML

5. Add base playbook method:
   - `load_base(self, task_type: str) -> BasePlaybook | None` -- load from `base/<task_type>.yaml`

6. Mark legacy methods with deprecation warnings:
   - Add `import warnings` and `warnings.warn("load() is deprecated, use load_site() or resolve()", DeprecationWarning, stacklevel=2)` at the top of `load()` and `get_relevant_sections()`

7. YAML serialization: use `yaml.dump(playbook.model_dump(exclude_none=True), sort_keys=False, default_flow_style=False)` for human-readable output. For datetime fields, serialize as ISO strings.

**Test File:** `tests/services/test_playbook_service_yaml.py`
- Test load_site with valid YAML file (use tmp_path)
- Test load_site returns None for non-existent source
- Test save_site writes valid YAML, re-loadable
- Test save_flow updates existing playbook with new flow
- Test save_flow creates new playbook when none exists
- Test list_site_playbooks enumerates all .yaml files in site dir
- Test delete_site removes file and returns True
- Test delete_site returns False for non-existent source
- Test load_host and save_host round-trip
- Test load_base returns BasePlaybook from existing survey.yaml
- Test legacy load() still works but emits deprecation warning

**Acceptance Criteria:**
- [ ] All CRUD methods implemented and tested
- [ ] YAML output is human-readable (no flow style for nested dicts)
- [ ] Legacy methods still functional with deprecation warnings
- [ ] Tier directories created on init
- [ ] All tests pass
- [ ] `just all` passes
- [ ] File stays under 500 lines (if approaching limit, extract YAML serialization helpers)

**Session Awareness Instructions:**
1. Check if PyYAML is already a dependency; add via `uv add pyyaml` if not
2. Record the YAML serialization pattern for reuse in auto-playbook generation (Task 012)
3. Note any datetime serialization issues for YAML round-trip
```

---

### Task ID: 007

- **Title**: Add cascading resolution and prompt formatting to PlaybookService
- **File**: src/soren/services/playbook_service.py
- **Complete**: [x]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren operator, I want the playbook system to automatically combine site, host, and base playbook instructions in priority order, so that the CUA agent receives the most specific guidance available without manual configuration.
- **Outcome (what this delivers)**: The `resolve()` method on PlaybookService implementing cascading resolution (site > host > base), `to_prompt_instructions()` for converting FlowPlaybook steps to prompt text, and `update_confidence()` for tracking learning outcomes.

#### Prompt:

```markdown
**Objective:** Add cascading resolution, prompt formatting, and confidence tracking to PlaybookService. Implements discovery document Sections 4, 6.4, and 11.1.

**File to Modify:** `src/soren/services/playbook_service.py` (extended in Task 006)

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 002-006
3. Read discovery document Sections 4 (cascading architecture) and 6.4 (resolve interface)
4. Read discovery document Section 11.1 (prompt injection strategy)

**Detailed Instructions:**
1. Implement `resolve(self, source_name: str, host_domain: str | None, task_type: str, flow_type: str) -> str`:
   - Load site playbook for source_name; extract the flow matching flow_type (and task_type for execution flows)
   - Load host playbook for host_domain if provided
   - Load base playbook for task_type
   - Format each tier's content with priority headers:
     ```
     ## Site-Specific Instructions (highest priority)
     Follow these instructions first. They override all other guidance.
     [site playbook content]

     ## Host Instructions
     These apply to the survey host platform.
     [host playbook content]

     ## General Guidelines
     Follow these when no specific instructions apply above.
     [base playbook content]
     ```
   - If cascade_enabled is False in config, return only the site playbook content
   - Return empty string if no playbooks found at any tier

2. Implement `to_prompt_instructions(self, flow: FlowPlaybook, flow_type: str, credentials: Any | None = None) -> str`:
   - Convert FlowPlaybook steps to numbered imperative instructions
   - Replace `field: "credential:username"` placeholders with credential handoff references (not actual values)
   - Include `expected_after` as inline context: "1. Click 'Log in' in top nav (expect: Login modal visible)"
   - For steps with `triggers_new_tab: true`, add annotation: "[TAB: new tab expected at {host_domain}]"
   - For `DELEGATE_TO_HOST` instruction, add: "[DELEGATE: switch to host playbook for {host_domain}]"
   - For `returns_to_platform: true`, add: "[RETURN: switch back to platform tab]"

3. Implement `update_confidence(self, source_name: str, flow_type: str, success: bool, task_type: str | None = None) -> None`:
   - Load site playbook, find the specified flow
   - On success: increment `consecutive_successes`, set `confidence = min(1.0, confidence + 0.15)`, update `verified_at`
   - On failure: reset `consecutive_successes = 0`, set `confidence = max(0.0, confidence - 0.2)` per M4
   - Save updated playbook

**Test File:** `tests/services/test_playbook_resolution.py`
- Test resolve with all three tiers populated
- Test resolve with only site playbook (no host, no base)
- Test resolve with only base playbook (no site, no host)
- Test resolve returns empty string when nothing found
- Test resolve with cascade_enabled=False
- Test to_prompt_instructions produces numbered steps
- Test to_prompt_instructions handles credential placeholders
- Test to_prompt_instructions annotates tab transitions
- Test update_confidence increments on success
- Test update_confidence decrements on failure (confidence floor at 0.0)
- Test update_confidence resets consecutive_successes on failure

**Acceptance Criteria:**
- [ ] resolve() returns formatted cascading instructions with priority headers
- [ ] to_prompt_instructions() converts FlowPlaybook to CUA-readable prompt
- [ ] update_confidence() tracks learning outcomes per flow
- [ ] All tests pass
- [ ] `just all` passes
- [ ] File stays under 500 lines (if approaching limit, extract formatting helpers to a separate module)

**Session Awareness Instructions:**
1. Record the prompt format (priority headers) for consistency in PersonaContext integration (Task 009)
2. Note the confidence update formula for reuse in auto-playbook generation (Task 013)
```

---

### Task ID: 008

- **Title**: Author base playbooks for email_reply and form task types
- **File**: data/playbooks/base/email_reply.yaml
- **Complete**: [x]
- **Sprint Points**: 1

- **User Story (business-facing)**: As a Soren operator running email or form tasks, I want built-in base playbooks for these task types so that the CUA agent has generic guidance even before any site-specific training.
- **Outcome (what this delivers)**: Two new base playbook YAML files (`email_reply.yaml` and `form.yaml`) following the same structure as the existing `survey.yaml`, providing generic patterns for email reply and form submission task types.

#### Prompt:

```markdown
**Objective:** Author base playbooks for the email_reply and form (generic_form) task types. These ship with Soren and provide the bottom tier of the cascading playbook hierarchy.

**Files to Create:**
- `data/playbooks/base/email_reply.yaml` -- primary
- `data/playbooks/base/form.yaml`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Read `data/playbooks/base/survey.yaml` for the structure and style to follow (216 lines)
3. Read `src/soren/adapters/email_adapter.py` for email task patterns
4. Read `src/soren/adapters/generic_form.py` for form task patterns

**Detailed Instructions:**
1. Create `data/playbooks/base/email_reply.yaml`:
   - `task_type: email_reply`, `version: 1`
   - `patterns.navigation`: inbox navigation, unread filtering, message opening
   - `patterns.reply_composition`: reply button identification, editor interactions, formatting
   - `patterns.completion`: sent confirmation indicators, draft save signals
   - `patterns.error_recovery`: session timeout, send failures, attachment issues
   - Follow the same YAML structure as survey.yaml (description + specific sub-patterns)

2. Create `data/playbooks/base/form.yaml`:
   - `task_type: form`, `version: 1`
   - `patterns.navigation`: form identification, scrolling to see all fields, multi-page forms
   - `patterns.field_types`: text input, dropdown, checkbox, radio, file upload, date picker
   - `patterns.completion`: submit button patterns, success/error messages, confirmation pages
   - `patterns.error_recovery`: validation errors, required fields, session timeouts
   - Follow the same YAML structure as survey.yaml

3. Keep content practical and generic -- patterns that apply across many sites
4. Include common button labels, error messages, and UI patterns

**Acceptance Criteria:**
- [ ] email_reply.yaml is valid YAML and follows survey.yaml structure
- [ ] form.yaml is valid YAML and follows survey.yaml structure
- [ ] Both files include navigation, interaction, completion, and error recovery patterns
- [ ] Content is generic enough to apply across multiple sites
- [ ] Both YAML files load successfully via `BasePlaybook.model_validate(yaml.safe_load(...))`
- [ ] `just all` passes (no code changes, but YAML should be parseable)

**Session Awareness Instructions:**
1. Record the base playbook structure pattern for consistency across task types
2. Note any patterns that could be shared between survey, email, and form base playbooks
```

---

### Task ID: 009

- **Title**: Integrate cascading playbooks into TaskDiscovery, PersonaContext, and TaskExecutor
- **File**: src/soren/services/task_discovery.py
- **Complete**: [x]
- **Sprint Points**: 5

- **User Story (business-facing)**: As a Soren operator, I want the CUA agent to automatically receive cascading playbook instructions during both task discovery and task execution, so that learned playbook knowledge improves agent performance without manual intervention.
- **Outcome (what this delivers)**: Login and discovery playbooks injected into task discovery navigation. Execution playbooks flow through PersonaContext into task executor. Completes Phase 1 integration per discovery document Sections 6.5 and 11.1.

#### Prompt:

```markdown
**Objective:** Wire the cascading playbook system into the three main consumers: TaskDiscovery (login + discovery flows), PersonaContext (prompt assembly), and TaskExecutor (execution flow). Implements discovery document Sections 6.5 and 11.1.

**Files to Modify:**
- `src/soren/services/task_discovery.py` -- primary
- `src/soren/services/persona_context.py`
- `src/soren/services/task_executor.py`
- `src/soren/cli.py` (or `src/soren/cli/cycle.py` after Task 015 refactor)

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 002-007 (all model and service work)
3. Read discovery document Sections 6.5 (integration points) and 11.1 (prompt injection strategy)
4. Read current implementations of all three files

**Detailed Instructions:**

**TaskDiscovery (`task_discovery.py`):**
1. Add `playbook_service: PlaybookService | None = None` parameter to `__init__`
2. In `_phase1_navigate()`, before building the nav prompt:
   - If playbook_service exists, attempt to load login flow: `playbook_service.load_site(source.name)` -> get `flows.login`
   - If login flow exists, use `playbook_service.to_prompt_instructions(login_flow, "login")` to get login instructions
   - If discovery flow exists, use it instead of `adapter.navigation_prompt()`
   - Construct combined prompt per Section 6.5:
     ```python
     if login_flow:
         login_instructions = svc.to_prompt_instructions(login_flow, "login")
         nav_prompt = f"{login_instructions}\n\nAfter login:\n\n{discovery_section}"
     ```
   - If no playbook, fall back to existing `adapter.navigation_prompt()` behavior

**PersonaContext (`persona_context.py`):**
1. Replace the "Service playbook instructions" section with "Cascading playbook instructions"
2. The `playbook_content` parameter remains but now receives the output of `PlaybookService.resolve()`
3. Update the section header from "Service playbook instructions:" to "Playbook instructions (follow in order of specificity):"
4. No signature change needed -- the resolve() output is still a string

**TaskExecutor (`task_executor.py`):**
1. In `execute()`, replace the existing playbook loading logic (lines 79-84):
   ```python
   # OLD: load() + get_relevant_sections()
   # NEW: resolve() with cascading tiers
   if self._playbook_service:
       playbook_content = self._playbook_service.resolve(
           source_name=task.source_name or "",
           host_domain=None,  # Phase 4 adds host domain detection
           task_type=task.task_type or "survey",
           flow_type="execution",
       )
   ```
2. Pass the resolved content to PersonaContext.build() via the existing `playbook_content` parameter

**CLI cycle_run (`cli.py` or `cli/cycle.py`):**
1. In the `cycle_run()` command function, ensure `PlaybookService` is constructed and passed to `TaskDiscovery` constructor
2. If `TaskDiscovery.__init__` is updated to accept `playbook_service`, all callers must pass it
3. Check `SorenMetaAgent` constructor similarly -- ensure playbook_service is wired through

**Test Files:**
- `tests/services/test_task_discovery_playbooks.py`:
  - Test _phase1_navigate uses login playbook when available
  - Test _phase1_navigate falls back to adapter prompt when no playbook
  - Test discovery flow replaces adapter nav prompt
  - Test combined login + discovery prompt structure
- `tests/services/test_persona_context_cascading.py`:
  - Test section header updated to "Playbook instructions"
  - Test cascading content appears in correct position (after task context, before memories)
- `tests/services/test_task_executor_resolve.py`:
  - Test executor calls resolve() instead of load()
  - Test executor passes resolve output to PersonaContext
  - Test executor handles None playbook_service gracefully

**Acceptance Criteria:**
- [ ] TaskDiscovery injects login and discovery playbooks into navigation prompt
- [ ] TaskDiscovery falls back to adapter prompt when no playbooks exist
- [ ] PersonaContext.build() uses updated section header for playbook content
- [ ] TaskExecutor uses PlaybookService.resolve() for cascading playbook loading
- [ ] All existing tests still pass (backward compatibility)
- [ ] All new tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record integration patterns for Phase 2 (learning loop) and Phase 4 (multi-tab)
```

---

### Task ID: 010

- **Title**: Create StuckDetector service
- **File**: src/soren/services/stuck_detector.py
- **Complete**: [x]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren operator, I want the system to detect when the CUA agent is stuck (repeating actions, same screen, or budget exhaustion) so that it can trigger training mode or skip to the next task instead of wasting compute.
- **Outcome (what this delivers)**: A StuckDetector class monitoring CUA agent output for three stuck conditions (same screenshot, action loop, budget exhaustion) as specified in discovery document Section 5.

#### Prompt:

```markdown
**Objective:** Create the StuckDetector service that monitors CUA agent execution for stuck conditions. First component of the autonomous learning loop (Phase 2) per discovery document Section 5.

**File to Create:** `src/soren/services/stuck_detector.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Read discovery document Section 5 "Stuck Detection Heuristics"
3. Read discovery document Section 11.4 (Screenshot Comparison for Stuck Detection)
4. Read `src/soren/verification/baseline.py` for `compare_with_baseline()` histogram similarity (reuse per S3)
5. Read `src/soren/services/task_executor.py` to understand step output structure

**Detailed Instructions:**
1. Create `StuckDetector` class:
   ```python
   class StuckDetector:
       def __init__(
           self,
           step_budget: int,
           same_screen_threshold: int = 3,
           loop_detection_window: int = 6,
           budget_exhaustion_pct: float = 0.8,
       ) -> None:
   ```

2. Implement `record_step(screenshot: bytes | None, action: str) -> None`:
   - Append screenshot bytes and action string to internal history
   - Keep only last `loop_detection_window * 2` entries to bound memory

3. Implement `is_stuck() -> tuple[bool, str]`:
   - **Same screenshot check**: Compare last N screenshots by byte equality. If all identical, flag as stuck. Supplement with histogram similarity from `verification/baseline.py` (`compare_with_baseline()`) per S3 -- if byte-different but histogram similarity > 0.98, also flag. Catches cursor-blink false negatives.
   - **Action loop check**: Check if the last 2*loop_detection_window actions form a repeating pattern (second half equals first half).
   - **Budget exhaustion check**: If steps_taken >= budget_exhaustion_pct * step_budget and no completion signal recorded, flag as stuck.
   - Return `(True, reason_string)` on first matching condition, or `(False, "")`.

4. Implement `record_completion_signal() -> None`:
   - Mark that a completion signal was seen (suppresses budget exhaustion check)

5. Keep the class stateful but lightweight -- no file I/O, no async.

**Test File:** `tests/services/test_stuck_detector.py`
- Test same_screen_threshold triggers after N identical screenshots
- Test same_screen_threshold does NOT trigger with N-1 identical screenshots
- Test action loop detection with repeating pattern (e.g., ["click", "scroll"] * 4)
- Test action loop detection does NOT trigger with non-repeating actions
- Test budget exhaustion triggers at 80% of budget with no completion signal
- Test budget exhaustion does NOT trigger if completion signal was recorded
- Test histogram similarity fallback catches near-identical screenshots (mock compare_with_baseline)
- Test is_stuck returns first matching reason
- Test record_step bounds memory usage

**Acceptance Criteria:**
- [ ] Three stuck detection heuristics implemented per Section 5
- [ ] Histogram similarity from baseline.py integrated per S3
- [ ] Memory bounded (old entries discarded)
- [ ] Completion signal suppresses budget exhaustion
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the StuckDetector interface for integration in execute_with_learning() (Task 011)
2. Note the histogram similarity threshold (0.98) -- may need empirical tuning per Q5
3. Record the compare_with_baseline import path for reuse
```
