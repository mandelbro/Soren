# Closing the Integration Gap: A Retrospective on the Autonomous Learning System

*A post-mortem on how a well-written discovery document produced a feature that passed all tests and solved none of the original problem, and a process proposal for preventing it.*

---

## The failure

The autonomous learning system discovery document (`docs/discovery/autonomous-learning-system.md`) is 600+ lines of careful analysis. It identifies the problem (CUA agent can't navigate unfamiliar sites), proposes a solution (autonomous-first learning with human training as fallback), maps every integration point (credentials, personas, playbooks, cross-step context), provides code snippets showing exactly how credential handoff works (`cred = self._resolve_credential(source, persona)`), and specifies an implementation order that builds autonomous capability first and adds the human fallback last.

The task breakdown captured 23 tasks across 5 phases, totaling 73 story points. Every task was implemented. Every task passed its acceptance criteria. `just all` passed after every commit.

The result: a training system where the human explains every step, the agent loses context between steps, login credentials from the persona are never wired in, retries repeat the identical failed action three times, and a week of debugging was needed to get UI-TARS to find a login button. The word "autonomous" appears in the discovery title but nowhere in the delivered behavior.

This is not a code quality failure. The code is well-structured, well-tested, and follows the patterns specified. This is an **integration failure** — the feature was built as an isolated silo without holistic understanding of the system it was joining.

---

## Root cause analysis

### 1. The task prompt referenced the wrong discovery sections

Task 014 (TrainingSession, 5 points, the central orchestrator) lists these prerequisites:

> Read discovery document Section 10.6 (Training Session Detail) thoroughly
> Read discovery document Section 11.3 (Single-Step CUA Execution)

Section 10.6 describes the state machine: INIT → PREREQ_REPLAY → NAVIGATE → STEP_LOOP → FINALIZING → SAVING → DONE. The task implements that state machine faithfully.

But Section 6.5 contains the credential integration code:

```python
if login_flow:
    cred = self._resolve_credential(source, persona)
    login_instructions = svc.to_prompt_instructions(login_flow, "login", cred)
```

Section 5 describes autonomous context inheritance: "training inherits browser state." Section 5 also describes retry with reasoning about failure, not identical repeats.

None of these sections are referenced in Task 014's prompt. The task was scoped to the state machine only, and the state machine was implemented correctly in isolation. The integration points — the parts that make training useful — fell between sections.

### 2. Acceptance criteria tested the mechanism, not the outcome

Task 014's acceptance criteria:

```
- [ ] Full state machine from Section 10.6 implemented
- [ ] Callback-based I/O for testability
- [ ] Special commands all work (/help, /done, /abort, /undo, /steps)
- [ ] Prereq replay enforced for discovery/execution training
- [ ] Single-step CUA execution per Section 11.3
- [ ] Partial save on abort
- [ ] All tests pass
- [ ] `just all` passes
- [ ] File stays under 500 lines
```

Every item here is about the mechanism: the state machine transitions, the callback protocol, the slash commands. None test the outcome: "Can this feature train a login flow end-to-end using the persona's stored credentials?" "Does a failed step inform the next attempt about why it failed?"

The acceptance criteria verify *what the code does* but not *what the feature delivers*. A training session that implements every state transition perfectly but can't log in because it doesn't know the password passes all criteria.

### 3. No one asked "does this feature make sense in isolation?"

The discovery document explicitly says:

> Note the shift: training mode (Phase 3) is no longer the first thing to build. The autonomous learning loop (Phase 2) delivers value sooner because it works without human intervention. Training mode becomes the safety net for cases where autonomous learning fails.

Phase 3 was built and deployed as if it were the primary mode. The autonomous-first design from Phase 2 was not yet exercised. The result is a "fallback" that became the only mode, built without the context of what it was supposed to fall back *from*.

If anyone had asked "can I use this training feature to train a login flow for Sproutful right now?" before marking the tasks complete, the answer would have been "no, because it doesn't know the credentials and it doesn't know the URL." That question was never asked.

### 4. Session context loss across the implementation timeline

The 23 tasks were likely implemented across multiple Claude Code sessions spanning days or weeks. By the time someone was building Task 014, the rich context from Sections 5 and 6.5 had been compressed into "TrainingSession — discovery Section 10.6." The discovery document existed on disk, but the task prompt didn't point to the relevant integration sections, so the implementing agent had no reason to read them.

This is the same class of failure as the vision drift incidents that motivated the Vision-Keeper guardrail: load-bearing context drops out of the working window as sessions progress. But the Vision-Keeper catches *vision-level* drift (contradicting the RFC). It doesn't catch *integration-level* drift (building a feature that technically aligns with the RFC but doesn't connect to the system around it).

### 5. No semantic review of the gap between discovery and tasks

The task breakdown was technically reviewed by Shelly for complexity and story points. The review correctly identified that Phase 1 was underestimated and that three gap tasks (021-023) were missing. But the review evaluated whether the tasks *covered the work described in the discovery* — not whether the tasks *captured the integration assumptions implied by the discovery*.

The discovery says "autonomous-first learning system." The tasks say "TrainingSession orchestrator with callback-based I/O." These are not the same thing, and the gap between them is where the integration requirements live. No review step caught that gap.

---

## What we need to fix

The Vision-Keeper guardrail prevents the agent from contradicting the RFC. It doesn't prevent the agent from building features that are technically RFC-aligned but systemically disconnected. We need a second layer of protection for a different failure mode: **integration blindness**.

Integration blindness manifests when:

- A feature is built without wiring into existing capabilities (credentials, personas, URL config)
- A feature's tasks reference sections of the discovery that describe the mechanism but not the integration
- Acceptance criteria test internal behavior but not end-to-end usefulness
- The word "autonomous" appears in the title and nowhere in the delivered behavior
- All tests pass, `just all` passes, and the feature doesn't work

---

## Proposed process improvements

### 1. Add an "Integration Requirements" section to every discovery document

**Current state**: Norwood's discovery protocol includes "Existing Components & Reuse Plan" (what will be reused, what won't, why). This is good for code reuse but doesn't capture *how the feature integrates with the existing system at runtime*.

**Proposed addition**: A mandatory "Integration Requirements" section that maps:

```markdown
## Integration Requirements

### Data Flow
- What data does this feature need from existing components?
  (credentials, personas, URLs, playbooks, step history)
- What data does this feature produce that other components consume?
  (playbooks, step logs, confidence scores)

### Assumption Alignment
- What does the feature name imply about its behavior?
  ("autonomous" = agent-led, not human-led)
- What would a human assume this feature does if they only read the title?
- What integration points are so obvious a human wouldn't think to state them?
  (a login training feature needs login credentials)

### End-to-End Smoke Test
- Describe one concrete scenario that exercises the feature end-to-end.
  (example: "Train a Sproutful login flow using the researcher persona's
  stored credentials, starting from `soren train run sproutful --flow login`")
- If the feature cannot support this scenario, what's missing?
```

This section should be written by the discovery author (Norwood) and reviewed by Shelly for missing integration points before tasks are broken out.

### 2. Require outcome-based acceptance criteria on every task > 3 points

**Current state**: Acceptance criteria focus on mechanism ("state machine implemented", "callbacks work", "commands handled").

**Proposed rule**: Every task over 3 story points must include at least one acceptance criterion phrased as an outcome that a user would recognize:

```markdown
**Mechanism criteria (current, keep these):**
- [ ] Full state machine implemented
- [ ] Callback-based I/O for testability

**Outcome criteria (new, mandatory for tasks > 3 points):**
- [ ] A training session for source "sproutful" resolves credentials from
      the researcher persona's credential_ref
- [ ] A failed step's error context is available to the retry instruction
- [ ] Running `soren train run sproutful --flow login` from a cold start
      produces a saved login flow playbook without manual workarounds
```

Outcome criteria are not integration tests — they're acceptance tests phrased in terms of observable behavior. They catch the "all tests pass, feature doesn't work" gap because they force the implementer to think about what the feature actually does, not just how it's structured.

### 3. Add a "Discovery → Tasks" review gate using Shelly

**Current state**: Shelly reviews tasks for complexity and story points after they're written.

**Proposed expansion**: Before task implementation begins, Shelly reviews the complete task set against the discovery document for:

1. **Orphaned integration points**: Does the discovery describe an integration (e.g., Section 6.5 credential wiring) that no task references?
2. **Assumption drift**: Does any task title/description use a word ("autonomous", "cascading", "context-aware") that the task's acceptance criteria don't verify?
3. **Phase ordering coherence**: Does the implementation order match the discovery's recommended delivery? If not, why?
4. **Missing data flow**: For each task, what data enters and what data exits? Is the chain unbroken from feature input to feature output?

This review would have caught: "Task 014 implements the training state machine but no task wires credentials from the persona into the training session. Section 6.5 shows `_resolve_credential(source, persona)` in the login flow integration — which task implements this?"

### 4. Add a pre-implementation technical review using Zod

**Current state**: No pre-implementation review of task prompts.

**Proposed addition**: Before implementing any task over 3 story points, Zod reviews the task prompt against:

1. The discovery sections it references (are there sections it *should* reference but doesn't?)
2. The existing code it modifies (what existing functions/methods does it need to call?)
3. The integration points with adjacent tasks (does Task 014 need output from Task 009?)

This is a 5-minute review that catches "you're about to build TrainingSession without reading Section 6.5 which contains the credential integration code."

### 5. Add a post-phase "feature smoke test" checkpoint

**Current state**: Each task runs `just all` after completion. No end-to-end validation after a phase completes.

**Proposed addition**: After completing all tasks in a phase, run the end-to-end smoke test from the discovery's "Integration Requirements" section. Not a unit test — a real attempt to use the feature for its stated purpose.

For the training system, this would have been: "Run `soren train run sproutful --flow login` with the researcher persona configured. Can you complete a login training? If not, what's missing?"

This smoke test would have revealed immediately: no credentials, no auto-navigate, no cross-step context. It would have revealed these before the week of debugging, not after.

### 6. Create a "Discovery → Work Items" skill

**Current state**: Discovery (Norwood) and task breakdown (manual) and review (Shelly) happen as separate manual steps with context loss between them.

**Proposed skill**: A slash command or agent workflow that runs the full pipeline:

```
/plan <discovery-doc-path>

1. Norwood reads the discovery, verifies integration requirements section exists,
   inventories existing code, maps data flow
2. Task breakdown with integration-aware acceptance criteria
3. Shelly reviews tasks against discovery for orphaned integrations,
   assumption drift, phase ordering, data flow completeness
4. Zod reviews task prompts for missing section references and
   integration points with existing code
5. Output: reviewed task files with integration acceptance criteria
```

This compresses three separate steps (with context loss between each) into a single flow where each reviewer has the full discovery context.

### 7. Feature-level context hooks (complement to Vision-Keeper)

The Vision-Keeper hook ensures RFC invariants are visible on every prompt. A similar pattern can ensure feature-level context is visible when working on a feature's tasks:

```bash
#!/bin/bash
# scripts/claude-hooks/feature-context.sh
# Emits the current feature's integration requirements when
# the user is working on that feature's tasks.

FEATURE_DIR="tasks/autonomous-learning-system"
DISCOVERY="docs/discovery/autonomous-learning-system.md"

if [[ -d "$FEATURE_DIR" ]]; then
  # Extract Integration Requirements section from discovery
  sed -n '/## Integration Requirements/,/^## /p' "$DISCOVERY" | head -50
fi
```

This ensures that five sessions into implementation, the agent still sees "this feature needs persona credentials" and "autonomous means agent-led, not human-led" — the load-bearing assumptions that drop out of context.

---

## How this relates to the existing guardrail layers

The Vision-Keeper guardrail and this proposal address different failure modes:

| Failure mode | Example | Caught by |
|---|---|---|
| **Vision drift** | Proposing DOM automation on a vision-based project | Vision-Keeper (RFC/ADR audit) |
| **Integration blindness** | Building training without persona credentials | Integration review (Shelly/Zod gate) |
| **Assumption drift** | Calling it "autonomous" but building it human-guided | Outcome-based acceptance criteria |
| **Context loss** | Forgetting Section 6.5 exists by Session 5 | Feature-context hooks + section references in task prompts |

The Vision-Keeper is Layer 1 (vision alignment). The process improvements proposed here are Layer 2 (integration alignment). Together they cover: "is this feature consistent with the project's thesis?" AND "does this feature actually connect to the system it's joining?"

---

## Summary of changes

| Change | Where | Effort | Impact |
|---|---|---|---|
| Integration Requirements section in discoveries | Norwood protocol | Low | Catches missing data flow before tasks exist |
| Outcome-based acceptance criteria | Task template | Low | Catches "all tests pass, feature doesn't work" |
| Shelly discovery→tasks review gate | Pre-implementation | Medium | Catches orphaned integrations and assumption drift |
| Zod task prompt pre-review | Pre-implementation | Low | Catches missing section references |
| Post-phase feature smoke test | Post-implementation | Low | Catches end-to-end gaps before debugging week |
| `/plan` discovery→work-items skill | New skill | Medium | Eliminates context loss between discovery and tasks |
| Feature-context hooks | Hook script | Low | Keeps integration assumptions in context across sessions |

---

## Next steps

1. **Update the Norwood agent protocol** to require the Integration Requirements section in every discovery.
2. **Update the task template** in `tasks-workflow.md` to include outcome-based acceptance criteria for tasks > 3 points.
3. **Define the Shelly discovery→tasks review checkpoint** as a documented step in the workflow.
4. **Build the `/plan` skill** (or a multi-agent flow) that runs Norwood → task breakdown → Shelly review → Zod review in one pass.
5. **Write a feature-context hook** for the training system's remaining implementation work.
6. **Revisit the autonomous learning system tasks**: now that we know what was missed, audit the completed tasks for integration gaps and create new tasks to close them (credential wiring, cross-step context, retry-with-reasoning, autonomous-first mode).
