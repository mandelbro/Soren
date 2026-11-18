# 0001: Apply per-service `typing_delay_ms`

**Status:** Open
**Origin:** Service configuration Phase 2 ([ADR-0002](../adr/0002-service-configuration.md)); deferred by agreement.
**Related:** [ADR-0001](../adr/0001-training-mode-keyboard-shortcut-policy.md) ("realistic typing rhythm"), RFC §Per-service rate limiting

## Problem

`typing_delay_ms` exists in three places:
- on `TaskSource`, since the RFC;
- on `ServiceConfig`, since ADR-0002;
- as a field of `ResolvedServiceConfig`.

Resolution gives it the usual precedence (default 50 ms), but nothing applies
it. ADR-0001 requires that "CUA's `interface.type_text` should not be used to
dump whole paragraphs without delay". Whether today's behavior meets that
depends on the VM-side server (see open question 1).

## Current state

These three paths all call `type_text` once with the whole string:
- **UI-TARS `type(content=...)` actions:** `cua_agent/loops/uitars.py` calls
  `make_type_item(text)`, then `cua_agent/computers/cua.py:type()` calls
  `interface.type_text(text)` once.
- **Deterministic typing:** `TrainingSession._auto_navigate_to_url` calls
  `interface.type_text(url)` once.
- **The underlying command:** `computer/interface/generic.py:type_text()`
  sends a single `type_text` command to the VM. Its `delay` argument is a
  pause *after* the whole action, not between keystrokes.

The resolved value is available at call time through
`soren.services.service_context.get_active_service().config.typing_delay_ms`.

## Proposed approach

Wrap `interface.type_text` in `soren.patches.vnc_screenshot.patch_computer_screenshot`,
next to the click wrappers:

```python
async def _type_text_paced(text, delay=None):
    per_key_ms = current_typing_delay_ms()   # active service, else legacy default
    if per_key_ms <= 0 or len(text) <= 1:
        return await _orig_type_text(text, delay=delay)
    for ch in text:
        await _orig_type_text(ch)
        await asyncio.sleep(jitter(per_key_ms) / 1000)
    if delay: await asyncio.sleep(delay)
```

- **Jitter:** add ±30% randomization so the rhythm isn't metronomic.
- **Newlines:** the Enter-key patch already strips trailing `\n` and emits
  explicit keypresses, so pacing only ever sees plain text.

An alternative is chunking (for example, 3-5 characters per call) to cut
round-trips while still avoiding a zero-delay paste.

## Open questions

1. Does the VM-side `type_text` handler already pace keystrokes (for example,
   `pyautogui.typewrite(interval=...)`)? Measure on a live VM before changing
   anything. If it does, this task may become "expose the interval" instead.
2. Round-trip cost: a 40-character URL becomes 40 WebSocket commands. Is the
   latency acceptable, or should chunking be the default?
3. Should credentials (password fields) use the same pacing? Probably yes,
   since humans type passwords too. Check that nothing logs per-character calls.

## Acceptance criteria

- With an active service, `typing_delay_ms` controls the gap between typed
  characters (unit-tested with a mocked `type_text` and a patched `sleep`).
- `typing_delay_ms: 0` preserves today's single-call behavior.
- No active service means today's behavior (or the resolved built-in default,
  if question 1 shows pacing is needed everywhere).
- Works for VLM `type` actions and for `TrainingSession._auto_navigate_to_url`.
- ADR-0002 Implementation notes are updated.
