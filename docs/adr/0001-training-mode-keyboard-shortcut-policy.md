# ADR-0001: Training-mode keyboard shortcut policy

**Status:** Accepted
**Date:** 2026-04-08
**Related RFC sections:** §Alternatives Considered #1 (DOM vs Vision-based), §Carve-out: VM state management

## Context

The Soren RFC establishes vision-based GUI automation as the core execution
modality for task actions, with an explicit rejection of DOM-based and
accessibility-API-based automation (§Alternatives Considered #1). The
rationale is bot-detection avoidance: real OS-level input events produce
`event.isTrusted === true`, while synthesized events from DOM automation
or accessibility APIs are detectable by modern anti-fraud libraries.

The RFC already contains a carve-out for osascript usage in "VM state
management" (closing Safari windows, switching Spaces, session reset) on
the grounds that such actions are environment setup, not task actions.
However, the RFC's wording in §Alternatives Considered #1 line 425 states:

> The only shortcut that's allowed is screenshot preprocessing (cropping,
> scaling, masking) before the VLM sees the image, because that's a
> transformation of the model's perception, not of the action transport.

This "only shortcut" phrasing created an internal tension with the osascript
carve-out, and left an ambiguity that surfaced when the Vision-Keeper
subagent was asked to audit a proposal to add automatic URL navigation via
`Cmd+L → type URL → Enter` dispatched through CUA's keyboard pathway
(rather than via a VLM-planned click on the URL bar). The subagent
correctly returned `AMBIGUOUS` because the RFC does not unambiguously
resolve:

- Whether `Cmd+L` (keyboard shortcut dispatched via CUA → real OS events)
  counts as a "shortcut" forbidden by line 425 or as a legitimate use of
  the existing mouse/keyboard transport
- Whether the "vision-based grounding" mandate applies per-action or as
  a dominant-modality description
- Whether URL navigation at the start of a task counts as "task action"
  (VLM-required) or "environment setup" (osascript carve-out applies)
- Whether a separate "training mode" should have different rules from
  autonomous execution

This ADR resolves the tension by stating the policy explicitly.

## Decision

**Deterministic keyboard and pointer sequences dispatched through CUA's
real-OS-input transport are permitted as task actions, in addition to
VLM-grounded actions.** UI-TARS is the grounding engine for unknown or
visual targets — it is NOT the mandatory router for every action.

Specifically:

1. **Real OS input via CUA is the authoritative action transport.** Any
   action that reaches the VM as `event.isTrusted === true` via the CUA
   keyboard/mouse pathway is permitted regardless of whether it originated
   from UI-TARS's plan or from hardcoded code.

2. **VLM grounding is required only when the target of an action is
   unknown or visual.** If a human operator completing the same task would
   look at the screen to find something before clicking, Soren should do
   the same (via UI-TARS). If a human operator would use a keyboard
   shortcut or a known fixed interaction, Soren may do the same directly.

3. **The bot-detection thesis defines the constraint.** The reason the RFC
   rejects DOM automation is that DOM actions produce synthetic events with
   `isTrusted === false`. A `Cmd+L` dispatched through CUA produces a real
   OS key event with `isTrusted === true` — indistinguishable from a human
   keystroke on the wire. The RFC's bot-detection rationale does not extend
   to real OS events.

4. **Deterministic sequences must still obey the "operate like a human"
   thesis at the behavioral level.** Valid examples:
   - `Cmd+L` to focus the URL bar (real humans use this shortcut)
   - `Cmd+W` to close a tab
   - Typing a URL character-by-character via `interface.type_text`
   - `Return` to submit a form
   - Standard browser hotkeys that real users actually use

   Invalid examples (would be detectable as bot-like):
   - Typing an entire paragraph in zero milliseconds (no natural rhythm)
   - Executing 20 hotkeys back-to-back with no pauses
   - Using non-standard hotkeys that no real user would know

5. **Training mode gains an explicit permission to pre-populate URL
   navigation** as a deterministic step before human instruction begins.
   This sidesteps the current Safari-chrome-crop incompatibility (the crop
   hides the URL bar from UI-TARS's view) and avoids wasting training
   cycles on a deterministic step.

## Rationale

The RFC's bot-detection thesis is about the *transport layer* — what the
target app and its anti-fraud libraries observe on the wire. DOM automation
and accessibility-API automation are rejected because their wire signatures
are distinguishable from real user input. Real OS events via CUA are not
distinguishable from real user input, regardless of whether a VLM planned
them or hardcoded code dispatched them.

Line 425 of the RFC ("only shortcut allowed is screenshot preprocessing")
was written to reject DOM-based shortcuts, not to prohibit code-originated
real-OS-input sequences. The phrasing is stricter than the underlying
thesis requires. This ADR narrows the interpretation to "only shortcut
that bypasses the real-OS-input transport is screenshot preprocessing of
the VLM's input" — which is consistent with both §Alternatives Considered
#1's bot-detection rationale AND the existing osascript carve-out for
session housekeeping.

A second benefit: treating UI-TARS as "the grounding engine for visual
targets" rather than "the router for all actions" clarifies the project's
model-driven boundary. VLMs are deployed where they add value (finding
and interpreting visual elements); deterministic code handles what
deterministic code handles well (known sequences, fixed shortcuts). This
is closer to how a real human uses their computer — humans don't visually
locate the "Cmd" and "L" keys every time they want to focus the URL bar.
They have motor memory. Soren's "motor memory" lives in code.

## Consequences

**Easier:**
- Training sessions can auto-navigate to the source URL before the human
  operator starts entering instructions, saving a training cycle on a
  deterministic step
- The Safari chrome crop (SOREN_CHROME_CROP_Y=100) can be enabled for
  webpage-click tasks without breaking URL navigation, because URL
  navigation no longer depends on a visible URL bar in UI-TARS's view
- Future features can use standard browser hotkeys (`Cmd+W` for tab close,
  `Cmd+F` for find-in-page, etc.) without consulting the Vision-Keeper
  first — as long as they dispatch through CUA's keyboard interface
- The Vision-Keeper now has an authoritative answer for "is this
  deterministic keystroke allowed?" instead of returning `AMBIGUOUS`

**Harder:**
- Future reviewers need to actively check that a proposed deterministic
  sequence is something a real human would actually do, not a bot-like
  pattern (e.g., "type entire URL in 0ms" vs "type URL at keyboard pace")
- The line between "VLM required" and "deterministic sequence OK" must be
  judged case-by-case. There is no bright-line rule beyond "would a human
  look at the screen to find this?" and "does the wire signature match a
  human's?"

**New constraints:**
- Any new deterministic sequence must document which human interaction
  pattern it is simulating (e.g., "Cmd+L is the standard Safari URL bar
  shortcut, used by ~everyone who uses Safari")
- CUA's `interface.type_text` should not be used to dump whole paragraphs
  without delay — the default per-keystroke delay must produce a realistic
  typing rhythm

**Supersedes:**
- The strict reading of RFC §Alternatives Considered #1 line 425. The
  RFC is updated in the same commit as this ADR to reference it and to
  replace "The only shortcut that's allowed is screenshot preprocessing"
  with language consistent with this ADR.

## Alternatives Considered

Four policy options were presented to the user by Vision-Keeper's
ambiguous verdict:

1. **Never allow non-VLM-originated keystrokes.** Rejected: would force
   UI-TARS to ground visually-obvious fixed shortcuts (Cmd+L, Cmd+W)
   every time, which is wasteful and also incompatible with the chrome
   crop feature for URL bar interactions.

2. **Only allow non-VLM keystrokes for VM/session housekeeping.**
   Rejected: this is what the existing osascript carve-out already
   covers. It doesn't address the URL navigation use case, which is
   a task action (or at best a task-prelude action), not VM housekeeping.

3. **Training-mode-only exception.** Rejected as too narrow: the same
   reasoning that justifies auto-navigate during training also justifies
   keyboard shortcuts during autonomous execution (e.g., `Cmd+W` to close
   an extra tab that opened unexpectedly). Limiting to training mode
   would create an inconsistent policy where the same action is OK in
   one mode and forbidden in another for no principled reason.

4. **Generally permitted, as long as the transport is real OS input.**
   CHOSEN. This is the broadest reading of the RFC's bot-detection thesis
   and matches the actual technical threat model. Any action that reaches
   the target app via `event.isTrusted === true` is indistinguishable
   from a real user on the wire, regardless of whether a VLM planned it.

## Implementation notes

This ADR is effective immediately upon acceptance. Implementation of the
first feature that exercises this policy (auto-URL-navigate for training
mode) follows in a separate commit.

The Vision-Keeper subagent and its hook should be updated to read ADRs
as part of the audit process, so this policy is enforced on future
proposals. That update is a separate commit landing alongside this ADR.
