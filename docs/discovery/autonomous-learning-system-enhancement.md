# Discovery: Autonomous Learning System Enhancement

## The One-Liner

The training system records steps but never actually learns -- credentials are invisible, each step starts amnesic, chrome crop requires manual incantation, there is no concept of a "service" that separates platform knowledge from persona identity, and the autonomous-first learning loop specified in the original discovery remains unwired.

## Date

April 8, 2026

## Status

Accepted. Current-state facts re-verified against the code on 2026-11-15 -- see [Section 11: Revision History](#11-revision-history) for what changed.

## Prior Work

`docs/discovery/autonomous-learning-system.md` designed the cascading playbook hierarchy, training session state machine, and auto-learning loop. Most of that infrastructure was implemented. This document addresses gaps that emerged during real-world training on the Acme Panel survey platform -- problems the original discovery did not anticipate because they only surface under live VLM execution -- AND reconnects the autonomous-first learning loop (original Section 5) that was deprioritized during initial implementation.

## RFC Reference

`docs/rfc.md` -- All proposals here comply with the four architectural invariants: vision-only execution, local-only inference, VM-isolated execution, GUI credential delivery. *(2026-11-17: the RFC amended local-only inference to local or Ollama cloud models; see RFC §Local or Ollama Cloud Models.)*

---

## 1. Problem Statement

### What the Learning System Should Do

After a human trains Soren on a service (Acme Panel, Beta Panel, etc.), the agent should be able to accept high-level instructions like "Search for available surveys and complete the first three" and execute them autonomously. Training captures the *how* -- how to log in, how to navigate to surveys, how to handle each step of a survey flow. The playbook serves as institutional knowledge that persists across sessions and personas.

Critically, the agent should also be able to learn WITHOUT human training. The original discovery (Section 5) specifies an autonomous-first loop: attempt autonomous execution, detect when stuck, reason about failure, try a different approach, and -- on success -- auto-generate a playbook. Human training is the FALLBACK, not the primary path. The current system has the building blocks (`StuckDetector`, `auto_playbook.py`, `execute_with_learning`) but they are not wired into a reasoning-based retry or autonomous exploration flow.

### What Actually Happens

During a Acme Panel login training session, two critical failures revealed systemic gaps:

**Failure 1: Credential Resolution Is Missing**
The instruction "Type the email stored in the user credentials for acmepanel" was passed verbatim to UI-TARS. `TrainingSession` has no access to `CredentialStore` and no mechanism to resolve credential references into actual values. UI-TARS received a natural language description of a credential rather than the credential itself, so it had nothing to type. The model thrashed, clicking randomly for 10 iterations per step.

Contrast with `TaskExecutor` (lines 426-466 of `task_executor.py`), which builds a credential handoff string containing the actual username/password and injects it into the agent's context via `PersonaContext`. Training mode has none of this wiring.

**Failure 2: Inter-Step Context Is Lost**
Each training step spawns a fresh `ComputerAgent` with a fresh `messages` list (`training_session.py:829-835`). Step 1 opens a login modal, but Step 2 has zero knowledge that a modal is now visible. UI-TARS starts each step as if it has never seen the application before.

In `TaskExecutor.execute()`, a single `ComputerAgent` instance persists across the entire task, maintaining a continuous `messages` list that accumulates screenshots and reasoning from every prior step.

**Failure 3: Step Budget Too Restrictive**
`_SINGLE_STEP_BUDGET = 10` limits each training step to 10 CUA iterations. Compound instructions ("click the login button, wait for the modal, type the email") need more headroom. Production execution uses 50.

**Failure 4: Chrome Crop Requires Manual Ceremony**
Every training session on a web application requires `SOREN_CHROME_CROP_Y=100`. This is an environment variable that must be set before launch. There is no service-level configuration, no auto-detection, and no documentation that makes this obvious to a new operator.

**Failure 5: Autonomous Behavior Is Absent**
`StuckDetector` detects stuck conditions but triggers only a boolean `should_offer_training` return. There is no reasoning step -- the agent does not receive context about what it tried, what the screen shows, and what was expected before retrying. Retry repeats the identical failed action. There is no autonomous *exploration* entry point for services that have no playbook yet.

> **Update (2026-11-15):** The original text also stated that `auto_playbook.py` is never called and that `FlowPlaybook` confidence fields are never incremented. Both are out of date: `SorenMetaAgent.run_cycle()` now calls `auto_generate_site_playbook()` / `auto_generate_host_playbook()` after successful runs and `PlaybookService.update_confidence()` after every run. See Section 2 and O6/O7 for the remaining gaps.

### Why This Matters

Without these fixes, training mode produces playbooks that cannot execute autonomously because: (a) credential steps reference descriptions instead of values, (b) multi-step sequences fail when context is lost between steps, (c) the chrome crop configuration that makes web training work is fragile and undocumented, and (d) the autonomous-first learning loop that should reduce dependence on human training does not function.

---

## 2. Current State Analysis

### Training Session Code Path

The `TrainingSession` class (`training_session.py`) implements a state machine: INIT -> PREREQ_REPLAY -> NAVIGATE -> STEP_LOOP -> FINALIZING -> SAVING -> DONE. The step loop calls `_execute_and_verify()` for each human instruction, which calls `_try_execute_step()` -> `_execute_single_step()`.

**`_execute_single_step` (line 778-899):**
- Creates a *new* `ComputerAgent` per step (line 829)
- Constructs a single-use `messages` list (line 835)
- Has no persona context, no credential handoff, no playbook content
- Uses a hardcoded VLM model from `task_executor.DEFAULT_VLM_MODEL`

**Versus `TaskExecutor.execute` (line 59-153):**
- Creates one `ComputerAgent` for the entire task (line 94)
- Maintains `messages` across all steps (line 90)
- Builds `PersonaContext` with playbook content, memories, step budget (lines 82-89)
- Composes credential handoff via `_build_credential_handoff()` (lines 426-466)

### Autonomous Execution Code Path

**`TaskExecutor.execute_with_learning()` (line 155):** Wraps `execute()` with `StuckDetector`. Returns `(TaskResult, should_offer_training)`. The executor itself does no post-run learning; that happens in the meta-agent:

- **On stuck** (`SorenMetaAgent.run_cycle()` step 7): attended mode prompts the operator via `InterventionCallbacks.on_stuck()` (train / skip / abort). Choosing *train* launches a `TrainingSession` for the execution flow (`_handle_train_intervention`). Unattended mode auto-skips after `scheduler.stuck_auto_skip_timeout`, records the source in `CycleReport.stuck_sources`, and applies a confidence floor (`UNATTENDED_CONFIDENCE_FLOOR = 0.1`). The next attended run warns about sources that were auto-skipped. *(Corrected 2026-11-15: this previously said "does nothing beyond logging".)*
- **On success** (step 6): the meta-agent calls `auto_generate_site_playbook()` and saves it unless a human-trained (`verified_by: human`) execution flow already exists. It calls `auto_generate_host_playbook()` for new host domains found in tab-switch steps (gated by `playbooks.auto_learn_host`), and then `update_confidence()` / `update_host_confidence()`. *(Corrected 2026-11-15: this previously said "no call to `auto_playbook.py`, no confidence increment".)*

**`StuckDetector`:** Detects same-screenshot, action loops, budget exhaustion. Returns `(is_stuck, reason)` but reason is not fed to the agent. Detection triggers exit, not adaptation.

**`auto_playbook.py`:** `auto_generate_site_playbook()` and `auto_generate_host_playbook()` are called from the meta-agent's post-success path (see above). They are not yet called from an exploration-specific path, because one does not exist (O6). *(Corrected 2026-11-15.)*

### Chrome Crop Implementation

`vnc_screenshot.py:_chrome_crop_y()` reads `SOREN_CHROME_CROP_Y` from the environment. The `_apply_crop()` function uses PIL to remove the top N pixels from VNC screenshots before they reach UI-TARS. The click wrappers in `_scale_xy()` (line 289-303) add the crop offset back to click coordinates so they land at the correct screen position.

The crop value is environment-only. Note that `soren.cli` calls `load_dotenv()` and the justfile uses `set dotenv-load`, so a value placed in `.env` applies to every session. The `services:` key in `config/settings.yaml` (line 36) exists but only holds `enter_key_behavior` overrides. There is no `chrome_crop_y` field on `TaskSource`, `SitePlaybook`, or any configuration model.

*(Added 2026-11-15)* The per-service `enter_key_behavior` override is currently unreachable:
- `load_settings()` does not parse `services:` or the top-level `enter_key_behavior`. `SorenSettings` has no field for either.
- Only `patches/enter_key_config.py:get_enter_key_behavior()` reads them, and it re-opens the repo's `config/settings.yaml` directly, so `--config <path>` has no effect on Enter-key behavior.
- The Enter-key patch calls `get_enter_key_behavior()` with no `service_id`, so only the top-level default is ever applied.

### Credential Store Wiring

`CredentialStore` lives at `services/credentials_service.py`. `TaskExecutor` initializes it from `Path("config/credentials.enc")` and `Path("data/credentials.key")` (lines 437-439). It resolves credentials by matching the `credential_ref` field on `TaskSource` against the encrypted store.

`TrainingSession.__init__` (lines 219-241) accepts `vm_manager`, `playbook_service`, `llm`, and `callbacks` -- but NOT `credential_store`, `settings`, or `persona`. The CLI `train_run` command (lines 247-307 of `train.py`) instantiates `TrainingSession` without any credential or persona wiring.

### Service Configuration Gap

`TaskSource` (in `models/task_source.py`) defines per-source settings: `name`, `type`, `url`, `credential_ref`, `typing_delay_ms`, `action_delay_ms`, `cooldown_between_tasks_s`, `enabled`. It is embedded in `Persona.task_sources`. There is no standalone "service" concept. The same service (Acme Panel) must be duplicated across every persona that uses it, with only `credential_ref` differing.

The `services:` key in `settings.yaml` provides a different kind of per-service override (currently just `enter_key_behavior`). These two configuration surfaces are disconnected.

*(Added 2026-11-15)* `typing_delay_ms`, `action_delay_ms`, and `cooldown_between_tasks_s` are declared on `TaskSource` (RFC §Per-service rate limiting) but no code reads them. Per-service rate limiting is specified but not implemented.

### Existing Components & Reuse Plan

**Will Reuse:**
- `CredentialStore.get()` and `_build_credential_handoff()` logic from `TaskExecutor` -- port into training session
- `PersonaContext.build()` -- already accepts playbook content and memories; training should use it for agent instructions
- `PlaybookService.resolve()` -- training should show the agent what's already known about a service
- `_chrome_crop_y()` and `_apply_crop()` from `vnc_screenshot.py` -- extend with config-driven values
- `_SAFARI_CHROME_PROMPT` from `training_session.py` -- conditional chrome guidance already working
- `StuckDetector` -- extend with reasoning context, do not replace
- `auto_generate_site_playbook()` and `auto_generate_host_playbook()` from `auto_playbook.py` -- wire into post-execution path
- `execute_with_learning()` from `task_executor.py` -- extend with reasoning retry and post-success playbook generation
- `FlowPlaybook.confidence` and `FlowPlaybook.consecutive_successes` fields -- already on the model, just need wiring

**Will Not Reuse (needs new work):**
- `TaskSource` model as service definition -- too tightly coupled to personas; need a standalone `Service` model
- `_execute_single_step()` in its current form -- needs persistent agent context instead of per-step agents
- Environment-only chrome crop -- needs config-model integration
- Current retry logic -- identical retry must be replaced with reasoning-based retry

**Consolidation Opportunities:**
- `TaskExecutor._build_credential_handoff()` should become a shared utility since both training and execution need it
- Chrome crop configuration exists in env var, training session, and VNC patch -- consolidate into a single source of truth on the service/site configuration
- Stuck detection + reasoning retry should be a composable utility usable by both `execute_with_learning` and future autonomous exploration mode

---

## 3. Gap Analysis

### O1: Chrome Crop Should Be Automatic or Service-Configured

**Current behavior:** Operator must set `SOREN_CHROME_CROP_Y=100` as an environment variable before every training or execution session involving a web browser. The value is read by `vnc_screenshot.py:_chrome_crop_y()` on every screenshot capture.

**Desired behavior:** Chrome crop is configured per-service (or per-site-playbook) and applied automatically when Soren operates on that service. No environment variable needed for normal operation.

**Technical gap:** No chrome crop field exists on `TaskSource`, `SitePlaybook`, or the proposed `Service` model. The crop value is hardcoded to the environment. `training_session.py:_chrome_crop_active()` duplicates the env-var check rather than reading from config.

**Proposed solution:**
- The `Service` model (see O4 below) carries `chrome_crop_y: int = 0`.
- When a training session or task execution starts for a service, the service's crop is in effect before any CUA operation begins.
- The env var stays as a debugging override (explicit env var > config value > 0 default).

**Impact if not fixed:** Every operator must memorize an undocumented environment variable. New operators will waste hours debugging why UI-TARS clicks on the Safari toolbar instead of webpage elements.

### O2: New Window Handling for Survey/Job Sites

**Current behavior:** Chrome crop is a static value applied uniformly to all screenshots. When a survey site opens a new browser window or tab, the crop value may be wrong: the new window might not be maximized, might have different chrome height, or might be a different browser entirely.

**Desired behavior:** When a new window or tab opens, Soren should assess the window state and adjust accordingly -- maximizing the window if needed and recalibrating the chrome crop for the new window's geometry.

**Technical gap:** `VMManager` has tab-awareness methods (`get_tab_count`, `get_active_tab_url`, `switch_to_tab`, `find_tab_by_domain`) but no window-geometry introspection. There is no mechanism to detect window size or maximize a window via the VM.

**Proposed solution:**
- `VMManager.maximize_active_window()` uses AppleScript to set Safari's window bounds to the screen size.
- Optional VLM calibration determines the boundary between browser chrome and page content in a fresh screenshot. It's one-shot: the verification model is asked where the browser toolbar ends and the webpage content begins, and the y-coordinate is cached for the session.
- For new tabs or windows during execution, the tab-transition handler (already in `TaskExecutor.execute_with_learning`) maximizes the window, and optionally recalibrates.
- If VLM calibration fails, the service-configured `chrome_crop_y` value applies.

**Impact if not fixed:** Surveys that open in new windows will have misaligned click targets. UI-TARS will click on browser chrome instead of survey elements, causing step budget exhaustion.

**Architectural note:** VLM-based calibration uses the local verification model (Qwen3-VL) to analyze a screenshot. This is vision-based analysis, not DOM inspection. Fully RFC-compliant.

### O3: Holistic Application Understanding (Inter-Step Context)

**Current behavior:** Each training step creates a new `ComputerAgent` instance with an empty message history. The agent has no knowledge of what happened in previous steps. Training produces a flat list of `PlaybookStep` objects with no relational context.

**Desired behavior:** The agent maintains a running mental model of the application across all steps in a training session. After training, the playbook should encode enough context that the agent can handle variations -- not just replay the exact sequence, but understand the application well enough to adapt when the UI changes slightly.

**Technical gap:** Three distinct context loss points:

1. **Within a training session** -- fresh `ComputerAgent` per step loses all accumulated visual and textual context
2. **Training session to playbook** -- `PlaybookStep` captures `instruction` and `expected_after` but not *why* or *what the agent was looking at*
3. **Playbook to autonomous execution** -- `PersonaContext.build()` converts playbook steps to numbered instructions but provides no visual reference or contextual reasoning

**Proposed solution (three-layer context architecture):**

**Layer 1: Persistent Agent Within Training Session**
A single persistent agent replaces the per-step `ComputerAgent` pattern (scope: one training flow, see the Decisions Log). The agent's `messages` list accumulates screenshots and reasoning across steps, giving each subsequent instruction full visual and textual context from all prior steps. Each step adds to the existing history rather than starting fresh. A per-step turn budget still bounds each instruction against runaway (see Q4).

**Layer 2: Enriched Playbook Steps**
Extend `PlaybookStep` with optional context fields:
```python
class PlaybookStep(BaseModel):
    seq: int
    instruction: str
    expected_after: str
    # ... existing fields ...
    context_note: str | None = None     # Why this step exists, what to look for
    screenshot_ref: str | None = None   # Already exists -- actually populate it
    precondition: str | None = None     # What must be true before this step
```

At training save, each step gets a VLM-generated `context_note`, such as "At this point the login modal is visible with email field focused." A step's `precondition` is the previous step's `expected_after`. Reference screenshots are saved to `data/training/<service>/<flow>/step_N.png`. The new fields change the playbook schema, so `SCHEMA_VERSION` becomes 2, and v1 playbooks migrate on load.

**Layer 3: Application Context Summary**
After a training flow completes, the LLM generates an application-level summary from the full step sequence:
```yaml
# Added to SitePlaybook or Service model
application_context:
  description: "Acme Panel is a survey platform. The main page has a navigation bar with Log in/Sign up buttons. Login opens a modal with email-first, then password fields."
  ui_patterns:
    - "Modal dialogs for authentication"
    - "Tab-based navigation for Studies/Profile/Settings"
  known_states:
    - name: "logged_out"
      indicators: ["Log in button visible in nav bar"]
    - name: "logged_in"
      indicators: ["Dashboard visible", "profile icon in top right"]
```

This summary is injected into `PersonaContext.build()` alongside the step-by-step playbook instructions, giving the autonomous executor the "holistic understanding" of the application.

**Impact if not fixed:** Training produces brittle playbooks that break on any UI variation. The agent can only replay exact sequences, not reason about the application. This is the gap between "follows recorded steps" and "understands how to use an application."

### O4: Services Data Model

**Current behavior:** Service configuration is embedded in `Persona.task_sources` as `TaskSource` objects. Same service duplicated across every persona. Service knowledge scattered across `config/settings.yaml`, `config/personas/*.yaml`, `data/playbooks/site/*.yaml`, and environment variables.

**Desired behavior:** A standalone `Service` model (`name`, `type`, `url`, `config: ServiceConfig`) in `config/services/*.yaml`. Personas reference services by name. Credentials remain persona-specific.

**Technical gap:** No `Service` model exists. `TaskSource` conflates service definition with persona binding.

**Proposed solution:** New `Service` and `ServiceConfig` Pydantic models. `ServiceConfig` holds `chrome_crop_y`, `enter_key_behavior`, `typing_delay_ms`, `action_delay_ms`, `step_budget`, `cooldown_between_tasks_s`. `TaskSource` gains optional `service: str` field -- backward compatible. Persona binding simplified to `{service: acmepanel, credential_ref: acmepanel, enabled: true}`.

**Impact if not fixed:** Every new persona duplicates service config. Changing a service URL requires editing every persona file.

### O5: Reasoning-Based Retry (StuckDetector Enhancement)

**Current behavior:** `StuckDetector.is_stuck()` returns `(True, reason)` but `execute_with_learning()` treats this as terminal -- execution stops, `should_offer_training=True` is returned. The reason string is never shown to the agent. Retry repeats the identical instruction.

**Desired behavior:** When stuck, the agent receives structured context (what it tried, what the screen shows, what was expected) and a reasoning prompt asking "Why might this have failed? What should you try differently?" Execution continues for N more steps. If STILL stuck after reasoning retry, THEN terminate.

**Technical gap:** No mechanism to compose a reasoning prompt from stuck context and inject it into the CUA agent's message stream. The loop breaks on stuck detection rather than attempting adaptation.

**Proposed solution:** A `StuckRecovery` class that composes reasoning prompts from stuck context (stuck_reason, recent_actions, last_screenshot, goal). The prompt is injected as a user message. Retry budget: 10 additional steps, max 1 reasoning retry per stuck detection. `TaskResult` carries the stuck reason and the reasoning prompt, for observability.

**Impact if not fixed:** The agent never learns from failure during a run. Three identical retries waste budget without gaining information.

### O6: Autonomous Exploration Mode

**Current behavior:** If a task's service has no playbook, the agent proceeds with generic instructions and frequently fails. There is no "exploration" entry point that combines goal-oriented execution with auto-playbook generation on success.

**Desired behavior:** When encountering a service with no playbook, the agent receives a goal ("learn to log in on acmepanel"), a URL, and credentials. It navigates, observes, reasons, acts. On success, auto-generates a playbook via `auto_playbook.py`. On stuck after reasoning retry, falls back to training mode. This is the autonomous-first loop from original discovery Section 5.

**Technical gap:** No `autonomous_explore()` method exists. The meta-agent does not distinguish "no playbook" from "playbook failed." *(Corrected 2026-11-15)* `auto_generate_site_playbook()` *is* called from the production path, but only after a successful `execute_with_learning()` run of an already-discovered task, and only for the `execution` flow. Nothing generates login or discovery flows autonomously.

**Proposed solution:** Add `TaskExecutor.autonomous_explore(service, flow_type, persona, computer)` that composes goal-oriented CUA execution with `StuckRecovery` and calls `auto_generate_site_playbook()` on success. The meta-agent calls this BEFORE falling back to training.

**Impact if not fixed:** Every new service requires human training. The autonomous-first design remains unimplemented.

### O7: Confidence Tracking

**Current behavior** *(corrected 2026-11-15; previously "Neither is ever updated")*: `PlaybookService.update_confidence(source_name, flow_type, success, task_type, min_confidence)` exists, and the meta-agent calls it after every execution:
- **Success:** `consecutive_successes += 1`, `confidence = min(1.0, confidence + 0.15)`, and `verified_at` is set to now.
- **Failure:** `consecutive_successes` resets to 0 and `confidence = max(min_confidence, confidence - 0.2)`. The floor is 0.1 when unattended mode auto-skips a stuck source.
- **Host playbooks:** `update_host_confidence()` applies the same +0.15/-0.2 rule.

**Desired behavior:** On each successful execution, increment `consecutive_successes` and update `confidence`. After N consecutive successes (default 3), mark as stable. On failure after stability, flag for review.

**Technical gap** *(revised 2026-11-15)*: The remaining gaps are:
- The implemented formula is additive (+0.15/-0.2), not the proposed `consecutive_successes / stability_threshold`.
- There is no `stability_threshold` setting, and no "stable" or "flag for review" state.
- Confidence is only updated for the `execution` flow. Login and discovery flows are never updated.
- `execute_with_learning` itself still does not feed results back; the meta-agent does.

**Proposed solution:** Add `PlaybookService.update_confidence(service_name, flow_type, success)` with increment/decrement logic. Formula: `confidence = min(1.0, consecutive_successes / stability_threshold)`. The meta-agent updates confidence after each execution. The stability threshold is a `SorenSettings` value (default 3).

**Impact if not fixed:** Confidence fields are dead data. Cannot distinguish proven playbooks from untested ones.

---

## 4. Autonomous Chrome Crop Design

**Resolution precedence** (highest wins):
1. `SOREN_CHROME_CROP_Y` env var (debug override)
2. `Service.config.chrome_crop_y` (from `config/services/*.yaml`)
3. `0` (no crop -- non-browser services)

**Window lifecycle:** New tab/window detected -> `VMManager.maximize_active_window()` (AppleScript sets Safari bounds to `{0, 0, 1024, 768}`) -> Optional VLM-based chrome boundary calibration (one-shot, cached per session) -> Apply crop.

**Service-aware crop injection:** The training CLI and meta-agent resolve service config before starting CUA operations. The resolved `chrome_crop_y` is set via `os.environ["SOREN_CHROME_CROP_Y"]` in-process so `vnc_screenshot.py` works without modification.

> **Superseded (2026-11-15) by ADR-0002:** Writing the resolved value into `os.environ` makes it indistinguishable from an operator's debug override. Once one service's value is injected, every later service in the same process would treat it as a top-precedence env override. The resolved value is instead carried in an explicit per-task service context that the patches read, and the env var remains a pure operator override. The field is also renamed `chrome_height_px` (settings use unit suffixes: `_ms`, `_s`, `_gb`). The full precedence order is in ADR-0002.

---

## 5. Holistic Context Architecture

### Three Context Levels

| Level | Scope | Stored In | Used By |
|-------|-------|-----------|---------|
| Step context | Single CUA iteration | `ComputerAgent.messages` | UI-TARS (next action) |
| Session context | All steps in a training flow | Persistent `messages` list | Training verification, retry |
| Application context | Everything known about a service | `Service.application_context` + `SitePlaybook` | Autonomous execution |

### Data Flow: Training to Autonomous Execution

Training session (persistent agent) -> Playbook generation (LLM summarizes steps into `PlaybookStep` objects with `context_note`, plus application context summary) -> Reference screenshots saved to `data/training/<service>/<flow>/` -> Autonomous execution via `PersonaContext.build()` which injects: (1) application context summary, (2) step-by-step playbook instructions, (3) credential handoff, (4) memory entries.

The architectural distinction between a macro recorder and an autonomous agent: the playbook provides the *vocabulary* (what elements exist, how navigation works), and the VLM provides the *reasoning* (how to combine those to accomplish a novel goal).

---

## 6. Integration Requirements

### Data Flow

**Data this feature needs from existing components:**
- `CredentialStore` -- resolved username/password for the active persona's service binding
- `Persona.task_sources` -- which services this persona operates on, with `credential_ref`
- `PlaybookService` -- existing playbooks (site, host, base) for cascade resolution
- `SorenSettings` -- scheduler config, LLM/VLM model names, step budgets
- `VMManager` -- VNC connection, tab introspection, AppleScript execution
- `StuckDetector` -- stuck condition detection (screenshots, action loops, budget)
- `LLMService` -- reasoning model for playbook generation, context summarization, stuck recovery prompts
- `auto_playbook.py` -- `auto_generate_site_playbook()` and `auto_generate_host_playbook()`

**Data this feature produces that other components consume:**
- `FlowPlaybook` with enriched steps (context_note, precondition) -- consumed by `PersonaContext.build()` for autonomous execution
- Application context summary -- stored on `Service` or `SitePlaybook`, consumed by `PersonaContext.build()`
- Updated `confidence` and `consecutive_successes` on `FlowPlaybook` -- consumed by future verification frequency optimization
- Auto-generated playbooks from autonomous exploration -- consumed by subsequent execution cycles
- `Service` config YAML files -- consumed by training CLI, meta-agent, and task executor for chrome crop, enter key behavior, step budget

### Assumption Alignment

**What does the feature name imply?**
"Autonomous Learning System Enhancement" implies the agent LEARNS AUTONOMOUSLY -- it can encounter a new service, figure out how to use it without human help, and improve its knowledge over time. The word "autonomous" sets the expectation that the agent leads the learning process.

**What would a human assume this feature does if they only read the title?**
A human would assume: "The agent can be pointed at a website it has never seen before and learn how to use it on its own. If it gets stuck, it reasons about why and tries something different. Over time, it gets more confident and reliable."

**What integration points are so obvious a human wouldn't think to state them?**
- A login training feature needs the actual login credentials, not a description of them
- An autonomous agent needs to know the URL of the service it's supposed to learn
- When the agent succeeds at something autonomously, it should save what it learned
- When the agent fails, the failure context should inform the next attempt
- Confidence should increase when the agent repeatedly succeeds

### End-to-End Smoke Test

Run `soren cycle run` with the example persona enabled and configured for acmepanel, no prior playbook. Confirm the agent autonomously navigates to acmepanel.example, discovers the login flow, enters credentials, and either succeeds (generating a playbook) or detects it's stuck and offers training mode.

**Specific verification steps:**
1. Delete `data/playbooks/site/acmepanel.yaml` if it exists
2. Ensure `config/services/acmepanel.yaml` has the correct URL and chrome crop
3. Ensure the example persona has `credential_ref: acmepanel` and valid encrypted credentials
4. Run `soren cycle run --persona example --max-cycles 1`
5. Observe: the agent should attempt autonomous exploration of acmepanel.example
6. If the agent succeeds at logging in: `data/playbooks/site/acmepanel.yaml` should contain a login flow with `verified_by: auto` and `confidence > 0`
7. If the agent gets stuck: logs should show reasoning-based retry ("Why might this have failed?") before offering training mode
8. If training mode is offered and completed: the resulting playbook should have resolved credentials (actual values, not "the email stored in credentials")

**If this smoke test cannot pass, what's missing?**
- No `autonomous_explore()` entry point (O6)
- No reasoning retry on stuck (O5)
- No auto-playbook call after success (O6)
- No confidence update after run (O7)

---

## 7. Risks and Constraints

### High Risk: Persistent Agent Memory Pressure

**Probability:** Medium. Each accumulated screenshot in the agent's message history consumes memory. A 20-step training session with 1024x768 PNG screenshots could accumulate 15-20 MB of image data in the messages list.

**Mitigation:** Use `only_n_most_recent_images=5` (same as `TaskExecutor` line 99) on the persistent `ComputerAgent`. This keeps the 5 most recent screenshots while preserving all text reasoning. For a 7B VLM running on 32GB Apple Silicon, this is well within budget.

**Detection:** `MemoryGuard` checks already run at cycle start. Training sessions should check before starting.

### Medium Risk: VLM Chrome Calibration Accuracy

**Probability:** Medium. Asking a VLM to identify the exact pixel boundary between browser chrome and webpage content may produce inconsistent results across different pages and window states.

**Mitigation:** VLM calibration is optional. The primary path is service-configured `chrome_crop_y` values. VLM calibration is a nice-to-have for new-window scenarios where the configured value may not apply. The calibration prompt should ask for a conservative estimate (round up to nearest 10px).

### Medium Risk: Reasoning Retry Causes Loops

**Probability:** Low-Medium. If reasoning produces the same action, the agent is stuck again immediately.

**Mitigation:** Max 1 reasoning retry per stuck detection, capped at 10 additional steps. Second stuck detection within reasoning retry is terminal. `StuckDetector` continues monitoring throughout.

### Medium Risk: Service Model Migration Complexity

**Probability:** Low-Medium. Introducing a new `Service` model requires migrating existing `TaskSource` configurations and updating all code paths that read service configuration.

**Mitigation:** Backward compatibility via the `service: str` optional field on `TaskSource`. Existing configs work unchanged. New configs use service references. Migration is incremental, not big-bang.

### Low Risk: Autonomous Exploration on Impossible Tasks

**Probability:** Low. Some services have CAPTCHAs or MFA that make autonomous exploration impossible.

**Mitigation:** Step budget limits time. After reasoning retry fails, falls back to training mode. Per Q7, track attempt count and cap at 3 failures before requiring human training.

### Low Risk: Persistent Agent Prompt Drift

**Probability:** Low. A persistent agent accumulates context that may cause the VLM to drift from the current instruction's intent.

**Mitigation:** Each step's instruction is clearly delimited in the messages. The `_SAFARI_CHROME_PROMPT` or its absence already provides grounding context. The verify-retry loop catches drift by checking actual outcomes against expected outcomes.

### Constraint: 32GB Apple Silicon Memory Budget

The model pipeline runs UI-TARS (7B, ~4GB VRAM via MLX), Qwen3-VL (8B, ~5GB via Ollama), and Gemma3 (27B, ~16GB via Ollama -- with fallback to 4B).

> **Update (2026-11-15):** These model sizes no longer match `config/settings.yaml`:
> - `llm.primary_model` is `gemma4:31b-cloud`.
> - `llm.fallback_model` is `gemma4:e4b`.
> - `verification.model` is `qwen3-vl:235b-cloud`.
>
> The `-cloud` tags are Ollama cloud-hosted models, which is also relevant to the "local-only inference" invariant cited in the RFC Reference section above. *(Resolved 2026-11-17: the RFC now allows Ollama cloud models for any role; credentials must never reach a cloud model.)* UI-TARS remains local via MLX. `meta_agent.MEMORY_ABORT_THRESHOLD_PCT` was raised to 92% "for cloud models". With `OLLAMA_MAX_LOADED_MODELS=1` and `keep_alive=0`, only one Ollama model is loaded at a time. The persistent agent's message history is CPU-memory only and does not compete with VRAM.

VLM chrome calibration adds one extra Qwen3-VL inference per session (not per step). Negligible impact.

---

## 8. Scope

### Orphaned Integration Points: Scope Declarations

| Integration Point | Disposition | Rationale |
|---|---|---|
| StuckDetector with reasoning | **IN SCOPE** (O5) | Core autonomous behavior -- the agent must reason about failure, not repeat it |
| Auto-playbook generation from successful runs | **IN SCOPE** (O6) | The primary mechanism for autonomous learning |
| Confidence tracking across runs | **IN SCOPE** (O7) | Required to distinguish proven playbooks from untested ones |
| Host playbook auto-learning | **OUT OF SCOPE** | Autonomous exploration (O6) generates a host playbook on the first success on a new host, but the full host-specific learning loop (extracting host-specific vs task-specific patterns) is deferred. Reason: host playbook quality depends on having multiple successful runs on different tasks within the same host, which requires confidence tracking to be stable first. Recommend as follow-up discovery. |
| Tab-aware execution improvements | **OUT OF SCOPE** | Tab awareness is already implemented in `TaskExecutor.execute_with_learning` (tab detection, domain switching, platform return). Window maximization (O2) connects to the tab-transition handler. Further tab improvements (e.g., multi-window orchestration) are deferred as they require real-world data on which survey hosts open new tabs/windows. |
| Credential resolution end-to-end test | **IN SCOPE** (Failure 1 + smoke test) | Training gets the persona's credentials (Failure 1). The smoke test (Section 6) verifies end-to-end credential resolution from encrypted store through PersonaContext to CUA agent. |

---

## 9. Open Questions

**Q1: Should `Service` definitions live in `config/services/` or `data/services/`?**
`config/` is version-controlled and human-edited. `data/` is runtime-generated. Service definitions start as human-authored but accumulate learned context (application_context). Recommendation: `config/services/` for the base definition, with `application_context` stored on `SitePlaybook` (already in `data/playbooks/site/`) to keep the clean separation.

**Resolved (2026-11-15, ADR-0002):**
- Service definitions live in `config/services/<name>.yaml`.
- They are project- or user-specific and gitignored, except for the shipped example (`config/services/acmepanel.yaml`), which mirrors the `config/personas/` convention. Because of this, the earlier assumption that `config/` is version-controlled does not hold for service files.
- Training may update these files (Phase 4 of the service-config work).

**Q2: How should credential references work in the service model?**
Currently `credential_ref` is on `TaskSource` (persona-specific). With the service model, should there be a default credential on the service and an override on the persona binding? Or keep credentials strictly persona-bound? Recommendation: keep strictly persona-bound. Credentials are identity, not service knowledge.

**Q3: Should VLM chrome calibration be mandatory or opt-in?**
VLM calibration adds ~2-3 seconds per session start and requires the verification model. For sites with known, stable chrome heights, the configured value is sufficient. Recommendation: opt-in via `ServiceConfig.auto_calibrate_chrome: bool = False`. Default to configured value.

**Q4: What is the step budget for the persistent training agent?**
With a persistent agent, the per-step budget limits how many CUA iterations one instruction can consume. The overall session has no budget (it runs until `/done`). Is 15-20 per step sufficient? Should there be a session-wide safety limit (e.g., 200 total iterations)? Recommendation: 20 per step, 200 per session.

**Q5: Should training mode support replaying from a saved playbook for editing?**
If a playbook has 10 steps and the user wants to re-record step 5, should they be able to replay steps 1-4 automatically and then enter interactive mode at step 5? This would be a significant UX improvement but is a separate feature. The `_prereq_replay` mechanism already replays login flows -- extending it to arbitrary step ranges is feasible but out of scope for this discovery.

**Q6: How many reasoning retries before giving up?**
The current proposal allows 1 reasoning retry (with a 10-step budget) before terminating. Should this be configurable? Should it depend on the stuck reason (e.g., budget exhaustion gets 0 retries, same-screenshot gets 1)? Recommendation: 1 retry for same-screenshot and action-loop, 0 for budget exhaustion (no point continuing if budget is nearly gone). Configurable via `StuckRecovery` constructor.

**Q7: Should autonomous exploration have a per-service attempt limit?**
If autonomous exploration fails on a service 3 times, should the system stop trying and require human training? Recommendation: yes, track `autonomous_attempts` on the service and cap at 3. After 3 failures, mark the service as `requires_training: true`.

---

## 10. Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Persistent agent scope | Per training flow (not per session) | A login flow and discovery flow have different contexts. Resetting between flows is cleaner. |
| Chrome crop config location | `ServiceConfig.chrome_crop_y` | Keeps crop value with the service it belongs to. Env var stays as debug override. *(Renamed `chrome_height_px` and applied via a per-task service context instead of `os.environ`; see ADR-0002.)* |
| Service files location | `config/services/*.yaml` | Human-authored. Application context goes to `SitePlaybook`. *(2026-11-15: gitignored except the shipped example; see Q1 and ADR-0002.)* |
| Credential handoff sharing | Extract to `soren.services.credential_utils` | Both `TaskExecutor` and `TrainingSession` need it. DRY. |
| Step budget increase | 10 -> 20 (training), keep 50 (production) | Training steps are more exploratory than production steps. 20 gives headroom without unbounded execution. |
| Backward compatibility | `TaskSource.service` is optional | Existing persona configs work unchanged. Migration is incremental. |
| Reasoning retry budget | 10 additional steps, max 1 retry | Enough to try a different approach without burning the full step budget. Budget exhaustion gets 0 retries. |
| Autonomous exploration entry | `TaskExecutor.autonomous_explore()` | Reuses existing CUA infrastructure. Composes with StuckRecovery and auto_playbook. |
| Confidence increment formula | `consecutive_successes / stability_threshold`, capped at 1.0 | Simple, deterministic, easy to reason about. Stability threshold is configurable. |
| Host playbook auto-learning | Out of scope | Depends on multiple successful runs across different tasks on the same host. Needs confidence tracking to be stable first. |
| Tab-aware improvements | Out of scope beyond window maximization | Existing tab awareness is functional. Further improvements need real-world data on survey host behavior. |
| Service config precedence *(2026-11-15)* | env (`SOREN_CHROME_CROP_Y`, crop only) > `settings.yaml` `services.<name>` > persona `task_sources[]` (explicitly-set fields only) > `config/services/<name>.yaml` > `settings.yaml` `service_defaults` / top-level defaults > built-in defaults | Operator overrides beat per-persona values, and per-persona values beat shared service definitions. This keeps legacy persona configs behaving exactly as before when a service file is added. See ADR-0002. |
| Invalid service file at runtime *(2026-11-15)* | Fail the task, do not fall back | A silently-wrong crop height produces misclicks that look like model failures. |
| Service notes in the agent prompt *(2026-11-15)* | Configurable via `include_notes_in_prompt` (default off), per service or globally via `settings.yaml` `service_defaults` | Lets operators compare results with and without notes. |
| Config-error handling *(2026-11-15)* | New `config_error` failure category, dead-lettered immediately, no training offer, no confidence change | Retrying can't fix a config file. |
| Keyboard shortcuts *(2026-11-15)* | Prompt hints only (`keys`, `action`, optional `when`) | The model is told what shortcuts exist and decides when to use them. Soren code does not dispatch them. Consistent with ADR-0001. |

---

## 11. Revision History

| Date | Change |
|------|--------|
| 2026-04-08 | Initial version. |
| 2026-11-15 | Re-verified the current-state claims against the code. All cited line numbers were still accurate. Corrected the following, which had become out of date: (1) the meta-agent *does* call `auto_generate_site_playbook()` / `auto_generate_host_playbook()` after successful runs (Failure 5, Section 2, O6); (2) `FlowPlaybook.confidence` / `consecutive_successes` *are* updated via `PlaybookService.update_confidence()`, with a different formula than proposed and for the execution flow only (Failure 5, O7); (3) stuck detection *does* trigger attended train/skip/abort intervention and unattended auto-skip with a confidence floor (Section 2); (4) the configured LLM/verification models are now Ollama `-cloud` models, not the local sizes in the memory-budget constraint (Section 7). Added facts the original missed: `load_settings()` ignores `services:`/`enter_key_behavior`, the per-service Enter-key override is unreachable, and the `TaskSource` rate-limit fields are never read (Section 2). Recorded service-config decisions (Q1, Decisions Log) and superseded the `os.environ` crop-injection design (Section 4) in favor of ADR-0002. Still accurate and unchanged: Failures 1-4 (training has no credentials, a fresh agent per step, a 10-iteration step budget, env-only crop), O2, O3, O5. |
| 2026-11-15 (later) | Service configuration Phases 1-5 implemented; ADR-0002 accepted. O1 (service-configured chrome crop, now `chrome_height_px`) and O4 (Service data model, `config/services/*.yaml`, `TaskSource.service`) are done, including training write-back and migration (`soren services migrate`). Still open from this document: Failures 1-3 (training credentials, persistent training agent, step budget), O2, O3, O5, O6, and the remaining O7 gaps. |
