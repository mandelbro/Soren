## Summary (tasks-4.md)

- **Tasks in this file**: 5
- **Task IDs**: 016 - 020
- **Total Points**: 18

### Phase 3 Continued: CLI Training Commands (7 pts) + Phase 4: Multi-Tab Execution + Host Playbooks (11 pts)

Phase 3 concludes with the CLI train command and playbook management commands. Phase 4 adds VMManager tab introspection, tab-aware execution with DELEGATE_TO_HOST, and host playbook auto-learning integration.

---

## Tasks

### Task ID: 016

- **Title**: Create CLI train command with Rich interactive loop
- **File**: src/soren/cli/train.py
- **Complete**: [ ]
- **Sprint Points**: 5

- **User Story (business-facing)**: As a Soren operator, I want a `soren train <source>` CLI command that guides me through recording playbook steps with Rich terminal UI, so that I can teach the agent how to navigate unfamiliar sites.
- **Outcome (what this delivers)**: The `soren train <source>` command with flow selection, Rich panels for screenshot descriptions, interactive step prompts, and integration with TrainingSession. Implements CLI commands from discovery document Section 10.7.

#### Prompt:

```markdown
**Objective:** Create the `soren train` CLI command group with Rich interactive UI. Implements training commands from discovery document Section 10.7 using Rich+Click per Section 7.2.

**File to Create:** `src/soren/cli/train.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 014 (TrainingSession), 015 (cli/ package)
3. Read discovery document Sections 7.2, 10.6, and 10.7
4. Use context7 for Rich library documentation (Console, Panel, Live, Table, Prompt)
5. Read existing CLI patterns in `src/soren/cli/` for consistency

**Detailed Instructions:**
1. Create `train` command group and register in `cli/__init__.py`

2. Implement main training command:
   ```python
   @train.command("run")
   @click.argument("source")
   @click.option("--flow", type=click.Choice(["login", "discovery", "execution"]), default=None)
   @click.option("--task-type", default=None)
   @click.option("--from-step", type=int, default=None)
   @click.option("--config", type=click.Path(exists=True), default=None)
   ```

3. Implement `RichTrainingCallbacks` (implements `TrainingCallbacks` protocol):
   - `show_screenshot()`: VLM description in Rich Panel with border
   - `get_instruction()`: `Console.input()` with styled prompt; support /help, /done, /abort, /undo, /steps
   - `confirm_step()`: Result in Panel, prompt `[y] Accept / [n] Redo / [d] Done`
   - `get_expected_after()`: Show VLM suggestion, allow edit or accept
   - `show_message()`: Rich console print with styling
   - `show_error()`: Rich console print in red

4. Flow per Section 10.6:
   - Show Rich Panel with training context (source, flow type, commands)
   - Initialize TrainingSession with RichTrainingCallbacks
   - Call `session.run()`
   - On completion, show summary table of recorded steps
   - On abort, show partial save location

5. Handle `--flow` selection:
   - Not specified: train all flows sequentially (login -> discovery -> execution)
   - Specified: train only that flow
   - `--task-type` only for execution flows

6. Handle `--from-step` for resuming interrupted training

**Test File:** `tests/cli/test_train_command.py`
- Test train command with mocked TrainingSession (verify correct params)
- Test flow selection logic
- Test RichTrainingCallbacks.get_instruction returns user input
- Test /help shows help text
- Test --from-step passed to session
- Use Click CliRunner for invocation tests

**Acceptance Criteria:**
- [ ] `soren train <source>` works with Rich UI
- [ ] Flow selection (--flow) works for all three types
- [ ] Rich panels display VLM descriptions and step summaries
- [ ] Interactive prompts for instruction, confirmation, expected_after
- [ ] Special commands (/help, /done, /abort) work
- [ ] Completion shows summary of recorded steps
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record Rich UI patterns for consistency with management commands (Task 017)
2. Note the RichTrainingCallbacks implementation
3. Record Click + Rich integration pattern
```

---

### Task ID: 017

- **Title**: Add playbook management CLI commands (list, show, delete)
- **File**: src/soren/cli/train.py
- **Complete**: [ ]
- **Sprint Points**: 2

- **User Story (business-facing)**: As a Soren operator, I want CLI commands to list, inspect, and delete playbooks so that I can manage the agent's learned knowledge and diagnose playbook issues.
- **Outcome (what this delivers)**: Subcommands `soren train list`, `soren train show <source>`, and `soren train delete <source>` for playbook health dashboards, cascade views, and cleanup. Implements management commands from discovery document Section 10.7 and Journey E.

#### Prompt:

```markdown
**Objective:** Add playbook management subcommands to the train CLI group. Implements non-interactive management commands from discovery document Section 10.7 and Section 10.3 Journey E.

**File to Modify:** `src/soren/cli/train.py` (created in Task 016)

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 006 (PlaybookService CRUD), 007 (resolve), 016 (train CLI group)
3. Read discovery document Section 10.3 Journey E and Section 10.7 command reference

**Detailed Instructions:**
1. Implement `soren train list`:
   - Rich table: Source, Login (step count or "none"), Discovery, Execution (per task_type), Confidence, Last Verified
   - Staleness warnings: 30 days for login/discovery, 14 days for execution
   - Host playbooks in separate section
   - Base playbooks in separate section

2. Implement `soren train show <source>`:
   - Display site playbook with formatted flow details
   - `--cascade` flag: show full resolved chain (call `resolve()` per flow type)
   - Rich Panels to separate tiers

3. Implement `soren train delete <source>`:
   - `--flow TYPE` to delete specific flow (default: entire playbook)
   - Confirmation prompt
   - Call `PlaybookService.delete_site()`

**Test File:** `tests/cli/test_train_management.py`
- Test list with mock PlaybookService returning sample playbooks
- Test list with no playbooks shows "No playbooks found"
- Test show displays playbook content
- Test show --cascade displays resolved chain
- Test delete calls delete_site
- Test delete with --flow deletes specific flow
- Use CliRunner for invocation

**Acceptance Criteria:**
- [ ] `soren train list` shows playbook health dashboard with staleness warnings
- [ ] `soren train show <source>` displays formatted playbook
- [ ] `soren train show <source> --cascade` shows resolved chain
- [ ] `soren train delete <source>` deletes with confirmation
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record staleness thresholds (30 days login/discovery, 14 days execution)
2. Note Rich table formatting pattern for consistency
```

---

### Task ID: 018

- **Title**: Add tab introspection methods to VMManager
- **File**: src/soren/services/vm_manager.py
- **Complete**: [x]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren operator running multi-tab surveys, I want the VM manager to detect and switch between browser tabs, so that the agent can handle survey platforms that open tasks in new tabs.
- **Outcome (what this delivers)**: New VMManager methods for tab counting, URL retrieval, tab switching, and domain-based tab finding via AppleScript. Implements "New VMManager methods" from discovery document Section 3.

#### Prompt:

```markdown
**Objective:** Extend VMManager with Safari tab introspection methods using AppleScript. Enables tab-aware execution per discovery document Section 3.

**File to Modify:** `src/soren/services/vm_manager.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. No task dependencies (parallel with Phase 2/3)
3. Read discovery document Section 3 (Multi-Tab Execution Model)
4. Read existing `_run_osascript()` and Safari window management patterns

**Detailed Instructions:**
1. Add `get_tab_count(self) -> int`:
   ```applescript
   tell application "Safari" to count of tabs of window 1
   ```
   Return 0 on error.

2. Add `get_active_tab_url(self) -> str`:
   ```applescript
   tell application "Safari" to URL of current tab of window 1
   ```
   Return empty string on error.

3. Add `switch_to_tab(self, index: int) -> bool`:
   ```applescript
   tell application "Safari" to set current tab of window 1 to tab {index} of window 1
   ```
   1-based index. Return True/False.

4. Add `find_tab_by_domain(self, domain: str) -> int | None`:
   AppleScript to iterate tabs, check URL contains domain. Return 1-based index or None.

5. Add `wait_for_new_tab(self, initial_count: int, timeout_s: float = 5.0) -> bool`:
   Poll `get_tab_count()` until exceeds initial_count or timeout. 0.5s interval.

6. All methods handle `self._computer is None` (raise RuntimeError).

**Test File:** `tests/services/test_vm_manager_tabs.py`
- Test get_tab_count parses osascript output (mock _run_osascript)
- Test get_tab_count returns 0 on error
- Test get_active_tab_url returns URL string
- Test switch_to_tab returns True/False
- Test find_tab_by_domain returns index or None
- Test wait_for_new_tab detects new tab
- Test wait_for_new_tab returns False on timeout

**Acceptance Criteria:**
- [ ] All five tab methods implemented using _run_osascript
- [ ] wait_for_new_tab polls with timeout
- [ ] Methods handle errors gracefully
- [ ] All tests pass with mocked osascript
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record AppleScript patterns for Safari tab manipulation
2. Note 1-based indexing convention
3. Record wait_for_new_tab pattern for tab-aware execution (Task 019)
```

---

### Task ID: 019

- **Title**: Implement tab-aware execution flow with DELEGATE_TO_HOST
- **File**: src/soren/services/task_executor.py
- **Complete**: [ ]
- **Sprint Points**: 5

- **User Story (business-facing)**: As a Soren operator, I want the task execution loop to handle survey platforms that open tasks in new browser tabs, so that the agent can complete surveys on external hosts and return to the platform.
- **Outcome (what this delivers)**: Tab-aware execution in TaskExecutor handling `triggers_new_tab`, `DELEGATE_TO_HOST` for host playbook handoff, and `returns_to_platform` for tab switching. Implements discovery document Sections 3 and 6.5.

#### Prompt:

```markdown
**Objective:** Add tab-aware execution to TaskExecutor. Implements discovery document Sections 3 and 6.5 with DELEGATE_TO_HOST fallback chain per S4.

**File to Modify:** `src/soren/services/task_executor.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 007 (resolve), 009 (integration), 018 (VMManager tabs)
3. Read discovery document Sections 3 and 6.5
4. Read Section 16 S4 for DELEGATE_TO_HOST fallback chain

**Detailed Instructions:**
1. Add `_execute_with_tabs()` private method for flows with tab transitions

2. Tab transition handling per Section 3:
   - **Before `triggers_new_tab` step**: Record tab count via `vm.get_tab_count()`
   - **After step**: `vm.wait_for_new_tab(initial_count)` to detect new tab
   - **On new tab**: Get URL via `vm.get_active_tab_url()`, extract host domain
   - **DELEGATE_TO_HOST**: Resolve per S4 fallback chain:
     1. Host playbook for domain
     2. Base playbook with host domain as context
     3. Generic CUA reasoning
   - **Execute delegated flow** with remaining step budget (budget sharing per S4)
   - **returns_to_platform**: `vm.find_tab_by_domain(platform_domain)` then `vm.switch_to_tab()`

3. Error recovery:
   - New tab not detected: log warning, continue current tab
   - Platform tab not found on return: attempt manual navigation
   - No host playbook: fall back to base or generic

4. Integrate into `execute()` and `execute_with_learning()` when execution flow has tab steps

**Test File:** `tests/services/test_executor_tabs.py`
- Test tab detection after triggers_new_tab step (mock VMManager)
- Test DELEGATE_TO_HOST resolves host playbook
- Test DELEGATE_TO_HOST falls back to base when no host playbook
- Test returns_to_platform switches to correct tab
- Test timeout on new tab does not crash
- Test platform tab not found falls back to navigation
- Test step budget shared with delegated execution
- Test no-tab-steps flow uses standard execution

**Acceptance Criteria:**
- [ ] Tab transition detection using VMManager methods
- [ ] DELEGATE_TO_HOST resolves with fallback chain (host > base > generic)
- [ ] Platform return uses domain-based tab finding
- [ ] Step budget shared with delegated execution
- [ ] Error recovery for tab detection and return failures
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record tab-aware execution pattern
2. Note DELEGATE_TO_HOST fallback chain for documentation
3. Record error recovery patterns
```

---

### Task ID: 020

- **Title**: Add host playbook integration and auto-learning into execution lifecycle
- **File**: src/soren/services/playbook_service.py
- **Complete**: [ ]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren operator, I want host playbooks to be automatically generated when the agent successfully completes a survey on a new host domain, so that future surveys on the same host benefit from learned navigation patterns.
- **Outcome (what this delivers)**: Host playbook CRUD wired into execution lifecycle, auto-learning trigger for new host domains, and host tier fully connected in cascading resolution. Completes Phase 4.

#### Prompt:

```markdown
**Objective:** Complete host playbook integration: auto-learning in execution lifecycle, host tier in cascading resolution. Finalizes the three-tier hierarchy.

**Files to Modify:**
- `src/soren/services/playbook_service.py` -- host resolution in resolve()
- `src/soren/services/meta_agent.py` -- trigger host auto-learning

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 006 (load_host/save_host), 012 (auto_generate_host_playbook), 019 (tab-aware execution)
3. Read discovery document Section 4 (host tier) and Section 5 (host auto-learning)

**Detailed Instructions:**
1. Ensure `PlaybookService.resolve()` loads and includes host playbooks:
   - When `host_domain` provided, call `load_host(host_domain)` and include in cascade
   - Format as "Host Instructions" section
   - Navigation overrides and quirks as instruction text

2. In `SorenMetaAgent.run_cycle()`, after successful tab-aware execution:
   - Extract host domain from step log (tab switch entries with URLs)
   - Check if host playbook exists for domain
   - If not and `auto_learn_host` enabled: generate and save via `auto_generate_host_playbook()`
   - Wrap in try/except

3. Add `format_host_playbook(self, playbook: HostPlaybook) -> str` helper:
   - Convert navigation dict and quirks to readable instructions
   - Quirks as warnings: "Note: {quirk}"

4. Add host confidence tracking via `update_host_confidence()`:
   - Similar to site confidence but on HostPlaybook

**Test File:** `tests/services/test_host_playbook_integration.py`
- Test resolve() includes host content when host_domain provided
- Test resolve() skips host tier when no host playbook
- Test host auto-learning triggers on new domain
- Test host auto-learning skips when playbook exists
- Test host auto-learning respects auto_learn_host flag
- Test format_host_playbook produces readable output
- Test host confidence tracking

**Acceptance Criteria:**
- [ ] Host playbooks in cascading resolution when host_domain provided
- [ ] Auto-learning generates host playbooks for new domains
- [ ] Existing host playbooks not overwritten
- [ ] Host confidence tracked independently
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record complete three-tier integration pattern
2. Note host domain extraction pattern from step logs
3. Document full cascading resolution chain
```
