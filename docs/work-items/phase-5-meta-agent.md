# Phase 5: Meta-Agent Orchestration — Work Items

**Phase Goal**: The `SorenMetaAgent` ties all components into the full autonomous execution cycle — assess backlog, discover if empty, select highest-priority task, execute, apply retry policy, finalize, and reset.

**Estimated Phase Duration**: 2 weeks (Apr 28 – May 11, 2026)

**Total Story Points**: 14

**Exit Criteria (Phase-Level)**:
- `just begin-soren-cycle` runs autonomously from start to finish
- When backlog is empty, discovery runs first before task selection
- Highest-priority eligible task is selected and executed
- Failed tasks are re-queued with escalating backoff; tasks exceeding max retries moved to `dead_letter`
- Browser state is always reset after each cycle, regardless of task outcome
- Memory pressure check triggers model fallback when appropriate
- CycleReport captures complete operational metrics

---

## Dependency Graph

```
WI 5.2 (Priority Scoring) ──►
                               WI 5.1 (SorenMetaAgent Core)
WI 5.3 (Retry Policy)     ──►
```

**Parallel tracks**: WI 5.2 and WI 5.3 can be developed in parallel as standalone modules, then integrated into WI 5.1.

**Phase dependency**: Phases 1–4 must be complete (all infrastructure, execution, discovery, and backlog services).

---

## Work Items

### WI 5.1: [Orchestration] SorenMetaAgent Core — Complete Implementation

- **Estimated Points**: 8
- **Dependencies**: WI 5.2, WI 5.3, Phases 1–4 (all prior services)
- **Completed**: Yes

**User Story**: As a system operator, I want a single `just begin-soren-cycle` command that autonomously discovers tasks when needed, selects the best available task, executes it, handles failures gracefully, and resets the environment, so that I can run the agent with zero manual intervention per cycle.

**Description**: Implement the central `SorenMetaAgent` orchestration engine with two primary operations: `run_cycle()` (full lifecycle) and `refresh_tasks()` (discovery only). The meta-agent coordinates all downstream services: TaskBacklog, TaskDiscovery, TaskExecutor, VMManager, PlaybookService (stub for Phase 6), MemoryService (stub for Phase 6), and ReportWriter. The cycle flow follows the RFC's sequence diagram exactly: init → assess backlog → discover if empty → select highest-priority eligible task → execute → apply retry policy on failure → write playbook on success → update memory → write report → reset browser state. Critical invariant: browser state is ALWAYS reset after every cycle, regardless of task outcome. Task failure must never crash the cycle.

**Deliverables:**

1. **SorenMetaAgent** — `src/soren/services/meta_agent.py`
   - `SorenMetaAgent(settings, db, llm, vm, backlog, discovery, executor, persona_loader, identity_mgr)`: the central orchestrator
   - `async run_cycle() -> CycleReport`: full lifecycle execution
     1. **Init**: load personas, decrypt credentials, restore memory, check VM readiness, reset MemoryGuard
     2. **Assess**: `backlog.get_eligible_tasks()` — excludes `dead_letter` and future `next_eligible_at`
     3. **Discover** (if backlog empty): iterate personas × sources, run `discovery.discover()`, insert results into backlog
     4. **Select**: score eligible tasks via priority scorer, pick highest
     5. **Execute**: `executor.execute(task, persona, computer)` with step budget
     6. **Finalize success**: `backlog.mark_completed()`, trigger playbook write-back (stub), update memory (stub)
     7. **Finalize failure**: `backlog.mark_failed()` with retry policy (escalating backoff or dead-letter)
     8. **Always**: write CycleReport, reset browser state, check if VM recreation needed
   - `async refresh_tasks(persona_filter: str | None = None) -> DiscoverySummary`: discovery-only mode
   - Graceful error handling: try/except around each phase; errors logged and captured in report; browser reset runs in `finally` block

2. **CLI Integration**
   - `just begin-soren-cycle` → `soren cycle run`: execute a full cycle
   - Stdout progress output: "Assessing backlog...", "Discovering tasks...", "Executing task: {title}...", "Cycle complete."

3. **End-to-End Wiring**
   - Service initialization: construct all services from `SorenSettings`, wire dependencies
   - Persona iteration: for each enabled persona with enabled sources
   - VM lifecycle: `ensure_ready()` before execution, `reset_browser_state()` after, `recreate_vm()` when triggered
   - Memory pressure: `MemoryGuard.reset_cycle()` at cycle start; model selection flows through `LLMService`

4. **Test Suite** — minimum 14 tests (`tests/test_meta_agent.py`)
   - `run_cycle()` discovers when backlog is empty
   - `run_cycle()` skips discovery when backlog has eligible tasks
   - `run_cycle()` selects highest-priority eligible task
   - `run_cycle()` calls `executor.execute()` with correct task and persona
   - `run_cycle()` marks task completed on success
   - `run_cycle()` marks task failed with backoff on failure
   - `run_cycle()` promotes task to dead-letter at max retries
   - `run_cycle()` always resets browser state (even on error)
   - `run_cycle()` triggers VM recreation at N-cycle intervals
   - `run_cycle()` triggers VM recreation on contamination detection
   - `run_cycle()` writes CycleReport regardless of outcome
   - `run_cycle()` handles executor crash without crashing cycle
   - `run_cycle()` handles discovery failure gracefully (empty backlog → no execution)
   - `refresh_tasks()` runs discovery only (no execution)
   - @pytest.mark.e2e: full cycle with discovery fallback, execution, and report generation

**Acceptance Criteria:**
- [ ] `just begin-soren-cycle` runs autonomously from start to finish without manual intervention
- [ ] When backlog is empty, discovery runs before task selection
- [ ] When backlog has eligible tasks, discovery is skipped
- [ ] Highest-priority eligible task is selected (using WI 5.2 priority scorer)
- [ ] Failed tasks are handled by retry policy (WI 5.3): backoff or dead-letter
- [ ] Browser state is ALWAYS reset after every cycle (verified: reset runs in `finally` block)
- [ ] VM recreation triggered every N cycles or on contamination detection
- [ ] Task failure, discovery failure, or VM error does NOT crash the cycle
- [ ] CycleReport captures all operational metrics: timing, steps, model used, fallback triggered, memory metrics
- [ ] `refresh_tasks()` operates in discovery-only mode (no execution)
- [ ] Test suite includes minimum 14 tests, 100% coverage for `meta_agent.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Integration complexity**: This WI wires together every prior service. **Mitigation**: Each service has independent tests; meta-agent tests use mocks for all dependencies; one E2E test validates the full chain.
- 🟡 **Error recovery**: Unexpected exceptions in any phase must not prevent browser reset or report writing. **Mitigation**: Nested try/except with `finally` blocks; defensive coding pattern proven in long-running systems.
- 🟢 **Service initialization order**: Dependencies are well-defined and acyclic. **Mitigation**: Constructor injection; no circular dependencies.

**References:** RFC §SorenMetaAgent interface; RFC §Execution Cycle Sequence (Mermaid diagram); RFC §Operational Contracts (Environment Reset Invariants)

---

### WI 5.2: [Orchestration] Priority Scoring & Task Selection — Complete Implementation

- **Estimated Points**: 3
- **Dependencies**: Phase 1 (WI 1.2 Task model, WI 1.5 settings)
- **Completed**: Yes

**User Story**: As a system operator, I want tasks prioritized by a composite score that weighs reward, persona fit, historical success, and backlog age, so that the most valuable and achievable tasks are executed first.

**Description**: Implement the `TaskPriorityScorer` that computes a composite priority score for eligible tasks. The scoring formula combines four weighted factors: estimated reward (normalized), persona fit (trait alignment with task type), historical success rate (tasks of this type × source), and backlog age (older tasks score higher to prevent starvation). Weights are configurable in `settings.yaml` with sensible defaults. The scorer returns tasks ranked by composite score, with the highest-scoring task selected for execution.

**Deliverables:**

1. **TaskPriorityScorer** — `src/soren/services/priority_scorer.py`
   - `TaskPriorityScorer(settings: SorenSettings)`: configurable scorer
   - `score(task: Task, persona: Persona, history: list[Task] | None = None) -> float`: compute composite score (0.0-1.0)
   - Factor 1 — Reward: `estimated_reward / max_reward` (normalized across backlog)
   - Factor 2 — Persona fit: task type alignment with persona traits (e.g., survey → high conscientiousness benefits)
   - Factor 3 — Historical success: `completed_count / total_attempts` for this `(task_type, source_name)` pair; defaults to 0.5 when no history
   - Factor 4 — Backlog age: `hours_in_backlog / max_age_hours` (capped at 1.0)
   - Composite: `w_reward * reward + w_fit * fit + w_success * success + w_age * age`
   - Default weights: `reward: 0.3, persona_fit: 0.2, success_rate: 0.2, backlog_age: 0.3`
   - `rank(tasks: list[Task], persona: Persona, history: list[Task] | None = None) -> list[Task]`: sort by score descending

2. **Settings Integration**
   - `scheduler.priority_weights` in `settings.yaml`: `{reward: 0.3, persona_fit: 0.2, success_rate: 0.2, backlog_age: 0.3}`
   - `PriorityWeights` Pydantic model with validation (weights should sum to 1.0 ± tolerance)

3. **Test Suite** — minimum 10 tests (`tests/test_priority_scorer.py`)
   - High-reward task scores higher than low-reward task (reward weight dominant)
   - Older task scores higher than newer task (age weight active)
   - Task with high historical success rate scores higher
   - Persona fit boosts surveys for high-conscientiousness personas
   - Default weights produce sensible rankings for mixed task set
   - Custom weights override defaults
   - Weights that don't sum to 1.0 are normalized
   - Task with no history defaults to 0.5 success rate
   - `rank()` returns tasks in descending score order
   - Tied scores maintain stable sort order

**Acceptance Criteria:**
- [ ] Composite score combines all four factors with configurable weights
- [ ] Default weights produce intuitive rankings (high-reward + old tasks first)
- [ ] Weights are configurable via `settings.yaml` under `scheduler.priority_weights`
- [ ] Scoring handles missing data gracefully (no history → default 0.5, no reward → 0.0)
- [ ] `rank()` returns a deterministic ordering (stable sort for tied scores)
- [ ] Test suite includes minimum 10 tests, 100% coverage for `priority_scorer.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **Scoring formula tuning**: Default weights are starting points; real tuning happens after Phase 5 has production data. **Mitigation**: Configurable weights; log scores in CycleReport for analysis.
- 🟢 **Normalization edge cases**: Empty backlog, all-zero rewards. **Mitigation**: Guard against division by zero; default normalization when max is 0.

**References:** RFC §SorenMetaAgent (Task priority scoring); RFC §Resolved Decision 6 (Configurable priority weights)

---

### WI 5.3: [Orchestration] Retry Policy & Dead-Letter Management — Complete Implementation

- **Estimated Points**: 3
- **Dependencies**: Phase 1 (WI 1.5 settings with scheduler config)
- **Completed**: Yes

**User Story**: As a system operator, I want an escalating retry policy with dead-letter quarantine so that transient failures get retried with increasing backoff while permanently failing tasks are quarantined without blocking the backlog.

**Description**: Implement the `RetryPolicy` engine that manages task failure handling per the RFC's retry state machine. On failure, the policy increments `attempt_count`, computes the next eligible time from the backoff schedule `[5, 30, 120]` minutes, or promotes the task to `dead_letter` when `attempt_count >= max_retries`. The policy also integrates memory pressure checks before Ollama calls — when pressure is detected, the MemoryGuard downgrades to the fallback model for the remainder of the cycle.

**Deliverables:**

1. **RetryPolicy** — `src/soren/services/retry_policy.py`
   - `RetryPolicy(settings: SorenSettings)`: configurable retry engine
   - `compute_next_action(task: Task, failure_reason: str) -> RetryAction`: determine retry or dead-letter
   - `RetryAction` model: `action: "retry" | "dead_letter"`, `next_eligible_at: datetime | None`, `backoff_minutes: int | None`
   - Backoff schedule: `settings.scheduler.retry_backoff_minutes` (default `[5, 30, 120]`), indexed by `attempt_count`
   - When `attempt_count < max_retries`: return "retry" with `next_eligible_at = now + backoff_minutes[attempt_count]`
   - When `attempt_count >= max_retries`: return "dead_letter"
   - Backoff overflow: if `attempt_count` exceeds schedule length, use last value in schedule

2. **Error Classification** — within `retry_policy.py`
   - `classify_failure(error: Exception | str) -> str`: categorize failure reason
   - Categories: `timeout`, `vlm_error`, `login_failure`, `step_budget_exceeded`, `verification_failed`, `vm_error`, `unknown`
   - Classification stored in `task.last_failure_reason` for pattern analysis

3. **Test Suite** — minimum 10 tests (`tests/test_retry_policy.py`)
   - First failure (attempt 0) → retry with 5-minute backoff
   - Second failure (attempt 1) → retry with 30-minute backoff
   - Third failure (attempt 2) → retry with 120-minute backoff
   - Fourth failure (attempt 3, max_retries=3) → dead-letter
   - Custom `max_retries` setting honored
   - Custom `retry_backoff_minutes` schedule honored
   - Backoff overflow: attempt_count beyond schedule length uses last value
   - Error classification maps timeout correctly
   - Error classification maps VLM error correctly
   - Error classification defaults to "unknown" for unrecognized errors

**Acceptance Criteria:**
- [ ] Escalating backoff follows configured schedule `[5, 30, 120]` minutes
- [ ] Tasks promoted to `dead_letter` when `attempt_count >= max_retries`
- [ ] Backoff schedule and max retries configurable via `settings.yaml`
- [ ] `last_failure_reason` populated with classified error type
- [ ] Error classification covers all RFC-specified categories: timeout, vlm_error, login_failure, step_budget_exceeded, verification_failed
- [ ] `just list-tasks --status dead_letter` shows quarantined tasks with failure reasons
- [ ] Test suite includes minimum 10 tests, 100% coverage for `retry_policy.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **Retry logic correctness**: Simple arithmetic with well-defined state machine. **Mitigation**: Comprehensive test coverage of all transitions.
- 🟢 **Error classification accuracy**: Classification is heuristic — some errors won't map cleanly. **Mitigation**: "unknown" catch-all; refine categories based on production data.

**References:** RFC §Operational Contracts (Task Retry State Machine); RFC §Resolved Decision 10 (Poison-Task Retry Policy); RFC §Observability (Error classification)

---

## Summary

| WI | Title | Points | Dependencies | Track |
|----|-------|--------|-------------|-------|
| 5.1 | SorenMetaAgent Core Orchestration | 8 | 5.2, 5.3, Phases 1–4 | Integration |
| 5.2 | Priority Scoring & Task Selection | 3 | Phase 1 | Scoring |
| 5.3 | Retry Policy & Dead-Letter Management | 3 | Phase 1 | Retry |
| | **Total** | **14** | | |

### Velocity Assumptions

- **Solo developer**: 7 points/week. Lower velocity accounts for end-to-end integration testing and debugging across the full service stack.
- **Week 1 target** (6 pts): WI 5.2 (3) + WI 5.3 (3) — scoring and retry modules (parallelizable, no external dependencies)
- **Week 2 target** (8 pts): WI 5.1 (8) — meta-agent integration (depends on week 1 deliverables)

### Test Coverage Summary

| WI | Minimum Tests | Coverage Target |
|----|--------------|-----------------|
| 5.1 | 14 | 100% for `services/meta_agent.py` |
| 5.2 | 10 | 100% for `services/priority_scorer.py` |
| 5.3 | 10 | 100% for `services/retry_policy.py` |
| **Total** | **34** | |

### Exit Criteria Traceability

| Phase Exit Criterion | Verified By |
|---------------------|-------------|
| `just begin-soren-cycle` runs autonomously | WI 5.1 AC + E2E test |
| Empty backlog triggers discovery first | WI 5.1 test (discovers when empty) |
| Highest-priority task selected | WI 5.1 + WI 5.2 integration |
| Failed tasks re-queued with backoff | WI 5.3 AC |
| Dead-letter quarantine at max retries | WI 5.3 AC |
| Browser state always reset | WI 5.1 AC (finally block) |
| Memory pressure triggers fallback | WI 5.1 + Phase 3 WI 3.2 integration |
| CycleReport captures all metrics | WI 5.1 AC |
