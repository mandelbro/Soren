# Phase 6: Memory & Playbooks — Work Items

**Phase Goal**: Persistent memory across cycles with importance-scored recall, and self-improving service playbooks that accumulate step-by-step instructions from successful executions.

**Estimated Phase Duration**: 2 weeks (May 12 – May 25, 2026)

**Total Story Points**: 10

**Exit Criteria (Phase-Level)**:
- After a successful task execution, the reasoning LLM generates playbook instructions and appends them to the service's markdown file
- On subsequent cycles, playbook content is loaded into the agent prompt
- Memory entries persist across cycles and are pruned when below importance threshold
- Human-edited playbook content is preserved during LLM write-back
- `just list-playbooks` and `just show-playbook <service>` display playbook information
- `--dry-run` flag previews playbook changes without writing

---

## Dependency Graph

```
WI 6.1 (MemoryService) ──►  (integrated into TaskExecutor prompt in Phase 5)
WI 6.2 (PlaybookService) ──► (integrated into meta-agent finalization in Phase 5)
```

**Parallel tracks**: WI 6.1 and WI 6.2 are independent and can be developed in parallel, then integrated into the existing meta-agent orchestration.

**Phase dependency**: Phase 5 must be complete (meta-agent triggers write-back after execution; executor uses memory in prompts).

---

## Work Items

### WI 6.1: [Memory] MemoryService — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phase 1 (WI 1.3 database schema with `memory_entries` table)
- **Completed**: Yes

**User Story**: As a system operator, I want the agent to remember context from previous cycles — what worked, what failed, service-specific quirks — so that task execution improves over time through accumulated experience rather than starting fresh every cycle.

**Description**: Implement the `MemoryService` providing `store`, `recall`, and `prune` operations against the `memory_entries` table. Memories are organized in three tiers: session (in-memory only, discarded after the current task), cycle (persisted, retained for 30 days), and long-term (indefinite retention, importance-scored). Recall queries match by key, entry type, and recency. Importance scoring combines explicit assignment with access-frequency decay. Pruning runs at cycle end, removing low-importance expired entries. The recall output integrates into the TaskExecutor's prompt construction (activating the stub from WI 3.3).

**Deliverables:**

1. **MemoryService** — `src/soren/services/memory_service.py`
   - `MemoryService(db: DatabaseManager)`: manages the memory_entries table
   - `async store(key: str, value: dict, entry_type: str = "cycle", importance: float = 0.5, expires_at: str | None = None) -> str`: insert or update a memory entry, return entry ID
   - `async recall(key: str | None = None, entry_type: str | None = None, limit: int = 10, min_importance: float = 0.0) -> list[MemoryEntry]`: query memories matching criteria, ordered by importance × recency
   - `async prune(min_importance: float = 0.3) -> int`: delete expired entries and entries below importance threshold, return count deleted
   - `async increment_access(entry_id: str) -> None`: bump `access_count` and `last_accessed_at` for recall tracking
   - Three-tier memory management:
     - **Session**: in-memory dict, never persisted, cleared after task completion
     - **Cycle**: persisted to DB with `expires_at = now + 30 days`
     - **Long-term**: persisted with `expires_at = NULL` (never auto-expires), importance-scored
   - Importance decay: `effective_importance = importance * (1.0 - age_factor)` where `age_factor` increases with days since last access

2. **Prompt Integration** — updates to `src/soren/services/persona_context.py`
   - `PersonaContext.build()` now accepts recalled memories and formats them as context
   - Memory retrieval: recall by task type, source name, and persona ID
   - Format: "From previous experience: {key}: {value}" for each relevant memory
   - Limit to top 5 most relevant memories to avoid prompt bloat

3. **Test Suite** — minimum 14 tests (`tests/test_memory_service.py`)
   - `store()` creates a new memory entry in the database
   - `store()` with existing key updates the entry
   - `recall()` by key returns matching entries
   - `recall()` by entry_type filters correctly
   - `recall()` respects `min_importance` threshold
   - `recall()` orders by importance × recency (most relevant first)
   - `recall()` respects `limit` parameter
   - `prune()` removes entries below importance threshold
   - `prune()` removes expired entries (past `expires_at`)
   - `prune()` preserves entries above threshold and not expired
   - `prune()` preserves long-term entries with `expires_at = NULL`
   - `increment_access()` updates `access_count` and `last_accessed_at`
   - Session memory: stored in-memory, not persisted to DB
   - Cycle memory: stored with 30-day expiration

**Acceptance Criteria:**
- [ ] `store()` persists memory entries to SQLite with correct tier-based expiration
- [ ] `recall()` retrieves relevant memories filtered by key, type, and importance
- [ ] `prune()` removes expired and low-importance entries without affecting long-term memories
- [ ] Three-tier memory management: session (in-memory), cycle (30-day), long-term (indefinite)
- [ ] Recalled memories integrated into TaskExecutor prompt via PersonaContext.build()
- [ ] Top 5 most relevant memories included in prompt (no bloat)
- [ ] Importance decay reduces effective importance of stale, unaccessed entries
- [ ] Test suite includes minimum 14 tests, 100% coverage for `memory_service.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **SQLite performance**: Memory queries are simple key-based lookups with small result sets. No performance concerns.
- 🟡 **Memory relevance**: Recalling the "right" memories for a given task depends on key naming conventions. **Mitigation**: Use structured keys (`{source_name}:{memory_type}:{topic}`) established in documentation; iterate based on task execution quality.
- 🟢 **Importance scoring**: Simple formula with configurable thresholds. Easy to tune.

**References:** RFC §MemoryService interface (store, recall, prune); RFC §Data Model (memory_entries table); RFC §Cross-cutting Concerns (Performance — DB growth < 50 MB/month)

---

### WI 6.2: [Playbooks] PlaybookService & Write-Back — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phase 3 (WI 3.2 LLMService for summarization), Phase 5 (meta-agent triggers write-back)
- **Completed**: No

**User Story**: As a system operator, I want the agent to automatically generate and refine per-service playbooks from successful task executions, so that subsequent cycles benefit from accumulated knowledge about how to navigate, log in, and complete tasks on each service.

**Description**: Implement the `PlaybookService` providing `load`, `get_relevant_sections`, `write_back`, and `list_playbooks` operations. Playbooks are per-service markdown files in `data/playbooks/`. Write-back uses the LLM (Gemma3:12B) to summarize task execution steps into reusable instructions, preserving any human-edited content. Each generated section is tagged with `<!-- soren-generated: {timestamp}, task: {task_id} -->` markers. The `--dry-run` flag on the cycle command previews changes without writing. Implements `just list-playbooks` and `just show-playbook <service>` CLI commands.

**Deliverables:**

1. **PlaybookService** — `src/soren/services/playbook_service.py`
   - `PlaybookService(settings: SorenSettings, llm: LLMService)`: manages playbook lifecycle
   - `load(service_name: str) -> str | None`: read `data/playbooks/{service_name}.md`, return content or None if not exists
   - `get_relevant_sections(content: str, task_type: str) -> str`: extract sections relevant to the task type (e.g., "## Login", "## Survey Completion")
   - `async write_back(service_name: str, task: Task, step_log: list[TaskStep], dry_run: bool = False) -> PlaybookUpdate`: LLM summarizes steps, appends/updates sections
   - `list_playbooks() -> list[PlaybookInfo]`: enumerate all playbook files with metadata (service name, file size, last modified, section count)

2. **Write-Back Engine** — within `playbook_service.py`
   - LLM summarization: send step log to Gemma3:12B with prompt to extract reusable instructions
   - Section management: identify existing sections, merge new content, preserve human edits
   - Human-edit preservation: content NOT enclosed in `<!-- soren-generated -->` markers is never modified or deleted
   - Generated content tagged: `<!-- soren-generated: {ISO-8601 timestamp}, task: {task_id} -->`
   - Conflict resolution: if a generated section already exists, update it (replace between markers); human content outside markers untouched
   - `PlaybookUpdate` model: `service_name`, `sections_added: int`, `sections_updated: int`, `dry_run: bool`, `preview: str | None`

3. **Prompt Integration** — updates to `src/soren/services/persona_context.py`
   - `PersonaContext.build()` now loads playbook content for the task's service and includes relevant sections in the agent instructions
   - Playbook content appears after persona context but before step budget notice

4. **CLI Integration**
   - `just list-playbooks` → `soren playbooks list`: table of all playbooks (service name, file size, last modified, section count)
   - `just show-playbook <service>` → `soren playbooks show <service>`: print playbook content with syntax highlighting
   - `--dry-run` flag on `soren cycle run`: preview playbook write-back without writing to disk

5. **Test Suite** — minimum 14 tests (`tests/test_playbook_service.py`)
   - `load()` returns content for existing playbook file
   - `load()` returns None for non-existent playbook
   - `get_relevant_sections()` extracts Login section for login-related tasks
   - `get_relevant_sections()` extracts Survey Completion section for survey tasks
   - `get_relevant_sections()` returns full content when no section headers match
   - `write_back()` generates new playbook file when none exists
   - `write_back()` appends new section to existing playbook
   - `write_back()` updates existing generated section (replaces between markers)
   - `write_back()` preserves human-edited content outside markers
   - `write_back()` tags generated content with `<!-- soren-generated -->` marker
   - `write_back()` with `dry_run=True` returns preview without writing file
   - `list_playbooks()` returns metadata for all playbook files
   - LLM summarization prompt produces reusable step-by-step instructions
   - Playbook content integrated into PersonaContext.build() output

**Acceptance Criteria:**
- [ ] `load()` reads playbook content for a service; returns None if not exists
- [ ] `write_back()` uses Gemma3:12B to summarize execution steps into reusable instructions
- [ ] Generated sections tagged with `<!-- soren-generated: {timestamp}, task: {task_id} -->` markers
- [ ] Human-edited content (outside markers) is NEVER modified during write-back
- [ ] Existing generated sections are updated in-place (not duplicated)
- [ ] `--dry-run` previews changes without writing to disk
- [ ] `just list-playbooks` displays all playbooks with metadata
- [ ] `just show-playbook <service>` prints playbook content
- [ ] Playbook content loaded into agent prompt via PersonaContext.build()
- [ ] Test suite includes minimum 14 tests, 100% coverage for `playbook_service.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **LLM summarization quality**: Gemma3:12B must produce actionable, step-by-step instructions from raw step logs. **Mitigation**: Structured summarization prompt with explicit format requirements; review first few generated playbooks manually.
- 🟡 **Human-edit preservation**: Parsing markdown to distinguish human vs generated content requires reliable marker detection. **Mitigation**: Use HTML comments as markers (invisible in rendered markdown, easy to regex match); test extensively with mixed content.
- 🟢 **File I/O**: Simple markdown file read/write. No concurrency concerns (single-user system).

**References:** RFC §PlaybookService interface; RFC §Trade-offs: Playbook Format (Markdown vs JSON); Discovery §Discovery Adapter Feasibility (Section 6: Service Playbook for Login)

---

## Summary

| WI | Title | Points | Dependencies | Track |
|----|-------|--------|-------------|-------|
| 6.1 | MemoryService | 5 | Phase 1 | Memory |
| 6.2 | PlaybookService & Write-Back | 5 | Phase 3, Phase 5 | Playbooks |
| | **Total** | **10** | | |

### Velocity Assumptions

- **Solo developer**: 5 points/week. Reduced velocity reflects LLM prompt iteration for playbook summarization and integration testing across the full stack.
- **Week 1 target** (5 pts): WI 6.1 (5) — MemoryService (standalone, no LLM dependency for core ops)
- **Week 2 target** (5 pts): WI 6.2 (5) — PlaybookService with write-back and prompt integration

### Test Coverage Summary

| WI | Minimum Tests | Coverage Target |
|----|--------------|-----------------|
| 6.1 | 14 | 100% for `services/memory_service.py` |
| 6.2 | 14 | 100% for `services/playbook_service.py` |
| **Total** | **28** | |

### Exit Criteria Traceability

| Phase Exit Criterion | Verified By |
|---------------------|-------------|
| LLM generates playbook from successful execution | WI 6.2 AC (write_back) |
| Playbook loaded into agent prompt on subsequent cycles | WI 6.2 AC (PersonaContext integration) |
| Memory entries persist and are pruned | WI 6.1 AC (store, recall, prune) |
| Human edits preserved during write-back | WI 6.2 AC (marker preservation) |
| `just list-playbooks` and `just show-playbook` work | WI 6.2 AC (CLI) |
| `--dry-run` previews without writing | WI 6.2 AC (dry_run flag) |
