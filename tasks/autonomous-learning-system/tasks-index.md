# Training Mode and CLI Framework -- Task Index

## Overall Project Task Summary

- **Total Tasks**: 23
- **Pending**: 5
- **Complete**: 18
- **Total Points**: 73
- **Points Complete**: 59

## Project: Training Mode and CLI Framework

- Task Source File: `docs/discovery/autonomous-learning-system.md`
- **Description**: Autonomous-first learning system with cascading playbook hierarchy (base/host/site), stuck detection, auto-playbook generation from successful runs, human-guided training mode as fallback, multi-tab execution awareness, and CLI commands for training and playbook management. Extends the existing PlaybookService and TaskExecutor with structured YAML playbooks, schema versioning, and a three-tier resolution chain.

### Point Estimate Rationale

The discovery document estimated 55 points across 5 phases. This task breakdown estimates 73 points. The delta is attributable to several factors: (1) the technical reviewer (S1) flagged Phase 1 at 16 points as underestimated given the scope of service evolution, schema versioning, and multi-file integration; (2) TDD requirements add overhead not fully accounted for in the discovery estimate; (3) the integration task (009) spans four files with distinct test requirements; (4) Task 015 (CLI refactor) re-pointed from 2 to 3 given 800-line split into 11 modules with lazy imports and test import path verification; (5) three gap tasks (021-023) added for stuck intervention UX, token safety valve, and unattended mode auto-skip. The estimates here reflect honest complexity assessment with full test coverage included.

### Sprint Planning Recommendation

Per the discovery document's recommended delivery schedule, adjusted per technical review:

- **Sprint 1a**: Phase 5 + Phase 1a (Tasks 001-005, 11 pts)
- **Sprint 1b**: Phase 1b + VMManager tabs (Tasks 006-009 + 018, 17 pts) — Task 018 moved up from Sprint 3 since it has zero code dependencies
- **Sprint 2**: Phase 2 (Tasks 010-013 + 022-023, 19 pts)
- **Sprint 3**: Phase 3 + Phase 4 end (Tasks 014-017 + 019-021, 26 pts)

Note: Sprint 3 is heavy at 26 pts. If velocity is insufficient, defer Task 017 (playbook management CLI, 2 pts) to a follow-up sprint.

### Simplification Decisions

The discovery document's technical review proposed three simplification opportunities. Decisions:

- **SIMP1 (Collapse host+base into shared tier for Phase 1)**: **Rejected.** The full three-tier model (base/host/site) is implemented from Phase 1 to avoid a breaking refactor in Phase 4 when multi-tab execution requires the host tier. Tasks 002-004 create all three tier models upfront.
- **SIMP2 (Defer `soren train verify` to Phase 4+)**: **Adopted.** The `soren train verify` command (discovery Section 10.7) is not included in these tasks. It will be scoped separately after the core learning system is operational.
- **SIMP3 (Defer screenshot consolidation to Phase 3)**: **Adopted.** Consolidating `TaskDiscovery._extract_image()` and `TaskExecutor._extract_screenshot_bytes()` into a shared `screenshot_utils.py` module is deferred. The Rule of Three is met when TrainingSession (Task 014) adds a third consumer, but the consolidation is not critical-path.

### Test Directory Convention

Tasks specify nested test directories (`tests/models/`, `tests/services/`, `tests/adapters/`, `tests/cli/`). The current codebase uses a flat `tests/` directory. During implementation, create the nested subdirectories as specified in each task, including `__init__.py` files in each new test subdirectory. This establishes a scalable test organization pattern for the growing test suite.

## Task File Index

- `tasks/autonomous-learning-system/tasks-1.md`: Contains Tasks 001 - 005 (5 tasks, 11 points) -- Phase 5 + Phase 1a: Data Models + Config
- `tasks/autonomous-learning-system/tasks-2.md`: Contains Tasks 006 - 010 (5 tasks, 17 points) -- Phase 1b: Service Evolution + Integration + Phase 2 Start
- `tasks/autonomous-learning-system/tasks-3.md`: Contains Tasks 011 - 015 (5 tasks, 20 points) -- Phase 2: Learning Loop + Phase 3 Start: Training + CLI Refactor
- `tasks/autonomous-learning-system/tasks-4.md`: Contains Tasks 016 - 020 (5 tasks, 18 points) -- Phase 3: CLI Commands + Phase 4: Multi-Tab + Host Playbooks
- `tasks/autonomous-learning-system/tasks-5.md`: Contains Tasks 021 - 023 (3 tasks, 7 points) -- Gap Tasks: Stuck Intervention UX + Safety Valve + Unattended Mode
