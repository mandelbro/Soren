# 0003: LLM-suggested service notes and shortcuts from training sessions

**Status:** Open
**Origin:** Service configuration Phase 4 ([ADR-0002](../adr/0002-service-configuration.md)); deferred by agreement.
**Related:** `docs/discovery/autonomous-learning-system-enhancement.md` O3 Layer 3 ("Application Context Summary")

## Problem

Phase 4 lets the trainer write service knowledge explicitly (`/note`,
`/instruction`, `/shortcut`, `/crop`). But the session already contains a lot
of implicit knowledge: the instructions the trainer typed, the verifier's
reasoning, screen descriptions, and which retries succeeded. The trainer has
to notice and transcribe all of it by hand. An LLM could propose notes,
instructions, and shortcuts from the session for the trainer to accept or
reject at `/done`.

## Current state

- **Staging and writing:** `TrainingServiceCommands.prepare_save()` already
  builds a `ServiceSavePlan` from `ServiceUpdate` (staged changes plus
  automatic URL/crop proposals). The trainer confirms it via
  `TrainingCallbacks.confirm_service_update()`. `ServiceWriter` writes it with
  validation, an atomic replace, and rotated backups.
- **Session data available at `/done`:**
  - recorded `PlaybookStep`s (instruction, expected_after);
  - per-step screen descriptions from `LLMService.generate`;
  - verifier reasoning;
  - click coordinates from retries.
- **Prompt cost:** service `notes` reach the agent prompt only when
  `include_notes_in_prompt` is true, so suggested notes can be evaluated with
  and without prompt exposure.
- **Prior art:** `LLMService` and the existing `auto_playbook.py` pattern
  (structured YAML from an LLM summary) can be reused.

## Proposed approach

1. Add `suggest_service_knowledge(llm, steps, descriptions, existing: Service | None) -> ServiceUpdate`.
   - The prompt asks for structured output only:
     `{notes: [...], instructions: [...], keyboard_shortcuts: [{keys, action, when}]}`.
   - Parse and validate with the existing models, dropping anything invalid.
   - Drop suggestions that duplicate the existing file.
2. At `/done`, merge the suggestions into the plan as separately labelled changes
   (`origin: "suggested"`). Let the trainer accept all, reject all, or pick
   individual items. This needs a richer confirm callback that returns the
   accepted subset.
3. Shortcut suggestions need an evidence check: only suggest a shortcut the
   trainer typed or explicitly mentioned, never one invented from general
   knowledge. ADR-0001 allows only real, standard shortcuts.

## Open questions

1. Should this be opt-in per session (`train run --suggest`) or on by default?
   Each suggestion costs one LLM call.
2. Which model: the primary reasoning model or the fallback? It's a
   cloud-tagged model in the current `settings.yaml`; see discovery doc
   Section 7 on local-only inference.
3. Should suggested notes be written to `notes:`, or to a separate
   `suggested_notes:` field until a human promotes them?

## Acceptance criteria

- Suggestions are shown as a distinct group in the `/done` confirmation, and
  nothing is written without acceptance.
- Invalid or duplicate suggestions are dropped before the trainer sees them
  (unit-tested with a stubbed LLM).
- Shortcut suggestions without evidence in the session are discarded.
- With suggestions disabled, behavior is identical to Phase 4.
