## Summary (tasks-3.md)

- **Tasks in this file**: 5
- **Task IDs**: 011 - 015
- **Total Points**: 20

### Phase 2 Continued: Autonomous Learning Loop (12 pts) + Phase 3 Start: TrainingSession + CLI Refactor (8 pts)

This file completes Phase 2 with the execute_with_learning wrapper, auto-playbook generation, and confidence tracking. Phase 3 begins with the TrainingSession orchestrator and CLI package refactor.

---

## Tasks

### Task ID: 011

- **Title**: Add execute_with_learning wrapper to TaskExecutor
- **File**: src/soren/services/task_executor.py
- **Complete**: [ ]
- **Sprint Points**: 5

- **User Story (business-facing)**: As a Soren operator, I want the task execution loop to automatically detect when the agent is stuck and signal that training is needed, so that I can intervene only when the autonomous system cannot handle a site.
- **Outcome (what this delivers)**: An `execute_with_learning()` method on TaskExecutor that wraps `execute()` with stuck detection monitoring, returning both the TaskResult and a `should_offer_training` boolean. Core integration point for the autonomous learning loop per discovery document Section 5.

#### Prompt:

```markdown
**Objective:** Add the `execute_with_learning()` wrapper to TaskExecutor that integrates StuckDetector into the CUA agent execution loop. Implements discovery document Section 5 "Integration with TaskExecutor."

**File to Modify:** `src/soren/services/task_executor.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 007 (resolve), 009 (integration), 010 (StuckDetector)
3. Read discovery document Section 5 "Integration with TaskExecutor" and Section 10.2 state machine
4. Read the current `execute()` method implementation

**Detailed Instructions:**
1. Add `execute_with_learning()` method:
   ```python
   async def execute_with_learning(
       self,
       task: Task,
       persona: Persona,
       computer: Any,
       memories: list[str] | None = None,
   ) -> tuple[TaskResult, bool]:
       """Execute with stuck detection. Returns (result, should_offer_training)."""
   ```

2. The method should:
   - Create a `StuckDetector` with the executor's step budget
   - Resolve the playbook chain via `PlaybookService.resolve()` and prepend as a memory
   - Run the CUA agent loop (similar to `execute()` but with stuck monitoring):
     - After each step, call `detector.record_step(screenshot_bytes, action_string)`
     - After each step, call `detector.is_stuck()` -- if stuck, break the loop early
     - On completion signal, call `detector.record_completion_signal()`
   - Determine `should_offer_training`:
     - `True` if stuck (detector flagged stuck)
     - `False` if success or normal failure (verification failed, exception)
   - Return `(TaskResult, should_offer_training)`

3. Do not change the external behavior or signature of the existing `execute()` method. Internal extraction of a shared CUA loop helper is permitted if it reduces duplication below 30 lines. The learning wrapper is additive. Callers that don't need learning (e.g., training mode) continue using `execute()`.

4. Use `FlowPlaybook.max_steps` (if available from the resolved playbook) as the StuckDetector's step budget, capped by the executor's global step budget. If no playbook is resolved, use the executor's default step budget.

5. Extract screenshot bytes and action string from step output items:
   - Screenshot bytes: reuse `_extract_screenshot_bytes()`
   - Action string: extract from `_parse_output_item()` result's `action_type` field

**Test File:** `tests/services/test_executor_learning.py`
- Test execute_with_learning returns (result, False) on successful execution (mock CUA agent)
- Test execute_with_learning returns (result, True) when StuckDetector flags stuck
- Test execute_with_learning returns (result, False) on normal failure
- Test StuckDetector receives step data on each iteration
- Test playbook chain is resolved and included in CUA instructions
- Test execute_with_learning does not modify behavior of existing execute()

**Acceptance Criteria:**
- [ ] execute_with_learning() implemented with stuck detection integration
- [ ] Returns tuple of (TaskResult, should_offer_training)
- [ ] Existing execute() method unchanged
- [ ] StuckDetector monitors each step
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the execute_with_learning interface for SorenMetaAgent integration
2. Note how screenshot bytes and action strings are extracted for the detector
3. Record the pattern for future Phase 4 tab-aware execution integration
```

---

### Task ID: 012

- **Title**: Create auto-playbook generation from successful runs
- **File**: src/soren/services/auto_playbook.py
- **Complete**: [ ]
- **Sprint Points**: 5

- **User Story (business-facing)**: As a Soren operator, I want the system to automatically generate playbooks from successful autonomous runs, so that the agent learns and improves without requiring human training.
- **Outcome (what this delivers)**: Functions `auto_generate_site_playbook()` and `auto_generate_host_playbook()` that convert successful execution step logs into structured playbooks via LLM summarization. Extends the existing `PlaybookService.write_back()` pattern per discovery document Section 5.

#### Prompt:

```markdown
**Objective:** Create the auto-playbook generation module. Implements "Auto-Playbook Generation from Successful Runs" and "Host playbook auto-learning" from discovery document Section 5.

**File to Create:** `src/soren/services/auto_playbook.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 002 (SitePlaybook models), 006 (PlaybookService CRUD)
3. Read discovery document Section 5 auto-playbook generation and host auto-learning
4. Read existing `PlaybookService._format_steps_for_llm()` and `_build_summarization_prompt()` for pattern
5. Read `src/soren/services/llm_service.py` for LLM generation interface

**Detailed Instructions:**
1. Create `auto_generate_site_playbook()`:
   ```python
   async def auto_generate_site_playbook(
       llm: LLMService,
       step_log: list[dict[str, Any]],
       source_name: str,
       source_url: str,
       flow_type: str,
       task_type: str | None = None,
       model: str | None = None,
   ) -> FlowPlaybook:
   ```
   - Format step log using same pattern as `PlaybookService._format_steps_for_llm()`
   - Build LLM prompt requesting structured PlaybookStep entries: imperative instructions, expected_after descriptions, credential field markers, tab transition flags
   - Parse LLM output into `PlaybookStep` objects
   - Return `FlowPlaybook` with `verified_by="auto"`, `confidence=0.0`, steps list

2. Create `auto_generate_host_playbook()`:
   ```python
   async def auto_generate_host_playbook(
       llm: LLMService,
       step_log: list[dict[str, Any]],
       host_domain: str,
       model: str | None = None,
   ) -> HostPlaybook:
   ```
   - Separate LLM prompt per Section 5: "What is specific to the host UI vs. the particular survey?"
   - Extract navigation patterns, quirks, host-specific elements
   - Return `HostPlaybook` with `learned_from="auto"`, `confidence=0.0`

3. Create helper `_parse_steps_from_llm(response: str) -> list[PlaybookStep]`:
   - Parse numbered list or YAML-like format from LLM
   - Handle malformed output gracefully (return empty list on parse failure)

4. LLM prompts should request YAML-parseable output for reliability.

**Test File:** `tests/services/test_auto_playbook.py`
- Test auto_generate_site_playbook produces FlowPlaybook (mock LLM)
- Test auto_generate_site_playbook sets verified_by="auto" and confidence=0.0
- Test auto_generate_host_playbook produces HostPlaybook (mock LLM)
- Test _parse_steps_from_llm handles well-formed numbered list
- Test _parse_steps_from_llm handles YAML-formatted output
- Test _parse_steps_from_llm returns empty list on garbage input
- Test credential field detection (login actions produce field markers)

**Acceptance Criteria:**
- [ ] auto_generate_site_playbook converts step logs to FlowPlaybook via LLM
- [ ] auto_generate_host_playbook extracts host-specific patterns via LLM
- [ ] LLM prompt requests structured, parseable output
- [ ] Graceful handling of malformed LLM output
- [ ] Generated playbooks have correct metadata (verified_by, confidence)
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the LLM prompt templates for future refinement
2. Note the step log format expected by the generation functions
3. Record the parse pattern for reuse in training session (Task 014)
```

---

### Task ID: 013

- **Title**: Add confidence tracking and learning accumulation to SorenMetaAgent
- **File**: src/soren/services/meta_agent.py
- **Complete**: [ ]
- **Sprint Points**: 2

- **User Story (business-facing)**: As a Soren operator, I want the system to track confidence in its playbooks over multiple runs, so that proven playbooks are trusted more and failing playbooks are flagged for retraining.
- **Outcome (what this delivers)**: SorenMetaAgent wired to use execute_with_learning(), trigger auto-playbook generation on success, and track confidence per the learning accumulation table in discovery document Section 5.

#### Prompt:

```markdown
**Objective:** Wire auto-playbook generation and confidence tracking into SorenMetaAgent's execution lifecycle. Implements "Learning Accumulation Over Multiple Cycles" from discovery document Section 5.

**Files to Modify:**
- `src/soren/services/meta_agent.py` -- primary
- `src/soren/services/playbook_service.py` (update_confidence already added in Task 007)

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 007 (update_confidence), 011 (execute_with_learning), 012 (auto_playbook)
3. Read discovery document Section 5 "Learning Accumulation" table
4. Read current `src/soren/services/meta_agent.py` finalize section (lines 196-232)

**Detailed Instructions:**
1. In `SorenMetaAgent.__init__()`, accept optional `auto_playbook` module functions or import them
2. In `run_cycle()`, replace `executor.execute()` with `executor.execute_with_learning()`:
   ```python
   task_result, should_offer_training = await self._executor.execute_with_learning(
       task, persona, computer, memories=memories
   )
   ```
3. After successful execution (finalize block):
   - Call `auto_generate_site_playbook()` with the step log
   - Save via `playbook_service.save_flow()` (do not overwrite human-trained playbooks -- check `verified_by`)
   - Call `playbook_service.update_confidence()` with `success=True`
   - If first run on new host domain and `auto_learn_host` enabled, call `auto_generate_host_playbook()`
   - Wrap all auto-generation in try/except (learning failures must not break cycles)
4. After failed execution:
   - Call `playbook_service.update_confidence()` with `success=False`
5. When `should_offer_training` is True:
   - Log the stuck condition (training mode integration comes in Phase 3)
   - Record in cycle report: `report.stuck_detected = True`, `report.stuck_reason = reason`
6. Preserve existing `write_back()` call

**Test File:** `tests/services/test_meta_agent_learning.py`
- Test successful execution triggers auto_generate_site_playbook (mock)
- Test auto-generated playbook saved via save_flow
- Test human-trained playbooks NOT overwritten by auto-generation
- Test confidence updates on success and failure
- Test should_offer_training=True logs stuck detection
- Test auto-generation failure does not crash cycle
- Test existing write_back behavior preserved

**Acceptance Criteria:**
- [ ] SorenMetaAgent uses execute_with_learning() for execution
- [ ] Auto-playbook generation runs after successful execution
- [ ] Human-trained playbooks protected from auto-overwrite
- [ ] Confidence tracking updates on every execution outcome
- [ ] Stuck detection signaled in cycle report
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record SorenMetaAgent learning integration pattern
2. Note "do not overwrite human" rule -- check verified_by before save
3. Record cycle report extensions for training mode CLI (Phase 3)
```

---

### Task ID: 014

- **Title**: Create TrainingSession orchestrator with callback-based I/O
- **File**: src/soren/services/training_session.py
- **Complete**: [ ]
- **Sprint Points**: 5

- **User Story (business-facing)**: As a Soren operator, I want to manually guide the CUA agent through a site's login, discovery, or execution flow when autonomous learning fails, so that the agent can learn from my demonstrations.
- **Outcome (what this delivers)**: A `TrainingSession` orchestrator implementing the training loop state machine (INIT -> PREREQ_REPLAY -> NAVIGATE -> step loop -> FINALIZING -> SAVING) with callback-based I/O for testability. Per discovery document Section 10.6.

#### Prompt:

```markdown
**Objective:** Create the TrainingSession orchestrator for human-guided training. Implements the state machine from discovery document Section 10.6 with callback-based I/O decoupled from CLI.

**File to Create:** `src/soren/services/training_session.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 002 (models), 006 (PlaybookService CRUD), 007 (prompt formatting)
3. Read discovery document Section 10.6 (Training Session Detail) thoroughly
4. Read discovery document Section 11.3 (Single-Step CUA Execution)
5. Read `src/soren/services/vm_manager.py` for VM/computer interaction patterns

**Detailed Instructions:**
1. Define callback protocol:
   ```python
   class TrainingCallbacks(Protocol):
       async def show_screenshot(self, screenshot: bytes, vlm_description: str) -> None: ...
       async def get_instruction(self) -> str: ...
       async def confirm_step(self, screenshot: bytes, vlm_description: str) -> str: ...
       async def get_expected_after(self, vlm_suggestion: str) -> str: ...
       async def show_message(self, message: str) -> None: ...
       async def show_error(self, error: str) -> None: ...
   ```

2. Define training session states:
   ```python
   class TrainingState(str, Enum):
       INIT = "init"
       PREREQ_REPLAY = "prereq_replay"
       NAVIGATE = "navigate"
       STEP_LOOP = "step_loop"
       FINALIZING = "finalizing"
       SAVING = "saving"
       DONE = "done"
       ERROR = "error"
   ```

3. Create `TrainingSession` class with `async def run(self) -> FlowPlaybook | None`

4. State machine per Section 10.6:
   - **INIT**: Ensure VM ready, connect computer, capture initial screenshot
   - **PREREQ_REPLAY**: If training discovery/execution, replay login playbook first (abort if login fails per M5)
   - **NAVIGATE**: Navigate to source URL
   - **STEP_LOOP**: Main loop: capture screenshot -> VLM description -> show user -> get instruction -> CUA single-step -> confirm [y/n/d] -> get expected_after -> record PlaybookStep
   - **FINALIZING**: Show recorded steps summary
   - **SAVING**: Save via PlaybookService.save_flow()

5. Handle commands: `/help`, `/done`, `/abort`, `/undo`, `/steps`
6. Handle errors: CUA exceptions offer retry/skip/abort
7. Single-step CUA execution per Section 11.3 (one ComputerAgent per step)

**Test File:** `tests/services/test_training_session.py`
- Test full loop with mock callbacks (3 steps -> save)
- Test /done exits step loop and saves
- Test /abort saves partial and returns None
- Test /undo removes last step
- Test prereq_replay runs login before discovery training
- Test prereq_replay skipped for login training
- Test error during CUA step offers retry
- Test state transitions follow expected order

**Acceptance Criteria:**
- [ ] Full state machine from Section 10.6 implemented
- [ ] Callback-based I/O for testability
- [ ] Special commands all work (/help, /done, /abort, /undo, /steps)
- [ ] Prereq replay enforced for discovery/execution training
- [ ] Single-step CUA execution per Section 11.3
- [ ] Partial save on abort
- [ ] All tests pass
- [ ] `just all` passes
- [ ] File stays under 500 lines

**Session Awareness Instructions:**
1. Record the TrainingCallbacks protocol for CLI implementation (Task 016)
2. Note single-step CUA execution pattern
3. Record the state machine flow for debugging
```

---

### Task ID: 015

- **Title**: Refactor cli.py into cli/ package
- **File**: src/soren/cli/__init__.py
- **Complete**: [ ]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren developer, I want the CLI code organized into a package with separate modules per command group, so that adding training commands does not push a single file past maintainable size.
- **Outcome (what this delivers)**: The existing `cli.py` (800 lines) refactored into `cli/` package with separate modules per command group. All commands work identically. No behavioral changes.

#### Prompt:

```markdown
**Objective:** Refactor monolithic `src/soren/cli.py` into `src/soren/cli/` package. Pure structural refactor -- no behavioral changes. Identified in discovery document Section 8 Phase 3 and technical review (Low Risk: "CLI File Becomes Unwieldy").

**Files to Create:**
- `src/soren/cli/__init__.py` -- exports `cli` group, registers all subcommands
- `src/soren/cli/cycle.py` -- cycle group
- `src/soren/cli/discover.py` -- discover group
- `src/soren/cli/tasks.py` -- tasks group
- `src/soren/cli/vm.py` -- vm group
- `src/soren/cli/credentials.py` -- credentials group
- `src/soren/cli/playbooks.py` -- playbooks group
- `src/soren/cli/personas.py` -- personas group
- `src/soren/cli/resources.py` -- resources group
- `src/soren/cli/history.py` -- history group
- `src/soren/cli/uat.py` -- uat group

**File to Delete:** `src/soren/cli.py` (after migration verified)

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Read current `src/soren/cli.py` thoroughly (800 lines)
3. Check `pyproject.toml` for CLI entry point configuration
4. Check any imports of `soren.cli` in the codebase

**Detailed Instructions:**
1. Create `src/soren/cli/__init__.py`:
   - Define top-level `@click.group()` `cli` function with version option
   - Import and register each command group
   - Keep `load_dotenv()` call

2. For each command group, create a module with the group decorator and commands
3. Register groups in `__init__.py` via `cli.add_command()`
4. Update entry point in `pyproject.toml` if needed
5. Delete old `src/soren/cli.py`
6. Run `soren --help` to verify all groups appear

**Test Approach:**
- No new test file for pure refactor
- Run `just all` to verify nothing breaks
- Verify `soren --help` shows all command groups

**Acceptance Criteria:**
- [ ] All existing CLI commands work identically
- [ ] Each command group in its own module file
- [ ] No module exceeds 150 lines
- [ ] Old `cli.py` removed
- [ ] Entry point updated if needed
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record CLI package structure for training command integration (Task 016)
2. Note how command groups are registered in __init__.py
3. Check if external scripts reference `soren.cli` directly
```
