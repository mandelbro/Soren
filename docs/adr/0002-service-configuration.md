# ADR-0002: Service configuration files and precedence

**Status:** Accepted (2026-11-15, after Phase 5)
**Date:** 2026-11-15
**Related RFC sections:** §Per-service rate limiting (Open Question decision), §Alternatives Considered #1
**Related:** [ADR-0001](0001-training-mode-keyboard-shortcut-policy.md), `docs/discovery/autonomous-learning-system-enhancement.md` (O1, O4)

## Context

Every website or desktop application an agent uses is different. Some of that
knowledge belongs to the *service*, not to any one persona or task:

- how much window chrome sits above the usable area
- which keyboard shortcuts exist
- Enter-key quirks (for example, Slack needs Escape before Enter)
- step budgets and pacing
- short tactical instructions

Before this ADR, that knowledge was spread across four surfaces that did not
know about each other:

1. Persona `task_sources[]`: `url`, `type`, `discovery_hint`, and rate-limit
   fields, copied into every persona that uses the service. The rate-limit
   fields were never read.
2. `settings.yaml`: a top-level `enter_key_behavior` and a `services.<name>`
   block. `load_settings()` ignored both, and the per-service lookup was
   unreachable because the Enter-key patch never passed a service name.
3. The `SOREN_CHROME_CROP_Y` environment variable, which is global to the
   process (often set in `.env`).
4. Site playbooks (`data/playbooks/site/`), which hold learned *steps*
   rather than configuration.

Several agents may use the same service, and the training process needs
somewhere to record what it learns about one.

## Decision

1. **One file per service:** `config/services/<name>.yaml`, validated by the
   `Service` model. `<name>` is the source/service name, sanitized the same
   way as site playbook filenames (`soren.services.naming.safe_service_name`).
   An explicit `name:` must map to the file's stem.

   ```yaml
   version: 1
   name: acmepanel
   type: survey_platform
   url: https://acmepanel.example/home
   discovery_hint: ...
   notes: |            # free-form service knowledge; data, not YAML comments
     ...
   config:             # same shape as a settings.yaml services.<name> entry
     chrome_height_px: 100
     enter_key_behavior: enter_only
     step_budget: 50
     typing_delay_ms: 50
     action_delay_ms: 500
     cooldown_between_tasks_s: 30
     required_apps: [Safari]
     keyboard_shortcuts:
       - {keys: w, action: Switch to week view, when: no text field is focused}
     instructions: [...]
     include_notes_in_prompt: false   # also give `notes` to the agent
   ```

2. **Files are local.** Everything in `config/services/` is gitignored except
   the shipped example, `acmepanel.yaml`. This mirrors `config/personas/`.

3. **Precedence, highest first.** Scalars replace lower layers. Lists replace
   lower layers and are never merged, so an explicit `[]` clears a list.

   | # | Layer | Notes |
   |---|-------|-------|
   | 1 | `SOREN_CHROME_CROP_Y` env var | `chrome_height_px` only. Operator debug override. |
   | 2 | `settings.yaml` `services.<name>` | Global operator override. Looked up by service name, then by the legacy source name. |
   | 3 | Persona `task_sources[]` | Only fields *explicitly set* in the persona file, so model defaults never mask the service file. Blank strings count as unset. |
   | 4 | `config/services/<name>.yaml` | The service definition. |
   | 5 | `settings.yaml` `service_defaults`, then the legacy top-level `enter_key_behavior` | Global defaults for every service, in the same `ServiceConfig` shape. Added in Phase 2 so toggles like `include_notes_in_prompt` can be flipped for all services at once. |
   | 6 | Built-in defaults | Reuse the legacy `TaskSource` defaults. |

   Each resolved value records which layer supplied it (`ResolvedService.provenance`).
   A warning is logged when the env var replaces a *different* configured
   chrome height, because a stale value in `.env` would otherwise silently win.

4. **Personas bind to services** with `task_sources[].service: <name>`. When
   `name` is omitted it defaults to the service name. Credentials stay
   persona-bound (`credential_ref`). Legacy inline sources keep loading, and
   their inline values keep winning: with no `service:` field, the source name
   is used to look up an optional service file. *(Clarified 2026-11-17.)* If a
   service file with that name exists, a legacy source **does** pick up the
   settings it can't set inline: chrome height, Enter-key behavior, step
   budget, required apps, shortcuts, instructions and notes. This is
   deliberate: it is how an existing persona benefits from a new service file
   without being edited. To keep a legacy source fully unchanged, don't create
   a file with its name (or rename the source).

5. **Strict new files, lenient legacy surfaces.**
   - Service files reject unknown keys and invalid values. An invalid file,
     or an explicit `service:` reference with no file, raises
     `ServiceConfigError` / `ServiceNotFoundError`. At runtime the task fails
     rather than falling back, because a silently wrong chrome height produces
     misclicks that look like model failures.
   - The failure is classified `config_error` and dead-lettered on the first
     occurrence, since retrying can't fix a config file. Training is never
     offered for it, and playbook confidence is left unchanged. Discovery
     skips only the affected source.
   - The legacy `settings.yaml` `services:` block and top-level
     `enter_key_behavior` keep the old reader's tolerance: unknown keys and
     invalid values are ignored with a warning.

6. **The resolved config is applied through an explicit per-task service
   context, not `os.environ`.** The discovery document proposed writing the
   resolved crop into `os.environ["SOREN_CHROME_CROP_Y"]`. That makes a
   config value indistinguishable from the operator's override: every later
   service in the same process would see the previous service's value as a
   top-precedence env override. The screenshot and Enter-key patches instead
   read the active service from `soren.services.service_context` (a
   `ContextVar`), which the executor, discovery, and training set for the
   duration of their agent loop.

7. **Keyboard shortcuts are prompt hints.** The agent is told which shortcuts
   exist (`keys`, `action`, optional `when`) and decides whether to use them.
   Soren code never dispatches them itself. They are rendered in UI-TARS's
   own action syntax (`hotkey(key='cmd k') — Open search`) together with the
   service's `instructions`. `notes` are included only when
   `include_notes_in_prompt` is true, so results can be compared with and
   without them. This section is never truncated. The keystrokes travel the real-OS
   input transport, as ADR-0001 requires. Service shortcuts documented by the
   service itself (for example Google Calendar's `w` / `m`) meet ADR-0001's
   "standard hotkeys a real user would know" test.

## Rationale

- **One file per service** removes duplication across personas and gives
  training a single, obvious place to write service knowledge.
- **The precedence order** keeps existing deployments behaving exactly as
  before:
  - Explicit persona values beat a newly added service file, so adding a file
    never changes a value the persona already sets. Settings a persona can't
    set inline do come from a same-name file (see Decision 4).
  - The operator's `settings.yaml` overrides and the env var keep their
    "override" meaning.
  - Top-level `enter_key_behavior` stays a default, as the legacy reader
    treated it. If it overrode service files, no service could ever differ
    from the shipped `enter_only`.
- **Provenance** makes "why is the crop 100?" answerable without reading code.
- **`chrome_height_px`** (rather than the discovery doc's `chrome_crop_y`)
  names the geometry, not the mechanism, and follows the unit-suffix
  convention of the other settings (`_ms`, `_s`, `_gb`).

## Consequences

**Easier**
- Service knowledge lives in one place per service, shared by all personas.
- The chrome crop no longer needs a manual env var for each session.
- Per-service Enter-key overrides and rate limits get a real home.

**Harder**
- There is one more config surface. The precedence table is the contract,
  and `soren services show` (Phase 3) must display the resolved values with
  their provenance.
- Service files are local, so each machine needs its own. The example file
  and migration tooling (Phase 5) mitigate this.

**New constraints**
- New `ServiceConfig` fields must default to `None` so layering works. Each
  new field also needs a built-in default in
  `service_resolution.BUILTIN_DEFAULTS`.
- A `SERVICE_SCHEMA_VERSION` bump requires a migration, as with site
  playbooks.

## Alternatives Considered

1. **Keep everything in persona `task_sources`.** Rejected: it duplicates the
   service across personas and gives training no shared place to write.
2. **`settings.yaml` `services:` as the only home.** Rejected: it puts every
   service in one global file, which is awkward for training to rewrite, and
   that block must keep its existing "override" meaning.
3. **Service file above persona values.** Rejected: adding a service file
   would silently change the behavior of existing personas.
4. **Inject the resolved crop via `os.environ`.** Rejected; see Decision 6.
5. **Merge lists across layers.** Rejected: merged lists are hard to
   override, and "replace" matches how every scalar behaves.

## Implementation notes

- **Phase 1** (this ADR): `soren.models.service`,
  `soren.services.service_registry`, `soren.services.service_resolution`,
  `TaskSource.service`, and `SorenSettings.services` / `enter_key_behavior`.
  No runtime behavior changes.
- **Phase 2:** `soren.services.service_context` (`use_service`,
  `current_chrome_height_px`), `soren.services.service_prompt`, and
  `TaskExecutor` / `TaskDiscovery` / `TrainingSession` integration:
  - **Step budget:** an explicit constructor value wins, then the service's
    `step_budget`, then the playbook's `max_steps` cap.
  - **Pacing:** `action_delay_ms` sets the agent's `screenshot_delay`.
  - **Apps:** `required_apps` are opened via `VMManager.open_required_apps`.
  - **Errors:** `config_error` is added to the retry policy.
  - **Settings:** `service_defaults` is added to `settings.yaml`.
  - **Deferred:** `typing_delay_ms` and `cooldown_between_tasks_s` are tracked
    in `docs/follow-up-tasks/0001` and `0002`.
- **Phase 3:** `soren services list/show/validate` (and `just` recipes).
  `train run` resolves SOURCE by source name or service name (`--persona` to
  choose) and passes the resolved service to `TrainingSession`, so the
  playbook key is the matched source's name. The meta-agent's "train"
  intervention passes the task's service too.
- **Phase 4:** training write-back.
  - **Commands:** `/shortcut`, `/instruction`, `/note`, `/crop` (applies
    live), and `/service` stage changes (`soren.services.service_update`,
    `soren.services.training_service_commands`).
  - **Automatic proposals:** the navigation URL when the file has none, and a
    crop that only came from `SOREN_CHROME_CROP_Y`.
  - **Confirmation:** at `/done` the plan is shown with warnings (comments
    will be lost; a saved value is shadowed by a higher layer) and defaults to
    Yes.
  - **Writing:** `soren.services.service_writer.ServiceWriter` writes it with
    PyYAML: `|` blocks for multi-line text, validation and a round-trip check
    before writing, atomic replace, a managed header, and backups in
    `config/services/.backups/` capped at 3 per service.
  - **Deferred:** LLM-suggested knowledge, follow-up `0003`.
- **Phase 5:** migration and deprecations.
  - **`soren services migrate` (`service_migration`):** creates service files
    from explicit inline persona values. It never edits persona files, never
    overwrites a service file, and never moves `settings.yaml services.<name>`
    (those overrides rank above files). Personas that disagree on a value are
    reported and skipped. Personas that lacked a value are told they will now
    inherit it. Contributing personas resolve identically before and after
    (tested).
  - **Deprecations (`service_deprecations`):** top-level `enter_key_behavior`
    (use `service_defaults`), relying on `SOREN_CHROME_CROP_Y` (use
    `chrome_height_px`; the env var stays a debug override), and inline persona
    values without a service file or repeating it. These are reported by
    `soren services validate` and logged once per process at runtime. The
    shipped `settings.yaml` now sets `service_defaults.enter_key_behavior`.
