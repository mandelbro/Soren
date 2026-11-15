# How Experienced Developers Prevent AI Coding Assistants from Drifting Away from Architectural Vision

External research survey, April 2026. Scope: Claude Code, Cursor, Cline, Aider, Windsurf, Continue, and the broader AI coding tools ecosystem.

## Executive summary

- The community has converged on **persistent, hierarchical markdown files** (CLAUDE.md, AGENTS.md, .cursor/rules/*.mdc, memory-bank/) as the single most reliable defense against drift, because conversation context is lossy but repo-committed files survive compaction, new sessions, and even tool switches.
- The GitHub Spec Kit "constitution" pattern and Cline's Memory Bank both codify the same insight: split **immutable principles** (what must never change) from **active context** (what's changing now), and force the agent to re-read the immutable file at the start of every task.
- Claude Code's **SessionStart, UserPromptSubmit, PreCompact, and PreToolUse hooks** give power users programmatic choke points to inject invariants into the context and to block proposals that violate them — this is the closest thing to a hard guardrail available today.
- **Subagents acting as architecture reviewers** (e.g. `architecture-guardian`, `architect-reviewer`) are being used as pre-commit auditors that diff uncommitted changes against documented patterns — a separation-of-concerns approach that keeps the main agent fast while a cheaper review agent enforces invariants.
- Academic work confirms what practitioners see empirically: **all evaluated models exhibit goal drift** under competing objectives, and drift correlates with context length as pattern-matching displaces instruction following ([Goal Drift, 2025](https://arxiv.org/abs/2505.02709)). Scaffolding and structured memory meaningfully extend the safe horizon.

## 1. Prompt and spec patterns in active use

### 1.1 Hierarchical context files (CLAUDE.md, AGENTS.md, .cursor/rules)

Every major tool now supports directory-traversal loading of markdown rule files. Claude Code recursively reads CLAUDE.md files, merging a personal file at `~/.claude/CLAUDE.md`, a project file at `./CLAUDE.md`, and subdirectory files that "append context rather than override parent context" ([Steve Kinney, Developing with AI Tools](https://stevekinney.com/courses/ai-development/claude-dot-md)). The recommended pattern is to put *stable rules* in CLAUDE.md rather than in the conversation, because "early instructions can get lost during compaction" ([Claude Code best practices](https://code.claude.com/docs/en/best-practices)).

Cursor uses the same idea with `.cursor/rules/*.mdc` files. Each MDC file has frontmatter controlling scope:

```mdc
---
description: Enforce repository pattern for DB access
globs: ["src/services/**/*.ts"]
alwaysApply: false
---
```

`alwaysApply: true` attaches the rule to every conversation regardless of globs; `alwaysApply: false` with `globs` only attaches when files matching the glob are touched. Cursor supports nested `.cursor/rules/` folders inside subdirectories so component-scoped invariants live next to the code they govern ([Cursor Rules docs](https://cursor.com/docs/context/rules), [mer.vin deep dive](https://mer.vin/2025/12/cursor-ide-rules-deep-dive/)).

**AGENTS.md has become the cross-tool standard.** OpenAI pioneered the format for Codex, donated it to the Linux-Foundation-hosted Agentic AI Foundation in December 2025, and it now ships with GitHub Copilot, Cursor, Windsurf, Kilo Code, and others; over 60,000 public GitHub repos already include one ([Augment Code guide](https://www.augmentcode.com/guides/how-to-build-agents-md), [agents.md](https://agents.md/)). Agents "automatically read the nearest file in the directory tree, so the closest one takes precedence."

### 1.2 The constitution pattern (Spec Kit)

GitHub's Spec Kit introduces a file at `memory/constitution.md` containing **nine articles of immutable principle** — library-first structure, CLI interface mandate, test-first, simplicity (≤3 projects), anti-abstraction, integration-first, etc. The `/speckit.plan` command validates every implementation plan against these articles via pre-implementation "gates":

```markdown
#### Simplicity Gate (Article VII)
- [ ] Using ≤3 projects?
- [ ] No future-proofing?

#### Anti-Abstraction Gate (Article VIII)
- [ ] Using framework directly?
- [ ] Single model representation?
```

If a gate fails, the LLM must document justification in a "Complexity Tracking" section — the drift is recorded rather than silent ([github/spec-kit spec-driven.md](https://github.com/github/spec-kit/blob/main/spec-driven.md)). The mechanism that actually prevents drift is the split between `specs/[branch]/` (what's changing this sprint) and `memory/constitution.md` (what never changes) — the same split Cline's Memory Bank enforces.

### 1.3 Memory Bank patterns (Cline and Cascade)

Cline's Memory Bank mandates six markdown files that the agent "MUST read ALL of at the start of EVERY task" ([Cline docs](https://docs.cline.bot/features/memory-bank)): `projectbrief.md`, `productContext.md`, `activeContext.md`, `systemPatterns.md`, `techContext.md`, `progress.md`. The explicit hierarchy separates vision (`projectbrief`) from current work (`activeContext`) so that changing one doesn't corrupt the other. Windsurf's community `cascade-memory-bank` uses a similar four-file layout with `decisionLog.md` capturing the *why* of architectural choices ([GreatScottyMac/cascade-memory-bank](https://github.com/GreatScottyMac/cascade-memory-bank)).

### 1.4 ADRs as living context

Chris Swan argues ADRs are "perfect for things based on LLMs" because they combine structured headers with natural language — they already communicate decisions to humans, so feeding them to agents costs nothing ([State of Me blog](https://blog.thestateofme.com/2025/07/10/using-architecture-decision-records-adrs-with-ai-coding-assistants/)). The practical technique is to reference ADRs by path in CLAUDE.md/AGENTS.md (`"Before proposing changes to the model pipeline, read docs/adr/004-local-inference.md"`) so the agent retrieves them when relevant.

### 1.5 Per-step constraint design

A nuanced drift-prevention technique from [Akari Iku on dev.to](https://dev.to/akari_iku/how-to-stop-claude-code-skills-from-drifting-with-per-step-constraint-design-2ogd): rather than assigning one freedom level to an entire skill or prompt, apply **different constraint types per step**. Four types are identified:

1. **Procedural** (HOW) — sequential instructions
2. **Criteria** (WHAT) — define standards, not methods
3. **Template** — fix output structure, allow content flexibility
4. **Guardrail** — prohibition-based boundaries

Example of the shift: `"Calculate ROI"` (procedural, drifts) becomes `"Calculate on 3-year TCO basis; quantify benefits on 3 axes: time saved, cost reduction, error rate reduction; surface all assumptions as text"` (criteria, stable). The principle: **match specificity to fragility** — lock down the steps where drift would be expensive, leave creative latitude where it's cheap.

### 1.6 Addy Osmani's six-area spec framework

Addy Osmani's [good spec guide](https://addyosmani.com/blog/good-spec/) organizes invariant files around six areas: **commands**, **testing**, **project structure**, **code style** (with real examples, not prose), **git workflow**, and **boundaries**. The boundaries section uses a three-tier system that maps directly to drift prevention:

- **Always do** — safe actions requiring no approval
- **Ask first** — high-impact decisions needing human review
- **Never do** — hard stops

Osmani warns against "context overload: the curse of instructions degrades performance as requirements multiply" and recommends "extended TOC with summaries" so large specs stay indexable.

## 2. Tooling patterns in active use

### 2.1 Claude Code hooks as programmable guardrails

Claude Code exposes 24 hook events. The four most relevant to drift prevention:

- **SessionStart** — fires on new session, resume, `/clear`, or compact. Output via stdout or `hookSpecificOutput.additionalContext` (10,000 char cap) is injected into the conversation before Claude sees anything. Canonical use: load git status, recent issues, current branch, and a paste of the project constitution ([Claude Code hooks reference](https://code.claude.com/docs/en/hooks)).

- **UserPromptSubmit** — runs between user prompt submission and Claude processing. Can inject context *or block the prompt* with `{"decision": "block", "reason": "..."}`. This is the single most powerful drift-prevention hook because it lets you enforce rules like "every prompt mentioning `inference` must be accompanied by a quote from the local-inference ADR."

- **PreToolUse** — inspects and blocks/modifies individual tool calls. Official docs show patterns for blocking `rm -rf`, denying Bash commands by regex, and scoping `Write` to specific paths ([Claude Code power user customization](https://claude.com/blog/how-to-configure-hooks)).

- **PreCompact** — the most important hook for long sessions. Fires before context compaction, receives session_id and transcript_path, and can "spawn a fresh Claude instance via `claude -p` to generate a handover summary because the current context window is already nearly full" ([Yuanchang blog](https://yuanchang.org/en/posts/claude-code-auto-memory-and-hooks/)). Users have independently built PostCompact verification logic that detects whether identity markers survived compaction and signals ALIVE / WEAK / SILENT ([Claude Code issue #40492](https://github.com/anthropics/claude-code/issues/40492)).

A minimal SessionStart hook that loads a constitution looks like:

```bash
#!/bin/bash
jq -n --rawfile ctx .claude/constitution.md '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
```

### 2.2 Subagents as architecture auditors

Claude Code subagents are being used as **separation-of-concerns reviewers**. Two representative examples:

- **architecture-guardian** ([masikonis/cc-subagents](https://github.com/masikonis/cc-subagents)) — "analyzes uncommitted git changes against established project patterns, guidelines, and architectural decisions documented in the codebase." It runs before commits and can catch drift that the main agent missed.

- **architect-reviewer** ([VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)) — a four-phase workflow that queries the context manager for system architecture, reviews against pattern/scalability/security/technical-debt dimensions, and returns strategic recommendations. Configured to use Opus specifically so the reviewer is "smarter than" the implementer.

The pattern generalizes: use a cheaper model for the implementation loop, spawn a more expensive model as a subagent that only sees the diff and the invariants file.

### 2.3 MCP memory servers and ConPort

The gap between "a markdown file I can grep" and "a knowledge graph the agent queries naturally" is being filled by MCP memory servers. **Context Portal (ConPort)** is the leading example: an MCP server that stores project decisions, progress, and architectural entities as a SQLite-backed knowledge graph with vector embeddings for semantic search ([greatscottymac/context-portal](https://github.com/greatscottymac/context-portal)). The pitch is "structured context as a first-class interface rather than text blobs retrieved at generation time" ([gitbutler blog on MCP vs RAG](https://blog.gitbutler.com/mcp-vs-rag)). For projects with many ADRs, this lets the agent retrieve the specific decision rather than reading all of them into context.

### 2.4 Aider's architect mode

Aider splits reasoning from editing by running two models per turn: an architect model that proposes an approach, and an editor model that turns it into file edits ([aider.chat architect blog](https://aider.chat/2024/09/26/architect.html)). On Aider's code-editing benchmark, `o1-preview` as architect + `deepseek` or `o1-mini` as editor hit SOTA 85%. The relevance to drift: the architect can "focus on solving the problem and describe the solution however comes naturally to it" while the editor does the mechanical work. For drift-sensitive refactors, a smart architect asked to re-check invariants is cheaper and more reliable than a single model juggling both.

Aider also supports a **conventions file** passed via `--read` that ships as read-only context every turn — equivalent to CLAUDE.md but enforced at the tool level.

## 3. Process patterns in active use

### 3.1 State-file-first workflows

The top-voted HN discussion on Claude Code drift concluded: "treating state files as a first-class primitive rather than hoping the agent remembers between sessions is the right instinct" ([HN 47402125](https://news.ycombinator.com/item?id=47402125)). The practical implementation is four files — `PROJECT.md` (vision), `REQUIREMENTS.md` (schemas, flows, edge cases), `ROADMAP.md` (phases), `STATE.md` (current position) — that every agent reads before any code. Multiple agents in parallel read the same files, which gives consistency at the cost of a slower feedback loop.

### 3.2 Git worktree isolation

Mentioned in the same HN thread: use `git worktree` to isolate each agent's filesystem when running parallel sessions, preventing "the physical isolation problem — multiple agents in the same working directory stepping on each other's files mid-session." This is a process pattern, not a context one, but it removes a class of drift caused by agents observing half-complete state from other agents.

### 3.3 The spec-refresh ritual

The spec-driven development literature is consistent that **specs go stale**, and a stale spec is worse than no spec because the agent trusts it. The Spec Kit answer is a constitutional amendment process: "Modifications require explicit documentation of rationale, review and approval by maintainers, backwards compatibility assessment." In practice, power users treat spec files like code: they update CLAUDE.md in the same PR that changes the pattern CLAUDE.md documents, and they review CLAUDE.md diffs with the same rigor as code diffs ([DataCamp Cursor Rules tutorial](https://www.datacamp.com/tutorial/cursor-rules) recommends reviewing rules "after any major refactor, dependency upgrade, or folder restructure since rules that reference deprecated APIs or removed files will feed the AI wrong instructions").

## 4. Documented failure modes and lessons learned

### 4.1 The 693-line hallucination spiral

SurgeHQ documented a detailed failure case where **Gemini 2.5 Pro spiraled into 693 lines of hallucinated code across 39 turns** trying to apply a 2-line fix to astropy's HTML table writer ([SurgeHQ blog](https://surgehq.ai/blog/when-coding-agents-spiral-into-693-lines-of-hallucinations)). Root cause: file truncation during code inspection. Rather than flagging the missing context, Gemini "filled in the gaps with assumptions and internal knowledge," inventing a non-existent `BaseWriter` class, fabricating methods like `_get_col_str_iters()`, and chasing phantom line numbers. Each hallucination fed the next: "It updated its assumptions just enough to explain away the contradiction."

The article contrasts three model behaviors on the same class of failure:
1. **Gemini (failed)**: doubled down on assumptions
2. **Claude (recovered)**: hit errors, reassessed, reinvestigated, corrected course
3. **GPT-5 (avoided)**: flagged gaps explicitly and re-inspected before proceeding

**Key lesson for drift prevention**: the failure mode is not bad code generation, it's an unwillingness to backtrack when assumptions break. Guardrails that force re-inspection (PreToolUse hooks, architecture-guardian subagents) are catching exactly this pattern.

### 4.2 Context rot

Multiple practitioners report a consistent degradation curve: "at 70% context, Claude starts losing precision; at 85%, hallucinations increase; at 90%+, responses become erratic" ([Elliot on Medium](https://medium.com/@elliotJL/your-ai-has-infinite-knowledge-and-zero-habits-heres-the-fix-e279215d478d)). This quantifies the folk wisdom that long sessions drift, and motivates the PreCompact handover pattern — compacting early and on your terms rather than waiting for the automatic cliff.

### 4.3 "Firefighting mode" is a known failure pattern

The fix-retry-loop failure — "Claude fails at something, retries the exact same approach five times, never tries a different strategy, never escalates" — is documented repeatedly ([ranthebuilder lessons](https://ranthebuilder.cloud/blog/claude-code-best-practices-lessons-from-real-projects/)). The pattern holds across tools. This is the observable surface of drift: the agent stops cross-referencing invariants because it's locked onto the immediate symptom.

## 5. Research literature

Four papers matter here:

- **Goal Drift Evaluation ([arXiv 2505.02709](https://arxiv.org/abs/2505.02709))** — provides the first systematic framework for measuring drift. Finds that "all evaluated models exhibit some degree of goal drift" and that drift "correlates with models' increasing susceptibility to pattern-matching behaviors as the context length grows." A scaffolded Claude 3.5 Sonnet held up past 100,000 tokens in the hardest setting, showing that **scaffolding matters more than raw model capability** for long-horizon adherence.

- **Scaling Long-Horizon LLM Agent via Context-Folding ([arXiv 2510.11967](https://arxiv.org/abs/2510.11967))** — introduces "context-folding" where an agent branches into a sub-trajectory for a subtask, then folds it back into a summary. Matches or beats ReAct baselines on Deep Research and SWE tasks while using **10× smaller active context**. Directly relevant: this is the academic version of PreCompact handover summaries.

- **Agent Drift: Quantifying Behavioral Degradation in Multi-Agent LLM Systems ([arXiv 2601.04170](https://arxiv.org/abs/2601.04170))** — proposes three interventions: episodic memory consolidation, drift-aware routing, and adaptive behavioral anchoring. The first two map directly to the ConPort and architecture-guardian patterns described above.

- **Agentic Memory (AgeMem) ([arXiv 2601.01885](https://arxiv.org/abs/2601.01885))** — treats memory operations as tool-based actions the agent decides when to invoke, rather than a passive context window. This is the research direction behind the MCP memory server pattern.

## 6. Recommendations for a Claude Code user on a long-running project

Ordered by effort-to-value ratio, best first:

1. **Write an `invariants.md` (or `constitution.md`) and load it via SessionStart hook.** Highest return on effort. Separate the immutable "what this project must always be" from the mutable "what we're working on." 20-line hook, file under a hundred lines. Covers the Soren-style problem of forgetting the central vision during firefighting. ([Claude Code hooks reference](https://code.claude.com/docs/en/hooks))

2. **Split CLAUDE.md into `./CLAUDE.md` (rules) and `./docs/CLAUDE.md`, `./src/CLAUDE.md`, `./tests/CLAUDE.md` (subdirectory context).** Zero runtime cost, reduces cognitive load per-query because the agent only merges files in directories it's touching. ([Claude Code best practices](https://code.claude.com/docs/en/best-practices))

3. **Install a UserPromptSubmit hook that blocks prompts missing invariant acknowledgement for high-risk file paths.** Medium effort. Lets you enforce "any change under `src/inference/` must reference `docs/adr/004-local-inference.md` by path in the prompt." Catches firefighting before it starts.

4. **Create an `architecture-guardian` subagent that runs before commits.** Borrow the [masikonis/cc-subagents](https://github.com/masikonis/cc-subagents) definition and adapt it to your specific invariants. Configure it to use Opus; this is the single highest-leverage audit you can automate.

5. **Install a PreCompact hook that snapshots active decisions and open questions to a file.** Medium effort but compounds over long sessions. Look at [coleam00/claude-memory-compiler](https://github.com/coleam00/claude-memory-compiler) for a working implementation.

6. **Treat CLAUDE.md/AGENTS.md changes like code changes.** Require them in the same PR as the pattern change. Use `./docs/CLAUDE.md` diffs as a drift-detection signal during PR review.

7. **Apply per-step constraint design to any skill or slash command that has drifted in the past.** Rewrite procedural steps as criteria-based ones wherever the agent has latitude that caused problems.

8. **Consider a ConPort-style MCP memory server if ADRs/decisions exceed ~15 documents.** At that scale, pasting all ADRs into context is wasteful and RAG over a knowledge graph is cleaner. Lower priority because it adds a dependency.

9. **For high-stakes refactors, switch to Aider architect mode for that session.** The two-model split forces the expensive model to *describe* the change before the editor touches files — a natural checkpoint to validate against invariants.

10. **Add a "drift check" line item to your PR review rubric.** Before merging, manually confirm the change is consistent with the top three project invariants. This is low-tech and experimental but catches the cases where automation missed.

Techniques flagged as experimental or of uncertain value: PostCompact verification signals (ALIVE/WEAK/SILENT) are still hand-rolled and unstandardized. Context-Folding exists in research but not in production tooling. The AGENTS.md standard is young (donated to Linux Foundation in December 2025) and tool support varies.

## Sources

- [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks) — canonical list of 24 hook events, stdin/stdout contracts, exit code semantics
- [Claude Code best practices](https://code.claude.com/docs/en/best-practices) — Anthropic's own guidance on CLAUDE.md and context stability
- [Anthropic: How to configure Claude Code hooks](https://claude.com/blog/how-to-configure-hooks) — official examples of SessionStart and PreToolUse patterns
- [Steve Kinney — CLAUDE.md](https://stevekinney.com/courses/ai-development/claude-dot-md) — hierarchical CLAUDE.md loading and subfolder append behavior
- [Yuanchang — Claude Code Memory Evolution](https://yuanchang.org/en/posts/claude-code-auto-memory-and-hooks/) — PreCompact hook mechanics and fresh-instance handover pattern
- [Cursor Rules docs](https://cursor.com/docs/context/rules) — MDC rule types, alwaysApply/globs semantics
- [mer.vin — Cursor IDE Rules Deep Dive](https://mer.vin/2025/12/cursor-ide-rules-deep-dive/) — hierarchical directory-scoped rules
- [DataCamp — Cursor Rules tutorial](https://www.datacamp.com/tutorial/cursor-rules) — stale-rule failure mode and review cadence
- [Cline Memory Bank docs](https://docs.cline.bot/features/memory-bank) — six mandatory markdown files, "MUST read ALL" enforcement
- [GreatScottyMac — Cascade Memory Bank](https://github.com/GreatScottyMac/cascade-memory-bank) — four-file layout with decisionLog.md for Windsurf
- [Windsurf Cascade Memories docs](https://docs.windsurf.com/windsurf/cascade/memories) — rules vs auto-memories, global_rules.md, AGENTS.md support
- [AGENTS.md home](https://agents.md/) and [Augment Code AGENTS.md guide](https://www.augmentcode.com/guides/how-to-build-agents-md) — cross-tool standard, 60k+ repos, LF donation
- [github/spec-kit spec-driven.md](https://github.com/github/spec-kit/blob/main/spec-driven.md) — constitution articles, planning gates, drift prevention mechanism
- [Chris Swan — ADRs with AI coding assistants](https://blog.thestateofme.com/2025/07/10/using-architecture-decision-records-adrs-with-ai-coding-assistants/) — natural-language fit, agent-swarm implications
- [Addy Osmani — How to write a good spec](https://addyosmani.com/blog/good-spec/) — six-area framework, three-tier boundary system, curse of instructions
- [Akari Iku — Per-step constraint design](https://dev.to/akari_iku/how-to-stop-claude-code-skills-from-drifting-with-per-step-constraint-design-2ogd) — procedural vs criteria vs template vs guardrail constraint types
- [masikonis — architecture-guardian subagent](https://github.com/masikonis/cc-subagents) — concrete subagent definition for pre-commit architectural diffing
- [VoltAgent — architect-reviewer subagent](https://github.com/VoltAgent/awesome-claude-code-subagents/blob/main/categories/04-quality-security/architect-reviewer.md) — four-phase review workflow, Opus configuration
- [Aider — architect mode](https://aider.chat/2024/09/26/architect.html) — two-model split, SOTA benchmark result, rationale for separating reasoning from editing
- [greatscottymac/context-portal (ConPort)](https://github.com/greatscottymac/context-portal) — MCP memory server with SQLite knowledge graph and vector embeddings
- [gitbutler — MCP vs RAG](https://blog.gitbutler.com/mcp-vs-rag) — framing of structured context as first-class interface
- [SurgeHQ — 693 lines of hallucinations](https://surgehq.ai/blog/when-coding-agents-spiral-into-693-lines-of-hallucinations) — detailed failure case, cross-model comparison, re-inspection lesson
- [Elliot on Medium — your AI has zero habits](https://medium.com/@elliotJL/your-ai-has-infinite-knowledge-and-zero-habits-heres-the-fix-e279215d478d) — context-rot percentages (70/85/90%)
- [ranthebuilder — Claude Code best practices lessons](https://ranthebuilder.cloud/blog/claude-code-best-practices-lessons-from-real-projects/) — fix-retry-loop failure pattern documentation
- [HN 47402125 — Persistent markdown files for Claude Code drift](https://news.ycombinator.com/item?id=47402125) — state-file-as-first-class-primitive quote, git worktree isolation pattern
- [arXiv 2505.02709 — Goal Drift in LM Agents](https://arxiv.org/abs/2505.02709) — first systematic drift measurement, scaffolding beats raw capability finding
- [arXiv 2510.11967 — Context-Folding](https://arxiv.org/abs/2510.11967) — 10x active context reduction via sub-trajectory folding
- [arXiv 2601.04170 — Agent Drift quantification](https://arxiv.org/abs/2601.04170) — three interventions: episodic memory consolidation, drift-aware routing, adaptive behavioral anchoring
- [arXiv 2601.01885 — Agentic Memory (AgeMem)](https://arxiv.org/abs/2601.01885) — memory operations as first-class agent tools
- [coleam00 — claude-memory-compiler](https://github.com/coleam00/claude-memory-compiler) — working PreCompact hook implementation for reference
