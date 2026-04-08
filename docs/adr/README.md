# Architecture Decision Records

This directory contains **Architecture Decision Records (ADRs)** for Soren —
short, append-only documents that capture significant architectural decisions,
their rationale, alternatives considered, and trade-offs.

## Why ADRs exist in this project

The Soren RFC (`docs/rfc.md`) is the high-level architectural specification.
It captures the *initial* decisions for v1. As the system evolves, we need a
way to document decisions that:

1. **Refine or clarify an RFC section** without requiring a full RFC rewrite
2. **Supersede an RFC decision** that was made before new evidence came in
3. **Codify a discovery** (`docs/discovery/*.md`) once a direction is chosen
4. **Resolve an ambiguity** the RFC left open (e.g., the Vision-Keeper
   subagent returned `AMBIGUOUS` on a proposal and the user clarified the
   policy)

Without ADRs, the RFC either drifts out of date or grows unwieldy. ADRs keep
the decision trail auditable and let the RFC stay focused on architectural
shape rather than every refinement.

## Relationship to the Vision-Keeper guardrail

The Vision-Keeper subagent (`.claude/agents/vision-keeper.md`) audits proposed
architectural changes against the RFC **and** the ADRs in this directory.
When an ADR and the RFC conflict, **the more recent ADR wins** — the
Vision-Keeper is instructed to treat a published ADR as authoritative for
the specific area it addresses.

This means: when a discovery turns into a decision, the decision gets
captured in an ADR *before* implementation. The RFC may reference the ADR
("See ADR-NNNN"). The Vision-Keeper reads both, so future sessions audit
proposals against the current-state-of-the-architecture, not a stale spec.

If you implement something without writing an ADR for the decision, and the
RFC doesn't already cover the decision explicitly, future Vision-Keeper
audits will flag proposals in the same area as `AMBIGUOUS` because the spec
is incomplete. That is the intended behavior — missing ADRs mean missing
authority, and authority needs to be written down to be enforced.

## ADR format

Each ADR is a markdown file named `NNNN-short-slug.md`, numbered
sequentially starting at `0001`. The format is lightweight (based on
Michael Nygard's original proposal) with sections:

```markdown
# ADR-NNNN: <title>

**Status:** Proposed | Accepted | Superseded by ADR-NNNN | Deprecated
**Date:** YYYY-MM-DD
**Supersedes:** (optional) ADR-NNNN
**Related RFC sections:** (optional) §Goals, §Alternatives Considered #1

## Context

What is the situation? What problem or question is this decision addressing?
What does the RFC say (if anything) about this area? What discovery or
session prompted the decision?

## Decision

What is the decision? State it plainly and concretely.

## Rationale

Why this decision and not the alternatives? What are the load-bearing
arguments? If the decision resolves a tension between two RFC sections,
name the sections and explain how the tension is resolved.

## Consequences

- What becomes easier?
- What becomes harder?
- What new constraints does this impose?
- What code/config needs to change to implement the decision?

## Alternatives Considered

List the alternatives that were evaluated and why they were rejected.
Be specific — "we considered X but X violates Y" is more useful than
"we considered several options."

## Implementation notes

Optional. If the decision is implemented in a specific commit, PR, or
file, reference it here. This makes the ADR a navigable anchor back to
the code.
```

## Index

When adding a new ADR, update the index below and add a reference to the
relevant RFC section(s) if the decision refines, clarifies, or supersedes
RFC content.

| # | Title | Status | Date | RFC sections |
|---|-------|--------|------|--------------|
| [0001](0001-training-mode-keyboard-shortcut-policy.md) | Training-mode keyboard shortcut policy | Accepted | 2026-04-08 | §Alternatives Considered #1 |

## When NOT to write an ADR

- Bug fixes that don't change the architectural shape
- Pure refactoring that preserves behavior
- Additions of new tests, docs, or developer tooling with no runtime impact
- Config tweaks that don't change what the system *can* do

ADRs are for decisions with *architectural weight* — things that constrain
or enable whole classes of future implementation. When in doubt, ask:
"would the Vision-Keeper need to know about this to audit future proposals
in this area?" If yes, write an ADR. If no, a commit message is fine.
