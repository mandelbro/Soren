# Soren RFC Review Feedback

Review target: `soren/docs/rfc.md`  
Protocol: principal engineer RFC review

---

## Findings

### Critical (Must Address)

1. **Task deduplication key will drop legitimate tasks on shared dashboards**
   - Current dedup uses `(source_url, persona_id)`, which collapses distinct tasks from the same page (common for survey/email/form queues), causing silent backlog data loss.
   - Evidence:
     - Discovery adapters explicitly target multi-item pages.
     - Dedup key does not include task identity fields.

2. **Core reset strategy is internally inconsistent (snapshot-first vs browser-cleanup fallback)**
   - Main execution flow and performance assumptions are snapshot-oriented, but the open question resolution shifts to browser-level cleanup.
   - These approaches provide different isolation guarantees, contamination risk profiles, and cycle timings.

3. **No poison-task policy (retry caps/quarantine/backoff by task)**
   - `attempt_count` exists, but RFC does not define scheduler behavior for repeated failures.
   - A single high-priority flaky task can consume cycles repeatedly without progress.

### Important (Should Address)

1. **Security wording overstates credential path guarantees**
   - The claim that credentials never appear in DOM/HTTP is not strictly accurate when using a real browser login flow.
   - Better to scope guarantee to automation logs/APIs and define practical leakage controls.

2. **Memory budget plan is contradictory without runtime admission control**
   - One section states two models loaded at once, while another sums near-32GB total memory.
   - Requires explicit model residency policy, thresholds, and fallback logic to avoid OOM behavior.

3. **Roadmap ambiguity around cloud fallback**
   - Non-goals say no cloud fallback in v1, but trade-off text says planned for "Phase 3" (ambiguous with current v1 phase numbering).

4. **Answered open questions are not fully propagated into normative sections**
   - Decisions like per-service rate limits and compatibility discovery are answered but not consistently reflected in architecture/config/interfaces.

### Suggestions (Consider)

- Add an explicit **Operational Contracts** section:
  - Retry state machine
  - Dead-letter/quarantine semantics
  - Cleanup invariants
  - Model admission/load policy
- Add a failure-mode test matrix:
  - VM boot failure
  - VNC/stale screenshot issues
  - VLM drift and verification mismatches
  - Auth challenge and partial submission handling

---

## Assessment Summary

- **Verdict:** Request Changes
- **Complexity Rating:** Complex
- **Confidence Level:** High

## What Works Well

- Strong decomposition between discovery, execution, orchestration, VM, and storage layers.
- Good prior-art reuse strategy from `docs/desktop-agent.md`, reducing integration risk.
- Pragmatic v1 constraints keep implementation scope realistic.
- Testing and observability foundations are present and reasonably structured.

## Questions Requiring Answers

1. What is the canonical reset contract in v1: snapshot revert, browser cleanup, or hybrid?
2. What is the definitive dedup identity model for discovered tasks?
3. What scheduler behavior applies to repeatedly failing tasks (max retries, cooldown, dead-letter)?
4. What hard memory thresholds trigger model fallback/downshift and where is it enforced?

## Alternative Approaches

1. **Hybrid reset strategy**
   - Browser cleanup each cycle + full VM rebuild every N cycles (or on contamination signals).
2. **Dedup by source-native ID + fallback hash**
   - Prefer provider task IDs; otherwise hash normalized task payload fields.
3. **Expanded task failure state machine**
   - `queued -> in_progress -> failed_retryable | failed_terminal | dead_letter`.

## Recommended Next Steps

1. Normalize reset strategy and guarantees across sequence, security, and performance sections.
2. Replace dedup definition with robust task identity and collision-resistant fallback behavior.
3. Add explicit retry/backoff/dead-letter policy and CLI/report visibility for those states.
4. Convert memory planning into enforceable runtime policy (admission control + fallback model).
5. Propagate resolved open-question decisions into architecture, config schema, and phase deliverables.

---

## Context Check

- [x] I understand the business objective
- [x] I understand the technical scope
- [x] I identified affected systems
- [x] I noted blind spots requiring validation (notably Lume snapshot behavior in target runtime)
