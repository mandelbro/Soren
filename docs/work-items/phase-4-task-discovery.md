# Phase 4: Task Discovery — Work Items

**Phase Goal**: Vision-based task discovery from external services using a three-phase pipeline (UI-TARS navigates → Qwen3-VL extracts → Gemma3:12B structures), tiered deduplication, and backlog management.

**Estimated Phase Duration**: 2 weeks (Apr 14 – Apr 27, 2026)

**Total Story Points**: 20

**Exit Criteria (Phase-Level)**:
- `just refresh-tasks` boots a VM, navigates to a configured service, identifies available tasks, and inserts them into the SQLite backlog
- `just list-tasks` displays the backlog with persona, status, type, and limit filters
- Duplicate detection works: re-running discovery for the same service produces zero new inserts
- Three-phase extraction pipeline functional: UI-TARS navigation → Qwen3-VL extraction → Gemma3:12B structuring
- 55%+ discovery rate on at least one real service with evidence of improvement across 3+ test cycles

---

## Dependency Graph

```
WI 4.1 (TaskBacklog Service)
  └── WI 4.2 (Discovery Pipeline Engine) ──► WI 4.3 (SurveyPlatformAdapter)
                                           └── WI 4.4 (Email & Form Adapters)
```

**Sequential flow**: WI 4.1 provides the backlog persistence layer, WI 4.2 builds the pipeline engine, then WI 4.3 and WI 4.4 implement the concrete adapters (parallelizable after 4.2).

**Phase dependency**: Phase 3 must be complete (LLMService, TaskExecutor, CUA ComputerAgent).

---

## Work Items

### WI 4.1: [Backlog] TaskBacklog Service — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phase 1 (WI 1.3 database schema with `dedup_key` UNIQUE constraint)
- **Completed**: Yes

**User Story**: As a system operator, I want a TaskBacklog service that manages the full lifecycle of discovered tasks with tiered deduplication and status transitions, so that the meta-agent has a reliable task queue and operators can inspect, filter, and manage the backlog via CLI.

**Description**: Implement the `TaskBacklog` service providing full CRUD operations on the `tasks` table, status transitions per the RFC's retry state machine, and tiered deduplication. Primary dedup uses provider-native IDs when available; fallback uses content fingerprints (`sha256(task_type + source_url + normalized_title + normalized_snippet + stable_metadata)`). Insertion uses `INSERT ... ON CONFLICT(dedup_key) DO NOTHING` to silently skip duplicates. Implements five CLI commands for backlog management.

**Deliverables:**

1. **TaskBacklog Service** — `src/soren/services/task_backlog.py`
   - `TaskBacklog(db: DatabaseManager)`: manages the task queue
   - `async insert_discovered(tasks: list[DiscoveredTask]) -> InsertResult`: batch insert with dedup; returns counts (inserted, skipped, errors)
   - `async get_eligible_tasks(persona_id: str | None = None) -> list[Task]`: query `WHERE status = 'queued' AND (next_eligible_at IS NULL OR next_eligible_at <= datetime('now'))`, excludes `dead_letter`
   - `async mark_in_progress(task_id: str) -> None`: set `status = 'in_progress'`, `started_at = now()`
   - `async mark_completed(task_id: str, result_data: dict | None = None) -> None`: set `status = 'completed'`, `completed_at = now()`
   - `async mark_failed(task_id: str, reason: str, backoff_minutes: list[int]) -> None`: increment `attempt_count`, set `next_eligible_at` from backoff schedule, or promote to `dead_letter` if `attempt_count >= max_retries`
   - `async requeue_task(task_id: str) -> None`: reset dead-letter task: `status = 'queued'`, `attempt_count = 0`, clear `next_eligible_at`
   - `async get_summary() -> BacklogSummary`: counts by status, persona
   - `async clear_all() -> int`: delete all tasks, return count

2. **Deduplication Engine** — within `task_backlog.py`
   - `build_dedup_key(task) -> str`: tiered key generation
   - Primary: `{source_name}:{provider_task_id}:{persona_id}` when `provider_task_id` is present
   - Fallback: `{source_name}:sha256({task_type}+{source_url}+{normalized_title}+{normalized_snippet}+{stable_metadata}):{persona_id}`
   - `normalize_title(title: str) -> str`: lowercase, collapse whitespace, strip dynamic fragments (trailing counts like "5 left", "NEW" badges)
   - `normalize_snippet(text: str, max_chars: int = 160) -> str`: lowercase, collapse whitespace, truncate
   - SQL: `INSERT INTO tasks (...) VALUES (...) ON CONFLICT(dedup_key) DO NOTHING`

3. **CLI Integration**
   - `just list-tasks` → `soren tasks list`: display backlog table with columns (ID, title, status, persona, type, priority, attempts); filters: `--persona`, `--status`, `--type`, `--limit`
   - `just retry-failed` → `soren tasks retry-failed`: requeue all dead-letter tasks
   - `just retry-task <id>` → `soren tasks retry <id>`: requeue a specific dead-letter task
   - `just clear-backlog` → `soren tasks clear`: delete all tasks with confirmation prompt

4. **Test Suite** — minimum 16 tests (`tests/test_task_backlog.py`)
   - Insert single task with provider_task_id → dedup key uses native ID
   - Insert single task without provider_task_id → dedup key uses content fingerprint
   - Insert duplicate task (same dedup_key) → silently skipped, `InsertResult.skipped == 1`
   - Batch insert of 5 tasks with 2 duplicates → 3 inserted, 2 skipped
   - `get_eligible_tasks()` excludes `dead_letter` status
   - `get_eligible_tasks()` excludes tasks with future `next_eligible_at`
   - `get_eligible_tasks()` includes tasks with past or null `next_eligible_at`
   - `get_eligible_tasks()` filters by persona_id when provided
   - `mark_in_progress()` sets status and `started_at`
   - `mark_completed()` sets status, `completed_at`, and `result_data`
   - `mark_failed()` increments `attempt_count` and sets backoff
   - `mark_failed()` promotes to `dead_letter` when `attempt_count >= max_retries`
   - `requeue_task()` resets dead-letter task to queued
   - `normalize_title()` strips dynamic fragments and collapses whitespace
   - `normalize_snippet()` truncates at 160 chars
   - `get_summary()` returns correct counts per status

**Acceptance Criteria:**
- [ ] `INSERT ... ON CONFLICT(dedup_key) DO NOTHING` silently skips duplicates (no exceptions)
- [ ] Provider-native ID takes precedence over content fingerprint when available
- [ ] Content fingerprint is deterministic: same inputs always produce the same dedup key
- [ ] `get_eligible_tasks()` correctly excludes `dead_letter` and future `next_eligible_at` tasks
- [ ] `mark_failed()` applies escalating backoff from `retry_backoff_minutes: [5, 30, 120]` and promotes to dead-letter at max retries
- [ ] `requeue_task()` resurrects a dead-letter task (resets attempt_count, clears next_eligible_at)
- [ ] `just list-tasks --status dead_letter` shows quarantined tasks with last failure reason
- [ ] Test suite includes minimum 16 tests, 100% coverage for `task_backlog.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Fingerprint stability**: VLM extraction inconsistency may produce different fingerprints for the same task across discovery runs. **Mitigation**: Aggressive normalization; prefer provider-native IDs; log fingerprint inputs for debugging.
- 🟢 **SQLite ON CONFLICT**: Well-documented SQLite feature. No known issues with aiosqlite.

**References:** Discovery §Discovery Adapter Feasibility (Section 4: Dedup Stability); RFC §TaskDiscovery (Dedup); RFC §Operational Contracts (Task Retry State Machine); RFC §Data Model (tasks table)

---

### WI 4.2: [Discovery] DiscoveryAdapter Protocol & Pipeline Engine — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 4.1 (TaskBacklog for insertion), Phase 3 (LLMService, VMManager, ComputerAgent)
- **Completed**: Yes

**User Story**: As a developer, I want a DiscoveryAdapter protocol and three-phase pipeline engine that orchestrates navigation, extraction, and structuring across different model specialties, so that new service adapters can be added by implementing three prompt methods without touching the pipeline infrastructure.

**Description**: Implement the `DiscoveryAdapter` protocol defining the three-prompt interface (`navigation_prompt`, `extraction_prompt`, `structuring_prompt`) and the `TaskDiscovery` engine that orchestrates the three-phase pipeline. Phase 1 uses UI-TARS via ComputerAgent to navigate to the task listing page. Phase 2 uses Qwen3-VL to extract structured data from captured screenshots. Phase 3 uses Gemma3:12B to normalize raw extraction into `DiscoveredTask` JSON. Includes multi-screenshot capture with overlap for dashboards with 5+ items and the `DISCOVERY_READY` sentinel pattern for clean navigation-to-extraction handoff.

**Deliverables:**

1. **DiscoveryAdapter Protocol** — `src/soren/adapters/base.py`
   - `DiscoveryAdapter(Protocol)`:
     - `navigation_prompt(source: TaskSource, persona: Persona) -> str`: instructions for UI-TARS (~20-30 lines)
     - `extraction_prompt(source: TaskSource) -> str`: instructions for Qwen3-VL (~30-40 lines)
     - `structuring_prompt(source: TaskSource, raw_text: str) -> str`: instructions for Gemma3:12B (~20-30 lines)
     - `source_type: str` property
     - `expected_fields: list[str]` property

2. **TaskDiscovery Engine** — `src/soren/services/task_discovery.py`
   - `TaskDiscovery(settings, llm, vm, backlog)`: orchestrates the full discovery pipeline
   - `async discover(persona: Persona, source: TaskSource, computer: Computer) -> list[DiscoveredTask]`:
     - Phase 1 — Navigation: build ComputerAgent with adapter's `navigation_prompt()`, run until `DISCOVERY_READY` sentinel detected in agent output, capture screenshots
     - Phase 2 — Extraction: send screenshots to Qwen3-VL via `LLMService.generate()` with adapter's `extraction_prompt()`
     - Phase 3 — Structuring: send raw extraction text to Gemma3:12B via `LLMService.generate_structured()` with adapter's `structuring_prompt()`
   - `DISCOVERY_READY` sentinel detection: monitor agent output for the token; stop navigation phase
   - Multi-screenshot capture: scroll-based capture with 80% viewport overlap, similarity check to detect page bottom
   - Credential-based login: retrieve credentials from `IdentityManager`, pass to ComputerAgent as part of navigation instructions
   - Adapter registry: map `source.type` → adapter instance

3. **CLI Integration**
   - `just refresh-tasks` → `soren discover refresh`: run discovery for all enabled personas and sources; `--persona` filter for single persona

4. **Test Suite** — minimum 12 tests (`tests/test_task_discovery.py`)
   - DiscoveryAdapter protocol enforces all three prompt methods
   - Pipeline executes Phase 1 → Phase 2 → Phase 3 in order
   - Phase 1 stops on `DISCOVERY_READY` sentinel
   - Phase 1 captures screenshots during navigation
   - Multi-screenshot capture uses overlap and similarity check
   - Multi-screenshot capture stops when page bottom detected
   - Phase 2 sends screenshots with extraction prompt to Qwen3-VL
   - Phase 3 sends raw text with structuring prompt to Gemma3:12B
   - Phase 3 output parsed into list of DiscoveredTask dicts
   - Discovered tasks inserted into backlog via TaskBacklog
   - Adapter registry resolves correct adapter for source type
   - Discovery handles navigation failure gracefully (returns empty list, logs error)

**Acceptance Criteria:**
- [ ] Three-phase pipeline functional: UI-TARS → Qwen3-VL → Gemma3:12B with model swaps between phases
- [ ] `DISCOVERY_READY` sentinel stops navigation phase cleanly
- [ ] Multi-screenshot capture with overlap handles dashboards with 5+ items
- [ ] Discovered tasks flow through to `TaskBacklog.insert_discovered()` with dedup
- [ ] `just refresh-tasks` orchestrates discovery for all configured persona × source combinations
- [ ] `just refresh-tasks --persona researcher` filters to a single persona
- [ ] Navigation failure produces empty result and error log (no crash)
- [ ] Test suite includes minimum 12 tests, 100% coverage for `task_discovery.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Model swap overhead**: Three-phase pipeline requires two model swaps (~14s total). **Mitigation**: Acceptable latency for discovery; batch discovery across sources to amortize.
- 🟡 **DISCOVERY_READY detection**: Sentinel token must be in agent's training vocabulary and generated reliably. **Mitigation**: Explicit instruction in navigation prompt; fallback to step budget timeout.
- 🔴 **Login reliability**: First-login without playbook guidance succeeds ~47% of the time. **Mitigation**: Retry with exponential backoff; playbook recording after first success (Phase 6).

**References:** Discovery §Discovery Adapter Feasibility (Sections 2, 5, 7: Extraction Strategy, Multi-Item Parsing, Prompt Engineering); RFC §TaskDiscovery interface; RFC §DiscoveryAdapter protocol

---

### WI 4.3: [Adapter] SurveyPlatformAdapter — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 4.2 (pipeline engine)
- **Completed**: Yes

**User Story**: As a system operator, I want a survey platform discovery adapter that finds available surveys with their titles, durations, and rewards, so that the agent can autonomously populate the backlog with survey tasks from services like Beta Panel.

**Description**: Implement the `SurveyPlatformAdapter` with three prompts totaling ~80 lines. The navigation prompt guides UI-TARS to reach the survey dashboard, handle login, dismiss banners, and scroll to load all items. The extraction prompt instructs Qwen3-VL to enumerate every visible survey with title, duration, reward, and provider ID. The structuring prompt has Gemma3:12B convert raw text to JSON with unit parsing (duration → minutes, reward → numeric). Includes provider-native ID extraction (survey IDs visible in dashboard tables) for primary dedup.

**Deliverables:**

1. **SurveyPlatformAdapter** — `src/soren/adapters/survey_platform.py`
   - Implements `DiscoveryAdapter` protocol
   - `source_type = "survey_platform"`
   - `expected_fields = ["title", "estimated_duration_minutes", "estimated_reward", "reward_currency", "provider_task_id"]`
   - `navigation_prompt()`: ~25 lines — login, navigate to dashboard, scroll to load all, `DISCOVERY_READY` sentinel, error recovery (CAPTCHA detection, 2FA detection, cookie banners)
   - `extraction_prompt()`: ~35 lines — enumerate all surveys with fields (title, duration, reward, provider_task_id, status, additional_info), numbered list format, explicit "Total surveys found: N" count, multi-screenshot handling instructions
   - `structuring_prompt()`: ~20 lines — JSON array conversion with schema, duration string parsing (minutes), reward string parsing (numeric + currency), null for unknown/unclear fields

2. **Provider ID Extraction**
   - Survey platforms typically display IDs in dashboard tables or URLs
   - Extraction prompt explicitly asks for "Any visible survey ID, reference number, or unique code"
   - When provider ID available: dedup key uses native ID path
   - When not available: fallback to content fingerprint

3. **Test Suite** — minimum 10 tests (`tests/test_survey_adapter.py`)
   - Adapter implements all three DiscoveryAdapter protocol methods
   - `navigation_prompt()` includes login instructions
   - `navigation_prompt()` includes scroll instructions
   - `navigation_prompt()` includes `DISCOVERY_READY` sentinel
   - `navigation_prompt()` includes CAPTCHA and 2FA detection
   - `extraction_prompt()` requests all expected fields
   - `extraction_prompt()` includes multi-screenshot dedup instructions
   - `extraction_prompt()` requests total count
   - `structuring_prompt()` defines correct JSON schema
   - `structuring_prompt()` includes duration and reward parsing rules
   - @pytest.mark.integration: full discovery against a real survey platform (manual verification of output quality)

**Acceptance Criteria:**
- [ ] Adapter implements `DiscoveryAdapter` protocol completely
- [ ] Navigation prompt handles login, scrolling, cookie banners, CAPTCHA detection, and 2FA detection
- [ ] Extraction prompt handles multi-screenshot scenarios with overlap dedup instructions
- [ ] Structuring prompt produces valid JSON array with correct field types
- [ ] Duration strings parsed to minutes (e.g., "15 min" → 15, "1 hour" → 60)
- [ ] Reward strings parsed to numeric values with currency (e.g., "$2.50" → 2.50 USD)
- [ ] Provider-native ID extracted when visible in dashboard
- [ ] Test suite includes minimum 10 tests, 100% coverage for `survey_platform.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Prompt iteration**: Survey platform layouts vary; initial prompts may need 10-15 refinement rounds. **Mitigation**: Budget 3-4 days for prompt iteration against a real service.
- 🟡 **Multi-item enumeration**: VLMs miss items on dense dashboards (10+ items). **Mitigation**: Multi-screenshot capture with overlap; verification pass adds ~5-10% recall.
- 🟢 **Adapter size**: ~80 lines across three prompts matches RFC estimate. Well-scoped.

**References:** Discovery §Discovery Adapter Feasibility (Section 3: SurveyPlatformAdapter design); RFC §TaskDiscovery (Built-in adapters)

---

### WI 4.4: [Adapter] EmailAdapter & GenericFormAdapter — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 4.2 (pipeline engine)
- **Completed**: Yes

**User Story**: As a system operator, I want email and generic form discovery adapters so that the agent can find actionable emails requiring response and identify fillable forms, expanding the range of discoverable task types beyond surveys.

**Description**: Implement two additional adapters. The `EmailAdapter` navigates to an email inbox, identifies unread emails requiring response (filtering out newsletters and notifications), and extracts sender, subject, preview, and timestamp. The `GenericFormAdapter` navigates to a specific URL and identifies whether a fillable form exists, extracting field labels, types, and requirements. Both follow the three-phase pipeline established by the SurveyPlatformAdapter.

**Deliverables:**

1. **EmailAdapter** — `src/soren/adapters/email_adapter.py`
   - Implements `DiscoveryAdapter` protocol
   - `source_type = "email"`
   - `navigation_prompt()`: ~20 lines — login to email provider, navigate to inbox, filter/sort by unread if available, scroll to see recent messages, `DISCOVERY_READY`
   - `extraction_prompt()`: ~30 lines — identify unread emails needing response (bold subjects, unread indicators), extract sender/subject/preview/timestamp, filter out promotional and automated emails, `requires_response` assessment
   - `structuring_prompt()`: ~20 lines — JSON array with email task fields, timestamp parsing
   - Provider ID: email message-ID from URL or detail view (adapters should always extract native ID for email)

2. **GenericFormAdapter** — `src/soren/adapters/generic_form.py`
   - Implements `DiscoveryAdapter` protocol
   - `source_type = "generic_form"`
   - `navigation_prompt()`: ~15 lines — navigate to target URL, identify form presence, scroll to see all fields, `DISCOVERY_READY`
   - `extraction_prompt()`: ~25 lines — identify form type, extract field labels/types/required status/options, submit button text
   - `structuring_prompt()`: ~15 lines — JSON with form metadata and field array
   - Dedup: URL-based (forms at fixed URLs rarely change)

3. **Test Suite** — minimum 10 tests (`tests/test_email_adapter.py`, `tests/test_form_adapter.py`)
   - EmailAdapter implements DiscoveryAdapter protocol
   - Email navigation prompt includes inbox navigation and unread filtering
   - Email extraction prompt filters out promotional emails
   - Email extraction prompt requests `requires_response` assessment
   - Email structuring prompt produces correct JSON schema
   - GenericFormAdapter implements DiscoveryAdapter protocol
   - Form navigation prompt handles login-gated forms
   - Form extraction prompt identifies field types and required status
   - Form structuring prompt produces correct JSON schema
   - Both adapters include `DISCOVERY_READY` sentinel in navigation prompt

**Acceptance Criteria:**
- [ ] Both adapters implement `DiscoveryAdapter` protocol completely
- [ ] Email adapter filters actionable emails from newsletters/notifications
- [ ] Email adapter extracts provider-native message-ID when available
- [ ] Form adapter identifies form presence, field structure, and submit button
- [ ] Form adapter dedup works via URL-based key (stable across page loads)
- [ ] Both adapters include CAPTCHA detection and 2FA handling in navigation prompts
- [ ] Test suite includes minimum 10 tests, 100% coverage for both adapter files
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Email inbox variability**: Webmail providers have vastly different layouts. **Mitigation**: Start with one provider; add provider-specific prompt variations in the extraction prompt.
- 🟢 **Generic form simplicity**: Forms at known URLs are the simplest discovery target. Low risk.
- 🟡 **Email message-ID extraction**: Not always visible in inbox view. **Mitigation**: Fall back to content fingerprint (sender + subject + timestamp).

**References:** Discovery §Discovery Adapter Feasibility (Section 3: EmailAdapter, GenericFormAdapter designs); RFC §TaskDiscovery (Built-in adapters)

---

## Summary

| WI | Title | Points | Dependencies | Track |
|----|-------|--------|-------------|-------|
| 4.1 | TaskBacklog Service | 5 | Phase 1 | Backlog |
| 4.2 | DiscoveryAdapter Protocol & Pipeline Engine | 5 | 4.1, Phase 3 | Pipeline |
| 4.3 | SurveyPlatformAdapter | 5 | 4.2 | Adapters |
| 4.4 | EmailAdapter & GenericFormAdapter | 5 | 4.2 | Adapters |
| | **Total** | **20** | | |

### Velocity Assumptions

- **Solo developer**: 10 points/week. Prompt iteration against real services is the primary time sink — budget 30-45 minutes per iteration round.
- **Week 1 target** (10 pts): WI 4.1 (5) + WI 4.2 (5) — backlog service and pipeline engine
- **Week 2 target** (10 pts): WI 4.3 (5) + WI 4.4 (5) — all three adapters (4.3 and 4.4 parallelizable)

### Test Coverage Summary

| WI | Minimum Tests | Coverage Target |
|----|--------------|-----------------|
| 4.1 | 16 | 100% for `services/task_backlog.py` |
| 4.2 | 12 | 100% for `services/task_discovery.py`, `adapters/base.py` |
| 4.3 | 10 | 100% for `adapters/survey_platform.py` |
| 4.4 | 10 | 100% for `adapters/email_adapter.py`, `adapters/generic_form.py` |
| **Total** | **48** | |

### Exit Criteria Traceability

| Phase Exit Criterion | Verified By |
|---------------------|-------------|
| `just refresh-tasks` discovers and inserts tasks | WI 4.2 + 4.3 integration test |
| `just list-tasks` displays backlog with filters | WI 4.1 AC |
| Duplicate detection works (zero new inserts on re-run) | WI 4.1 dedup tests |
| Three-phase pipeline functional | WI 4.2 AC |
| 55%+ discovery rate on one real service | WI 4.3 integration test |
