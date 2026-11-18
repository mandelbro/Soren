# 0004: Discovery ignores login/discovery playbooks in production

**Status:** Open
**Origin:** Found during service configuration Phase 2 review ([ADR-0002](../adr/0002-service-configuration.md)).
**Type:** Bug (existing before this work)

## Problem

`TaskDiscovery._build_nav_prompt()` injects the site playbook's `login` and
`discovery` flows into the navigation prompt, but only when the instance has a
`playbook_service`. Both production call sites construct it without one:

- `src/soren/cli/cycle.py`:
  `discovery = TaskDiscovery(settings, llm, vm, backlog)`. A `PlaybookService`
  is created on the line above and passed to the executor and meta-agent,
  but not to discovery.
- `src/soren/cli/discover.py`: `discovery = TaskDiscovery(settings, llm, vm, backlog)`.

So trained login and discovery flows are never used during discovery. The
agent always falls back to the adapter's generic navigation prompt, and
training those flows has no effect on `soren cycle run` or
`soren discover refresh`.

## Current state

- Unit tests (`tests/services/test_task_discovery_playbooks.py`) construct
  `TaskDiscovery(..., playbook_service=...)` directly, so they pass.
- No test covers the CLI wiring.

## Proposed approach

1. Pass `playbook_service=playbook_svc` in `cli/cycle.py`. Create a
   `PlaybookService` in `cli/discover.py` and pass it too.
2. Add CLI-level tests that patch `TaskDiscovery` and assert it receives a
   `playbook_service`. This follows the pattern in
   `tests/cli/test_train_command.py`.
3. Consider making `playbook_service` a required keyword argument of
   `TaskDiscovery` so this can't regress silently. That touches every test that
   constructs it.

## Acceptance criteria

- Both CLI entry points construct `TaskDiscovery` with a `PlaybookService`.
- A regression test fails if either call site drops it.
- A manual or e2e check confirms a trained login flow appears in the
  discovery navigation prompt.
