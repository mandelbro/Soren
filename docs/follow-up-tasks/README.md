# Follow-up Tasks

Scoped work items that were deliberately deferred from a feature, with enough
context to pick them up later without re-doing the investigation. Each file
has a status, origin, the current state of the code, proposed approaches,
open questions, and acceptance criteria.

When a task is picked up, move its status to *In progress* and link the PR.
When it's done, mark it *Done* (keep the file as a record) or delete it if the
resulting ADR/commit captures everything.

| # | Task | Status | Origin |
|---|------|--------|--------|
| [0001](0001-per-service-typing-delay.md) | Apply per-service `typing_delay_ms` | Open | ADR-0002, Phase 2 |
| [0002](0002-per-service-task-cooldown.md) | Apply per-service `cooldown_between_tasks_s` | Open | ADR-0002, Phase 2 |
| [0003](0003-llm-suggested-service-knowledge.md) | LLM-suggested service notes and shortcuts from training | Open | ADR-0002, Phase 4 |
| [0004](0004-discovery-playbooks-not-wired.md) | Discovery ignores login/discovery playbooks in production (bug) | Done ([#20](https://github.com/mandelbro/Soren/pull/20)) | ADR-0002, Phase 2 review |
