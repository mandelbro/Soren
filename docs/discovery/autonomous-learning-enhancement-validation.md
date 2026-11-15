# Validation Report: Autonomous Learning System Enhancement

**Date:** 2026-04-08
**Validator:** Norwood (discovery validation, /plan step 1)
**Document:** `docs/discovery/autonomous-learning-system-enhancement.md`

---

## 1. Required Sections Check

- [x] **Existing Components & Reuse Plan** -- Present (Section 2, thorough: reuse, won't-reuse, consolidation)
- [ ] **Integration Requirements** -- MISSING. No dedicated section mapping data-in/data-out per phase, no assumption alignment block, no end-to-end smoke test. This is the exact gap the retrospective called out.
- [ ] **End-to-end smoke test scenario** -- MISSING. No concrete "run this command, expect this outcome" scenario. The retrospective specifically mandated this.

**Verdict:** 1 of 3 required sections present. The two missing sections are the ones the retrospective identified as the root cause of the original integration failure.

---

## 2. Integration Gaps

1. **Credential flow into training is described but no smoke test verifies it.** Phase 0 wires CredentialStore, but no acceptance scenario says "run `soren train run sproutful --flow login` and confirm the agent types the actual password."
2. **PersonaContext.build() injection path is hand-waved.** Phase 1 task 1.3 says "add persona context and playbook content to persistent agent instructions" but does not specify which PersonaContext parameters change or how credential handoff feeds through.
3. **Service model -> chrome crop -> VNC patch data flow is fragile.** Phase 3 relies on setting `os.environ["SOREN_CHROME_CROP_Y"]` in-process before CUA starts. No task verifies that the env-var is set before the first screenshot capture in a training session.
4. **Application context (Phase 4) -> PersonaContext.build() -> TaskExecutor.execute() chain is unverified.** Phase 4 task 4.5 says "inject into PersonaContext.build()" but no task tests the round-trip from training to autonomous execution using the generated context.

---

## 3. Assumption Alignment

**FAIL.**

- **Title vs. delivery:** The title is "Autonomous Learning System Enhancement." Four of five phases (0, 1, 2, 3) improve the *human-led training path*. Phase 4 improves playbook richness for autonomous execution but does not implement any autonomous behavior. The enhancement fixes training mode -- it does not enhance the autonomous learning system.
- **Original vision gap:** The original discovery Section 5 specifies an autonomous-first loop: try autonomously -> stuck detection -> auto-generate playbook -> fall back to training. The enhancement does not touch `StuckDetector`, `auto_playbook.py`, `execute_with_learning()`, or the meta-agent's autonomous-first flow. All five of its phases improve the training fallback path.
- **Word audit:** "Autonomous" appears 7 times. Every occurrence describes the *downstream consumer* of training output ("execute autonomously", "autonomous executor receives"). None describes autonomous behavior being implemented. The enhancement makes training produce better playbooks *for* autonomous execution, but does not implement or improve autonomous execution itself.

---

## 4. Orphaned Integration Points from Original Discovery

These were specified in the original but are NOT addressed by the enhancement:

1. **StuckDetector with reasoning** (Section 5) -- Exists at `src/soren/services/stuck_detector.py` but the enhancement does not reference or improve it. The retrospective noted retry-with-reasoning was missing; still missing.
2. **Auto-playbook generation from successful runs** (Section 5) -- Exists at `src/soren/services/auto_playbook.py` but is not referenced. The autonomous-first path that generates playbooks without human training is not enhanced.
3. **Confidence tracking across runs** (Section 5) -- Fields exist on `FlowPlaybook` model (`confidence`, `consecutive_successes`) but no enhancement phase updates or exercises them.
4. **Host playbook auto-learning** (Section 5) -- Not addressed. The enhancement's Phase 4 enriches site playbooks but does not touch host playbook generation.
5. **Tab-aware execution** (Section 8, Phase 4) -- Already implemented in `TaskExecutor` with `VMManager` tab methods. The enhancement's Phase 3 adds window maximization but does not connect to the existing tab-transition handler.
6. **Credential resolution via `_resolve_credential(source, persona)`** (Section 6.5) -- Phase 0 addresses this for training, but the shared utility proposed (task 0.2) does not appear in any task's acceptance criteria as tested end-to-end.

---

## 5. Codebase Accuracy

- **TrainingSession.__init__ (cited as lines 219-241):** Actual location is lines 219-241. ACCURATE.
- **_execute_single_step (cited as line 778-899):** Actual definition at line 778. ACCURATE.
- **ComputerAgent creation (cited as line 829-835):** Actual at lines 829-835. ACCURATE.
- **_SINGLE_STEP_BUDGET = 10 (cited as line 49):** Actual at line 49. ACCURATE.
- **_build_credential_handoff (cited as lines 426-466):** Actual at lines 426-466. ACCURATE.
- **_scale_xy (cited as lines 289-303):** Actual at line 289. ACCURATE.
- **`maximize_active_window`:** Does NOT exist yet. Correctly identified as new work.
- **`train_run` (cited as lines 247-307):** Actual at lines 239-307. CLOSE ENOUGH (function def at 239, body at 250).

All codebase references are current and accurate.

---

## 6. Recommendation

**Do not generate tasks as-is. The discovery must be revised first.**

Specific revisions required before task generation:

1. **Add an Integration Requirements section** with data-flow-in, data-flow-out, and assumption alignment -- the exact format specified in `docs/process/closing-the-integration-gap.md` Section 1.
2. **Add at least one end-to-end smoke test scenario** (e.g., "Train a Sproutful login, then run `soren cycle run` and confirm the agent uses the trained playbook with resolved credentials to log in without human intervention").
3. **Rename or re-scope.** Either (a) rename to "Training Mode Integration Fixes" to match what the phases actually deliver, or (b) add phases that actually enhance autonomous behavior (StuckDetector improvements, auto-playbook refinement, confidence-driven verification reduction).
4. **Address orphaned integrations** from items 1-5 above, even if only to explicitly declare them out-of-scope with rationale.
