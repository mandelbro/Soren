# Guardrail Adoption Recommendations

Brief summary of which architectural guardrail patterns Soren uses, which we considered, and what we'd adopt next. Full details in the companion articles:

- [Maintaining Project Context with Claude Hooks](maintaining-project-context-with-claude-hooks.md) — the writeup of our two-layer guardrail with ADR evolution
- [Research: AI Context Preservation Techniques](research-ai-context-preservation-techniques.md) — Eco's survey of 28 sources across the ecosystem

---

## What we're using now

### Layer 1: UserPromptSubmit hook (per-prompt reminder)

`scripts/claude-hooks/vision-keeper-reminder.sh` fires on every prompt, injecting RFC invariants and published ADR titles into context. Survives context compaction because it re-fires fresh on each turn.

**Status**: Running in production. Caught drift within first week.

### Layer 2: Vision-Keeper subagent (callable auditor)

`.claude/agents/vision-keeper.md` — read-only subagent (Read/Grep/Glob only) that re-reads `docs/rfc.md` and all `docs/adr/*.md` on every invocation, returns ALIGNED / CONTRADICTS / AMBIGUOUS with citations.

**Status**: Running in production. Returned its first AMBIGUOUS verdict on the auto-URL-navigate proposal, which led to ADR-0001.

### Layer 3: ADR directory (structured evolution channel)

`docs/adr/` paired with Vision-Keeper. When the RFC is silent or ambiguous, user decisions become ADRs. Vision-Keeper reads ADRs on every audit; when RFC and ADR conflict, ADR wins.

**Status**: One ADR published (0001). Closed the stale-spec loop — AMBIGUOUS verdicts convert into ADRs rather than arguments.

---

## What we considered but are not adopting

| Pattern | Why not | Revisit when |
|---|---|---|
| SessionStart-only hook | Per-prompt is strictly better for our session lengths (50+ turns, multiple compacts) | Never — we already have the stronger version |
| Spec Kit constitution gates | More ceremony than a solo project needs; our ADR workflow serves the same function | Team grows beyond 1 developer |
| Cline Memory Bank (6-file) | Our RFC + ADR + CLAUDE.md already covers the same split; adding 6 named files would duplicate | Tool switch away from Claude Code |
| ConPort MCP knowledge graph | Overkill for 1 ADR; plain markdown files work at current scale | ADR count exceeds ~15 documents |
| Aider architect mode | Not Claude Code native; Plan Mode serves a similar role | Drift-sensitive refactors where a two-model split would help |

---

## Recently adopted (implemented 2026-04-10)

### 4. PreCompact handover hook

**What**: `scripts/claude-hooks/pre-compact-handover.sh` fires before context compaction, captures git state, active feature/task info, and integration requirements from the discovery doc. Outputs a handover summary injected into post-compact context.

**Status**: Registered in `.claude/settings.json` for both manual and auto compaction.

### 5. Feature-context hook

**What**: `scripts/claude-hooks/feature-context.sh` fires on `UserPromptSubmit`, detects which feature has pending tasks, loads the one-liner and Integration Requirements from its discovery document.

**Status**: Registered in `.claude/settings.json` alongside the Vision-Keeper reminder. Outputs nothing when no feature has pending tasks (zero noise).

### 6. Architecture-guardian subagent

**What**: `.claude/agents/architecture-guardian.md` — read-only integration auditor (Read/Grep/Glob only) that diffs uncommitted changes against the active feature's discovery document. Returns INTEGRATED / GAPS DETECTED / SILO WARNING with discovery citations.

**Status**: Available for invocation. Not yet wired to a pre-commit hook (invoke manually or via the `/plan` workflow for now).

### 7. `/plan` skill (Discovery → Work Items pipeline)

**What**: `.claude/commands/plan.md` — runs Norwood (discovery validation) → task breakdown → Shelly (integration review) → Zod (prompt review) in one pass.

**Status**: Registered as `/plan` skill. Ready for use.

### 8. Process workflow updates

- **Norwood protocol**: Added mandatory Integration Requirements section template to discovery workflow (`~/.claude/agents/Norwood.md`)
- **Shelly protocol**: Added Discovery-to-Tasks Review Gate with 5 checks: orphaned integrations, assumption drift, phase ordering, data flow, outcome criteria (`~/.claude/agents/Shelly.md`)
- **Zod protocol**: Added Pre-Implementation Task Prompt Review with 4 checks: section coverage, code integration, criteria sufficiency, cross-task data flow (`~/.claude/agents/Zod.md`)
- **Task workflow**: Added outcome-based acceptance criteria (mandatory for >3pt tasks), integration checkpoints, pre-implementation review gate (Step 1.5), post-phase smoke test (Step 1.6) (`~/.claude/knowledge/workflows/tasks-workflow.md`)

---

## What we'd adopt next (ordered by value)

### Per-step constraint design on training prompts

**What**: Rewrite training prompt steps using Akari Iku's taxonomy: criteria-based constraints ("find the primary action button in the webpage content area, below y=100") instead of procedural ones ("click the Log in button").

**Why**: The Safari chrome boundaries prompt is already partially this pattern. Formalizing it may reduce UI-TARS grounding drift more than threading verifier feedback through retries.

**Effort**: Low. Prompt engineering, no code changes.

### Architecture-guardian as pre-commit hook

**What**: Wire the architecture-guardian subagent to a `PreToolUse` hook matching git commit operations, so it runs automatically before every commit.

**Why**: Currently requires manual invocation. Automating it catches integration drift the implementer forgot to check for.

**When to adopt**: When we find ourselves committing changes that the architecture-guardian would have flagged if invoked.

---

## Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-04-08 | Adopted two-layer guardrail (hook + subagent) | Two drift incidents in single debugging session |
| 2026-04-08 | Added ADR directory + taught Vision-Keeper to read ADRs | First AMBIGUOUS verdict on auto-URL-navigate; needed structured evolution channel |
| 2026-04-10 | Documented alternative approaches from research | Broader ecosystem context for the article |
| 2026-04-10 | Implemented PreCompact handover hook | Long sessions cross compact boundaries; handover summaries preserve active state |
| 2026-04-10 | Implemented feature-context hook | Prevents integration blindness — loads discovery integration requirements per-prompt |
| 2026-04-10 | Created architecture-guardian subagent | Integration auditor complement to Vision-Keeper; diffs code vs. discovery |
| 2026-04-10 | Created `/plan` skill | Norwood → tasks → Shelly review → Zod review in one pass |
| 2026-04-10 | Updated Norwood/Shelly/Zod protocols | Integration Requirements mandate, discovery→tasks review gate, pre-impl review |
| 2026-04-10 | Updated task workflow | Outcome criteria, integration checkpoints, review gate (1.5), smoke test (1.6) |
