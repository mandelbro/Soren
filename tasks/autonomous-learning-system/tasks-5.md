## Summary (tasks-5.md)

- **Tasks in this file**: 3
- **Task IDs**: 021 - 023
- **Total Points**: 7

### Gap Tasks: Stuck Intervention UX + Token Safety Valve + Unattended Mode Auto-Skip

These tasks fill gaps identified during the principal-engineer technical review. Task 021 bridges the autonomous learning loop (Phase 2) and training mode (Phase 3) with the interactive stuck intervention prompt. Task 022 adds a token safety valve to prevent prompt overflow in PersonaContext. Task 023 implements unattended mode auto-skip behavior for stuck sources.

---

## Tasks

### Task ID: 021

- **Title**: Add stuck intervention prompt to cycle run
- **File**: src/soren/services/meta_agent.py
- **Complete**: [ ]
- **Sprint Points**: 3

- **User Story (business-facing)**: As a Soren operator, I want to be prompted with `[t] Train / [s] Skip / [a] Abort` when the agent gets stuck during cycle run, so that I can choose to train the agent, skip the stuck source, or abort the cycle.
- **Outcome (what this delivers)**: The interactive intervention prompt during `soren cycle run` when stuck detection fires. Bridges the autonomous learning loop (Phase 2) and training mode (Phase 3) with a callback-based pattern for testability. References discovery document Sections 10.2 (state machine) and 10.5 (notification design).

#### Prompt:

```markdown
**Objective:** Implement the `[t] Train / [s] Skip / [a] Abort` interactive prompt during `soren cycle run` when stuck detection fires. This is the bridge between the autonomous learning loop and training mode. The implementation uses a callback pattern (similar to TrainingCallbacks) so it can be tested without terminal I/O.

**Files to Modify:**
- `src/soren/services/meta_agent.py` -- primary (intervention callback + dispatch logic)
- `src/soren/cli/cycle.py` -- CLI-side callback implementation (after Task 015 refactor)

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Tasks 013 (confidence tracking / execute_with_learning integration), 016 (train CLI command)
3. Read discovery document Sections 10.2 (state machine) and 10.5 (notification design)
4. Read TrainingCallbacks protocol in `src/soren/services/training_session.py` for callback pattern reference

**Detailed Instructions:**
1. Define an intervention callback protocol:
   ```python
   class InterventionCallbacks(Protocol):
       async def on_stuck(
           self,
           source_name: str,
           stuck_reason: str,
           cycle_context: dict[str, Any],
       ) -> Literal["train", "skip", "abort"]: ...
   ```

2. Accept `intervention_callbacks: InterventionCallbacks | None = None` in `SorenMetaAgent.__init__()` or `run_cycle()`

3. In the `run_cycle()` method, when `should_offer_training` is True:
   - If `intervention_callbacks` is provided (attended mode): call `on_stuck()` and dispatch based on response
     - `"train"`: invoke training session for the stuck source and flow
     - `"skip"`: log `stuck_skipped: true` in cycle report, continue to next source
     - `"abort"`: raise or return early, saving cycle progress
   - If `intervention_callbacks` is None (unattended mode): defer to Task 023 auto-skip behavior

4. Create a `CLIInterventionCallbacks` class in `cli/cycle.py` that implements the protocol using Rich:
   - Display stuck reason in a Rich Panel
   - Prompt `[t] Train / [s] Skip / [a] Abort` with Rich Prompt
   - Return the user's choice

5. Wire `CLIInterventionCallbacks` into the cycle_run CLI command when running in attended mode

**Test File:** `tests/services/test_stuck_intervention.py`
- Test intervention prompt shown when `should_offer_training=True` and callbacks provided
- Test `"train"` response triggers training session invocation
- Test `"skip"` response skips source and logs `stuck_skipped: true`
- Test `"abort"` response aborts cycle gracefully
- Test unattended mode (no callbacks) does not prompt (defers to auto-skip)
- Test CLIInterventionCallbacks with mocked Rich prompt

**Acceptance Criteria:**
- [ ] InterventionCallbacks protocol defined
- [ ] SorenMetaAgent dispatches on stuck based on callback response
- [ ] CLIInterventionCallbacks implements Rich-based prompt
- [ ] Training session invoked on "train" response
- [ ] Source skipped and logged on "skip" response
- [ ] Cycle aborted gracefully on "abort" response
- [ ] Unattended mode (no callbacks) does not block
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the InterventionCallbacks pattern for reuse in other interactive prompts
2. Note the relationship between this task and Task 023 (unattended auto-skip)
3. Update session context with the attended vs. unattended mode dispatch pattern
```

---

### Task ID: 022

- **Title**: Add max_instruction_tokens safety valve to PersonaContext
- **File**: src/soren/services/persona_context.py
- **Complete**: [ ]
- **Sprint Points**: 2

- **User Story (business-facing)**: As a Soren developer, I want the persona context builder to truncate cascading playbook instructions when they exceed a configurable token limit, so that the CUA agent's context window is not overwhelmed.
- **Outcome (what this delivers)**: A `max_instruction_tokens` parameter on `PersonaContext.build()` that truncates cascading playbook instructions by priority (base first, then memories, then host) when total length exceeds the limit. Prevents prompt overflow as playbook knowledge accumulates. References discovery document Section 16 item S2.

#### Prompt:

```markdown
**Objective:** Add `max_instruction_tokens` safety valve to `PersonaContext.build()` to prevent prompt length overflow when cascading playbook instructions grow large. Implements the recommendation from discovery document Section 16 item S2.

**File to Modify:** `src/soren/services/persona_context.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Task 009 (cascading playbook integration into PersonaContext)
3. Read discovery document Section 16 item S2
4. Read current `PersonaContext.build()` implementation to understand prompt assembly order

**Detailed Instructions:**
1. Add `max_instruction_tokens: int | None = None` parameter to `PersonaContext.build()` (default None = no limit for backward compatibility)

2. Add a configurable default in `PlaybooksConfig`:
   - `max_instruction_tokens: int = Field(default=4000, description="Max token estimate for playbook instructions in CUA prompt")`

3. Implement token estimation:
   - Use a simple heuristic: `len(text) // 4` as approximate token count (sufficient for safety valve; exact tokenization not needed)
   - Or accept an optional `token_counter: Callable[[str], int]` for precise counting

4. Truncation priority (lowest priority truncated first):
   - Base playbook instructions (generic, least specific)
   - Memory entries (supplementary context)
   - Host playbook instructions (host-specific but not source-specific)
   - Site playbook instructions (highest priority, truncated last)
   - Task context and persona are never truncated

5. When truncation occurs:
   - Log a warning with the original and truncated token counts
   - Add a marker in the prompt: `[Note: Some lower-priority instructions were truncated to fit context window]`

6. Ensure backward compatibility: when `max_instruction_tokens` is None, no truncation occurs

**Test File:** `tests/services/test_persona_context_token_limit.py`
- Test build() with content under limit passes through unchanged
- Test build() with content over limit truncates lowest-priority sections first
- Test base playbook content truncated before host content
- Test site playbook content is last to be truncated
- Test warning logged on truncation
- Test truncation marker appears in output
- Test None limit (default backward compat) does not truncate
- Test custom token_counter function is used when provided

**Acceptance Criteria:**
- [ ] `max_instruction_tokens` parameter added to `PersonaContext.build()`
- [ ] Truncation follows priority order (base > memories > host > site)
- [ ] Warning logged when truncation occurs
- [ ] Backward compatible (None = no limit)
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the truncation priority order for documentation
2. Note the token estimation heuristic for potential refinement
3. Update session context with the PersonaContext.build() parameter changes
```

---

### Task ID: 023

- **Title**: Add unattended mode stuck auto-skip with configurable timeout
- **File**: src/soren/services/meta_agent.py
- **Complete**: [ ]
- **Sprint Points**: 2

- **User Story (business-facing)**: As a Soren operator running cycles unattended, I want stuck sources to be automatically skipped after a configurable timeout, so that the cycle continues to other sources without human intervention.
- **Outcome (what this delivers)**: Configurable timeout auto-skip behavior when running in unattended mode (no InterventionCallbacks provided). Stuck sources are logged with `stuck_skipped: true` and the next attended run warns the user about skipped sources. References discovery document Section 10.5.

#### Prompt:

```markdown
**Objective:** Implement the unattended mode auto-skip behavior for stuck sources with configurable timeout. Complements Task 021 (attended intervention prompt) by handling the case when no InterventionCallbacks are provided.

**File to Modify:** `src/soren/services/meta_agent.py`

**Prerequisite Requirements:**
1. Initialize the Memory or Session Awareness Protocol
2. Depends on Task 013 (confidence tracking / execute_with_learning integration)
3. Read discovery document Section 10.5 (notification design and unattended behavior)
4. Read Task 021 for the InterventionCallbacks pattern (if implemented first)

**Detailed Instructions:**
1. Add configuration for unattended mode:
   - `stuck_auto_skip_timeout: float = Field(default=30.0, description="Seconds before auto-skipping stuck source in unattended mode")` in relevant config model
   - `unattended_mode: bool` flag (derived from absence of InterventionCallbacks, or explicit config)

2. In `run_cycle()`, when `should_offer_training` is True and no intervention callbacks:
   - Log: "Source '{source_name}' is stuck ({reason}). Auto-skipping after {timeout}s timeout (unattended mode)."
   - Wait for `stuck_auto_skip_timeout` seconds (allows potential external monitoring to react)
   - Mark source as `stuck_skipped: true` in cycle report
   - Continue to next source in the cycle

3. Add stuck source tracking to cycle report:
   - `stuck_sources: list[dict]` with entries containing `source_name`, `stuck_reason`, `skipped_at`, `auto_skipped: true`

4. On next attended run (when InterventionCallbacks ARE provided):
   - At cycle start, check for previously stuck-skipped sources from last cycle report
   - If found, show warning: "The following sources were auto-skipped in the last unattended run: [list]. Consider running `soren train <source>` for these."

5. Ensure the auto-skip does NOT decrement confidence below a floor (0.1) to avoid overly aggressive de-learning from transient failures in unattended mode

**Test File:** `tests/services/test_unattended_auto_skip.py`
- Test unattended mode auto-skips stuck source after timeout
- Test attended mode (with callbacks) shows prompt instead of auto-skip
- Test `stuck_skipped: true` logged in cycle report
- Test stuck_sources list populated correctly
- Test next attended run warns about previously skipped sources
- Test confidence floor (0.1) enforced during unattended auto-skip
- Test configurable timeout value respected

**Acceptance Criteria:**
- [ ] Unattended mode auto-skips stuck sources after configurable timeout
- [ ] Stuck sources logged with `stuck_skipped: true` in cycle report
- [ ] Next attended run warns about previously skipped sources
- [ ] Confidence floor (0.1) prevents excessive de-learning
- [ ] Configurable timeout (default 30s)
- [ ] All tests pass
- [ ] `just all` passes

**Session Awareness Instructions:**
1. Record the unattended mode behavior for documentation
2. Note the confidence floor (0.1) for unattended mode
3. Record the stuck_sources report format for CLI display integration
```
