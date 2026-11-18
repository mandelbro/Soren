# 0002: Apply per-service `cooldown_between_tasks_s`

**Status:** Open
**Origin:** Service configuration Phase 2 ([ADR-0002](../adr/0002-service-configuration.md)); deferred by agreement.
**Related:** RFC §Per-service rate limiting (Open Question decision)

## Problem

`cooldown_between_tasks_s` is declared on `TaskSource` and `ServiceConfig` and
resolved with the usual precedence (default 30 s), but nothing enforces it.
The RFC promises per-service rate limiting, so that no service sees
back-to-back task executions that look automated.

## Current state

- `SorenMetaAgent.run_cycle()` runs **one task per cycle**. Cycles are
  started externally (`soren cycle run`, cron, etc.). An in-process `sleep`
  cannot enforce a cooldown, because the next task usually runs in a *new
  process*.
- **Task selection:** `TaskBacklog.get_eligible_tasks()` filters by status and
  `next_eligible_at`, then `TaskPriorityScorer` ranks the results. Neither
  knows about services.
- **What the `tasks` table records:** `started_at` / `completed_at` and
  `source_name`, which is enough to find "the last task run on this service".
- **Name mismatch:** resolution is keyed by *service* name, while tasks store
  `source_name`. They differ when a persona binds `name: acme-main` to
  `service: acmepanel`, so the cooldown lookup has to map source names to
  service keys through the personas.

## Proposed approach

1. Add `TaskBacklog.last_activity_by_source() -> dict[source_name, datetime]`,
   computed from `MAX(COALESCE(completed_at, started_at))` over non-queued
   tasks.
2. In `run_cycle()` step 4 (Select):
   - resolve each eligible task's service, caching per service key;
   - drop tasks whose service had activity within `cooldown_between_tasks_s`;
   - use the persona-to-service mapping to group source names that share a
     service.
3. If every eligible task is cooling down, end the cycle with a report note
   (`cooldown_skipped: {service: seconds_remaining}`) rather than sleeping.

## Open questions

1. **Scope:** should the cooldown apply per service (shared across personas,
   which protects the service) or per persona-and-service (which protects each
   identity)? The RFC wording suggests per service.
2. **Failures:** does a failed or dead-lettered attempt start the cooldown?
   A failed login attempt is still traffic, so probably yes.
3. **Discovery:** should discovery runs count as activity?
4. **Config errors:** how should selection treat a service whose config fails
   to resolve? The task will fail with `config_error` anyway. Skip resolving
   it during selection and let execution dead-letter it.

## Acceptance criteria

- A task whose service was active within its cooldown is not selected; the
  next best task on a different service is selected instead.
- Cooldown changes in `config/services/<name>.yaml`, `settings.yaml`
  `services.<name>`, or `service_defaults` take effect without a restart.
- The cycle report explains why a cycle ended with nothing executed because of
  cooldowns.
- Unit tests use an in-memory backlog with controlled timestamps (no sleeping).
