# Discovery: Autonomous Learning System with Training Mode Fallback

## The One-Liner

Soren needs an autonomous-first learning system that attempts self-discovery, auto-generates playbooks from successful runs, and falls back to human-guided training only when stuck -- supported by a cascading playbook hierarchy (base/host/site) and multi-tab execution awareness.

## Date

March 24, 2026 (revised March 28, 2026; C1/C2 resolution March 28, 2026)

## Status

Discovery -- ready for implementation planning

## RFC Reference

`docs/discovery/discovery-adapter-feasibility.md` -- Discovery pipeline architecture, adapter protocol

## Phase Gate

Post-Phase 6 enhancement -- extends playbook self-improvement system with autonomous learning, cascading rulesets, multi-tab execution, and human-recorded fallback sequences

---

## 1. Problem Statement

### What Is Happening

On the last Soren production run, the CUA agent loaded the task source URL (Sproutful) but failed to locate the login link. The agent exhausted its 30-step discovery budget probing the page without successfully authenticating.

The root cause is visible across all three adapters (`survey_platform.py`, `email_adapter.py`, `generic_form.py`). Every adapter uses the same generic instructions:

```
1. LOGIN: If you see a login page, use the stored credentials for this service.
2. DASHBOARD: Navigate to the survey/task listing page.
```

This tells the agent *what* to do but gives it zero information about *where* or *how*. The problem manifests at three distinct points in the lifecycle:

**Login**: Each site has a unique login flow -- Sproutful has a "Log in" link in a nav bar that opens a modal, Prolific redirects to an email-first/password-second flow, Gmail uses multi-page OAuth. The agent has no prior knowledge of these flows.

**Task Discovery**: After logging in, the agent needs to know WHERE the task list is. Different platforms organize dashboards differently -- which nav item, which tab, does it need to scroll to load lazy content.

**Task Execution**: Once a task is selected, it often **opens in a new browser tab or window** with a completely different interface. A survey on Sproutful might redirect to a Qualtrics form at `qualtrics.com/jfe/form/SV_xxx`, while a Prolific survey might open a Google Forms page. The agent must handle tab switching, unfamiliar host UIs, and returning to the platform afterward.

### Why the Generic Approach Fails

UI-TARS 1.5 7B is strong at element grounding (clicking the right thing once it knows what to look for) but weak at multi-step reasoning under ambiguity. The discovery-adapter-feasibility doc measured this gap: 24.6% on full OSWorld tasks vs. 88.2% on navigation-only benchmarks. All three flow types sit in the ambiguity zone.

The credential handoff in `task_executor.py` (line 187-227) solves the *what credentials* problem. The gap is entirely *how to use them on this specific site*, *where to find tasks*, and *how to complete them across tab boundaries*.

### What We Want Instead

An **autonomous-first learning system** with three capabilities:

1. **Try autonomously first** -- the agent attempts each flow using its own reasoning plus any applicable playbook rules, records its steps, and self-evaluates
2. **Auto-generate playbooks from success** -- when autonomous execution succeeds, extract a playbook from the step log (leveraging the existing `PlaybookService.write_back()` pattern)
3. **Fall back to human training when stuck** -- if self-evaluation detects failure, enter training mode with context of what was tried

This is supported by a **cascading playbook hierarchy** that separates common patterns from site-specific knowledge:

- **Base playbooks** (built-in): generic task-type rules (e.g., "survey" patterns)
- **Host playbooks** (learned): host-specific rules (e.g., Qualtrics navigation patterns)
- **Site playbooks** (per-source): source-specific rules (e.g., Sproutful's login modal)

---

## 2. Current Architecture Analysis

### Existing Components Inventory

| Component | File | Relevance |
|-----------|------|-----------|
| `PlaybookService` | `services/playbook_service.py` | Primary extension point. Has `write_back()` that LLM-summarizes step logs into reusable instructions. |
| `PersonaContext` | `services/persona_context.py` | Injects playbook content into CUA instructions (line 170). |
| `TaskDiscovery._phase1_navigate` | `services/task_discovery.py` | Navigation phase where playbooks must be injected. |
| `TaskExecutor` | `services/task_executor.py` | Execution loop with step budget, completion detection, verification. |
| `CredentialStore` | `services/credentials_service.py` | Encrypted credential storage. No changes needed. |
| `VMManager` | `services/vm_manager.py` | VM lifecycle, `connect_computer()`, browser reset. Has AppleScript-based Safari window management. |
| `DiscoveryAdapter` | `adapters/base.py` | Protocol with `navigation_prompt()`. Playbooks augment these prompts. |
| CLI | `cli.py` | Click-based, 801 lines. Training mode adds an interactive command. |

### Existing Components and Reuse Plan

**Will Reuse:**
- `PlaybookService.write_back()` -- the auto-playbook generation from successful autonomous runs uses this exact pattern
- `PlaybookService.get_relevant_sections()` -- already maps task type to sections; extend with flow section matching
- `PersonaContext.build()` -- already has `playbook_content` parameter; cascading playbooks feed through the same path
- `CredentialStore.get()` -- training session needs credentials
- `VMManager` -- VM lifecycle unchanged; extend with tab/window detection via AppleScript
- `TaskExecutor._is_completion_message()` and step logging -- stuck detection reuses the same output parsing
- `LLMService` -- used for auto-playbook summarization (same as existing write-back)

**Will Not Reuse (as-is):**
- `TaskDiscovery._phase1_navigate()` -- training mode replaces this with a human-guided loop; autonomous mode uses training output
- `DiscoveryAdapter.navigation_prompt()` -- training output augments these prompts
- `PlaybookService` markdown storage format -- site playbooks use YAML for machine-readability; `write_back()` output transitions from markdown to YAML (Section 17.1)

**Consolidation Opportunities:**
- Screenshot extraction logic exists in `TaskDiscovery._extract_image()` and `TaskExecutor._extract_screenshot_bytes()`. Both training mode and stuck detection are additional consumers -- consolidate into `src/soren/services/screenshot_utils.py`.
- The `discovery_hint` field on `TaskSource` is never referenced in adapter prompts. Site playbooks subsume this concept -- deprecate or repurpose as a pointer to the playbook.

---

## 3. Multi-Tab Execution Model

### The Problem

Survey platforms act as intermediaries. The typical flow is:

1. **Login + Discovery** happen on the survey *platform* (e.g., Sproutful at `sproutful.com`)
2. User clicks "Start Study" and the survey **opens in a new browser tab** at a completely different domain (e.g., `qualtrics.com/jfe/form/SV_xxx`)
3. The survey UI belongs to the survey *host* (Qualtrics, SurveyMonkey, Google Forms, Typeform)
4. After completion, the user must **return to the platform tab** to confirm submission

The current architecture assumes all execution happens on a single page. Nothing in `TaskExecutor` or the CUA agent handles tab/window transitions.

### How the Current Infra Handles It (It Doesn't)

`VMManager.reset_browser_state()` already manages Safari windows via AppleScript -- it closes all windows and opens a fresh one. The `_run_osascript()` method (line 232+) provides the primitive for tab/window introspection. But the agent loop in `TaskExecutor.execute()` has no awareness of tab changes.

### Design: Tab-Aware Execution

**New `VMManager` methods** (all via `_run_osascript()`):
- `get_tab_count()` -- count open Safari tabs in window 1
- `get_active_tab_url()` -- URL of currently active tab
- `switch_to_tab(index)` -- switch to tab by 1-based index
- `find_tab_by_domain(domain)` -- find tab whose URL contains the given domain

**Execution flow with tab awareness:**

1. Before clicking "Start Study", record current tab count and platform URL
2. After click, poll for new tab (tab count increases or active URL changes domain, up to 5s timeout)
3. Identify new tab's host domain and load appropriate host playbook
4. Execute survey using host + base playbook rules
5. After completion, switch back to platform tab via `find_tab_by_domain()`
6. Confirm submission on platform

When a new tab opens at `qualtrics.com`, the resolver checks `data/playbooks/host/qualtrics.yaml`, falls back to `data/playbooks/base/survey.yaml`, and combines both with the site playbook's execution flow.

### What the Site Playbook Execution Flow Records

The execution flow captures platform-side actions only. The actual survey-answering behavior lives in the host playbook. Key step attributes: `triggers_new_tab: true` signals an upcoming tab transition, `host_domain` identifies the expected survey host, `DELEGATE_TO_HOST` as an instruction is a sentinel that hands off to host-level playbook resolution, and `returns_to_platform: true` signals return to the original tab. This separation means site playbooks stay small (typically 3-4 steps for the platform wrapper) while host playbooks handle the survey UI complexity.

---

## 4. Cascading Playbook Architecture

### The Three-Tier Hierarchy

Instead of training every single survey site from scratch, separate knowledge into layers:

| Layer | Location | Authored By | Scope | Example |
|-------|----------|-------------|-------|---------|
| **Base** | `data/playbooks/base/<task_type>.yaml` | Ships with Soren | All surveys, all hosts | "Look for Next/Continue buttons, radio buttons, text areas, Submit" |
| **Host** | `data/playbooks/host/<host>.yaml` | Auto-learned or trained | All surveys on one host | "Qualtrics: progress bar at top, arrow for next, specific question layouts" |
| **Site** | `data/playbooks/site/<source>.yaml` | Per-source trained | One platform's flows | "Sproutful: click 'Start Study', confirm completion after return" |

### Resolution Order

When the executor needs instructions, it resolves: **site > host > base > generic CUA reasoning**.

`resolve_playbook_chain(source_name, host_domain, task_type, flow_type)` in `playbook_resolver.py` loads each tier, formats as labeled prompt sections ("Site-Specific Instructions", "Host Instructions", "General Guidelines"), and concatenates with a priority preamble: "Follow in order of specificity. Site-specific overrides host, which overrides general."

Layers do not merge step-by-step. The full text of each layer is included with clear priority headers. The CUA agent reads the combined instructions and follows the most specific applicable guidance -- the same pattern `PersonaContext.build()` already uses.

### Base Playbook Example (Ships with Soren)

`data/playbooks/base/survey.yaml` -- covers navigation patterns (Next/Continue/Submit buttons, progress bars), question types (multiple choice, text input, scale, matrix, dropdown), and completion signals (Thank you pages, completion codes). Structured as `patterns.navigation`, `patterns.question_types`, and `patterns.completion`.

### Host Playbook Example (Auto-Learned)

`data/playbooks/host/qualtrics.yaml` -- `host_domain: qualtrics.com`, `url_patterns: ["qualtrics.com/jfe/form/*"]`, navigation overrides (blue arrow buttons, thin progress bar at top), and quirks (animation delays, matrix scrolling). `learned_from: auto|human`.

### Updated Data Model

New fields on `PlaybookStep`: `triggers_new_tab: bool`, `host_domain: str | None`, `returns_to_platform: bool`. New models: `HostPlaybook` (host_domain, url_patterns, navigation dict, quirks list, learned_from: auto|human) and `BasePlaybook` (task_type, patterns dict). `PlaybooksConfig` extended with `base_playbooks_dir`, `host_playbooks_dir`, `site_playbooks_dir`, `auto_learn_host: bool`, `cascade_enabled: bool`. Full Pydantic models in Section 6.3.

---

## 5. Autonomous-First Learning Loop

### Overview

Instead of requiring human training on first contact with a new site, the agent attempts autonomous execution first, records everything it does, and self-evaluates the outcome. This matches how `PlaybookService.write_back()` already works -- it generates playbooks from successful executions via LLM summarization.

```
First encounter with new site:
  1. autonomous attempt (using base + host playbooks if available)
     |
     +-- success --> auto-generate site playbook --> done
     |
     +-- stuck  --> enter training mode (human fallback) --> save playbook --> done
     |
     +-- failure (not stuck, just wrong) --> record failure, retry next cycle

Subsequent encounters:
  autonomous execution with cascading playbooks (site > host > base)
```

### Stuck Detection Heuristics

`StuckDetector` records each step's screenshot bytes and action string, then checks three conditions:

1. **Same screenshot** for N consecutive steps (default N=3) -- byte equality comparison, reliable for VNC since identical framebuffer content produces identical PNGs
2. **Action loop** -- last 2N actions form a repeating pattern (e.g., click-scroll-click-scroll)
3. **Budget exhaustion** -- steps_taken >= 80% of step_budget with no completion signal

Returns `(is_stuck: bool, reason: str)`. Future optimization: perceptual hashing for near-identical screens.

### Auto-Playbook Generation from Successful Runs

When autonomous execution succeeds, `auto_generate_site_playbook()` formats the step log (reusing `PlaybookService._format_steps_for_llm()`) and prompts the LLM to extract structured `PlaybookStep` entries: imperative instructions, expected_after descriptions, credential field markers, and tab transition flags. Output is a YAML-parseable list that becomes a `FlowPlaybook`.

**Host playbook auto-learning:** On first successful execution on a new host domain, a separate LLM prompt extracts host-specific navigation patterns ("What about this is specific to the host UI vs. this particular survey?") and produces a `HostPlaybook`.

### Integration with TaskExecutor

`execute_with_learning()` wraps `TaskExecutor.execute()`. It passes the resolved playbook chain as a memory, runs with stuck detection, and returns `(TaskResult, should_offer_training: bool)`. On success: auto-generate playbooks. On stuck: signal training mode. On normal failure (verification failed, exception): retry next cycle without training.

### Learning Accumulation Over Multiple Cycles

Each successful run refines the playbook. The system tracks confidence based on consecutive successes:

| Run | Outcome | Action |
|-----|---------|--------|
| 1st | Autonomous success | Generate playbook (confidence: low) |
| 2nd | Success with playbook | Increase confidence; update playbook if steps differ |
| 3rd+ | Consistent success | Mark playbook as stable; reduce verification frequency |
| Any | Failure after stable | Flag playbook for review; increase verification |

This is tracked via `verified_at` and a new `confidence` field on each flow:

```python
class FlowPlaybook(BaseModel):
    verified_at: datetime | None = None
    verified_by: str | None = None       # "auto" or "human"
    confidence: float = 0.0              # 0.0-1.0, increases with consecutive successes
    consecutive_successes: int = 0
    max_steps: int = Field(default=20, ge=1)
    steps: list[PlaybookStep]
```

---

## 6. Site Playbook Design

### 6.1 Three Flow Types

A site playbook is a structured YAML file containing step-by-step instructions for one or more flows on a specific task source. Each flow type addresses a different phase:

| Flow | Purpose | When Used | Integration Point |
|------|---------|-----------|-------------------|
| `login` | Authenticate to the site | Before any other action | `_phase1_navigate()` before adapter nav prompt |
| `discovery` | Navigate to and identify the task list | After login, during Phase 1 | `_phase1_navigate()` after login steps |
| `execution` | Complete a specific task type | During task execution, may span tabs | `PersonaContext.build()` + tab-aware executor |

### 6.2 Site Playbook Data Model

```yaml
# data/playbooks/site/sproutful.yaml
source_name: sproutful
source_url: https://sproutful.com/home
version: 1

flows:
  login:
    verified_by: human
    confidence: 1.0
    auth_complete_indicator: "Dashboard with available surveys visible"
    steps:
      - {seq: 1, instruction: "Click 'Log in' in top nav", expected_after: "Login modal visible"}
      - {seq: 2, instruction: "Type username", expected_after: "Email in field", field: "credential:username"}
      - {seq: 3, instruction: "Click 'Next'", expected_after: "Password field visible"}
      - {seq: 4, instruction: "Type password", expected_after: "Dots in field", field: "credential:password"}
      - {seq: 5, instruction: "Click 'Sign In'", expected_after: "Dashboard visible"}

  discovery:
    verified_by: human
    confidence: 0.8
    discovery_ready_indicator: "Full task list visible"
    steps:
      - {seq: 1, instruction: "Click 'Studies' tab in sidebar", expected_after: "Study list visible"}
      - {seq: 2, instruction: "Scroll to load all studies", expected_after: "All loaded"}

  execution:
    survey:
      verified_by: auto
      confidence: 0.6
      completion_indicator: "Completion confirmation shown"
      steps:
        - {seq: 1, instruction: "Click 'Start Study'", expected_after: "New tab opens",
           triggers_new_tab: true, host_domain: "qualtrics.com"}
        - {seq: 2, instruction: "DELEGATE_TO_HOST", expected_after: "Survey complete in tab"}
        - {seq: 3, instruction: "Switch to Sproutful tab", expected_after: "Dashboard visible",
           returns_to_platform: true}
        - {seq: 4, instruction: "Click 'Confirm'", expected_after: "Marked complete"}
```

### 6.3 Pydantic Models

```python
# src/soren/models/site_playbook.py

SCHEMA_VERSION = 1  # Bump on breaking changes; see Section 17.2

class PlaybookStep(BaseModel):
    seq: int
    instruction: str
    expected_after: str
    field: str | None = None
    screenshot_ref: str | None = None
    triggers_new_tab: bool = False
    host_domain: str | None = None
    returns_to_platform: bool = False

class FlowPlaybook(BaseModel):
    verified_at: datetime | None = None
    verified_by: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    consecutive_successes: int = 0
    max_steps: int = Field(default=20, ge=1)
    steps: list[PlaybookStep]

class LoginFlow(FlowPlaybook):
    auth_complete_indicator: str = ""
    fallback_on_failure: Literal["stop_and_report", "generic"] = "stop_and_report"

class DiscoveryFlow(FlowPlaybook):
    discovery_ready_indicator: str = ""

class ExecutionFlow(FlowPlaybook):
    completion_indicator: str = ""

class SiteFlows(BaseModel):
    login: LoginFlow | None = None
    discovery: DiscoveryFlow | None = None
    execution: dict[str, ExecutionFlow] | None = None

class SitePlaybook(BaseModel):
    source_name: str
    source_url: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = SCHEMA_VERSION
    flows: SiteFlows
```

### 6.4 PlaybookService (Unified -- see Section 17.1)

The existing `PlaybookService` is evolved to handle both legacy markdown write-back and structured YAML playbooks. See Section 17.1 for the full unification strategy.

```python
class PlaybookService:
    """Unified playbook service: YAML site playbooks + markdown write-back."""

    # --- Site playbook CRUD (YAML, data/playbooks/site/) ---
    def load_site(self, source_name: str) -> SitePlaybook | None: ...
    def save_site(self, playbook: SitePlaybook) -> Path: ...
    def save_flow(self, source_name: str, flow_type: str, flow: FlowPlaybook,
                  task_type: str | None = None) -> Path: ...
    def list_site_playbooks(self) -> list[SitePlaybook]: ...
    def delete_site(self, source_name: str) -> bool: ...

    # --- Cascading resolution (replaces PlaybookResolver as method) ---
    def resolve(self, source_name: str, host_domain: str | None,
                task_type: str, flow_type: str) -> str: ...

    # --- Write-back (preserved from existing PlaybookService) ---
    async def write_back(self, source_name: str, task: Task,
                         step_log: list[dict], dry_run: bool = False) -> PlaybookUpdate: ...

    # --- Prompt formatting ---
    def to_prompt_instructions(self, flow: FlowPlaybook, flow_type: str,
                               credentials: StoredCredential | None = None) -> str: ...
    def update_confidence(self, source_name: str, flow_type: str,
                          success: bool, task_type: str | None = None) -> None: ...
```

### 6.5 Integration Points

#### Login + Discovery Flow Integration

Injected into `TaskDiscovery._phase1_navigate()`:

```python
# Login instructions + credential handoff prepended
if login_flow:
    cred = self._resolve_credential(source, persona)
    login_instructions = svc.to_prompt_instructions(login_flow, "login", cred)
    nav_prompt = f"{login_instructions}\n\nAfter login:\n\n{discovery_section}"

# Discovery playbook replaces generic adapter nav prompt
if discovery_flow:
    discovery_section = svc.to_prompt_instructions(discovery_flow, "discovery")
else:
    discovery_section = adapter.navigation_prompt(source, persona)
```

#### Execution Flow Integration (Tab-Aware)

The executor iterates execution flow steps with special handling for tab transitions: `triggers_new_tab` records pre-click tab count and polls for the new tab after the click, `DELEGATE_TO_HOST` resolves the host playbook chain and executes the full host flow, and `returns_to_platform` uses `find_tab_by_domain()` to switch back. Regular steps pass through to the CUA agent normally.

### 6.6 Architecture Summary

**First encounter**: autonomous attempt (base + host playbooks) -> success: auto-generate site playbook / stuck: training mode (human fallback). **Subsequent runs**: resolve cascade (site > host > base > generic) for login, discovery, and execution flows independently. Execution may span tabs (host playbook delegation) with return to platform. See Section 10.2 for the full state machine.

---

## 7. CLI Framework Evaluation

### 7.1 Why Evaluate Now

The current CLI (`cli.py`) is 801 lines of Click commands. Training mode introduces interactive complexity that Click was not designed for: step loops with user prompts, Rich terminal output during CUA execution, async integration (VM operations), and error recovery.

### 7.2 Recommendation

**Phase 1 (Training Mode): Rich + Click.** Keep existing Click CLI. Implement the training loop using `rich.console.Console.input()`, `rich.live.Live` for progress, and `rich.panel.Panel` for structured output. Zero framework migration risk.

**Phase 2 (Optional Typer Migration): Scope-controlled.** If the team adds 3+ more interactive features beyond training, migrate to Typer incrementally. Typer wraps Click, so this can happen command-group by command-group. NOT a prerequisite for training mode.

**Rationale:** The training loop's interactive requirements are fully satisfiable with Rich, which is already a dependency. A full comparison matrix (Click, Typer, Rich+Click, prompt_toolkit, Textual) was evaluated. Rich+Click delivers 80% of the interactive quality with zero migration cost.

---

## 8. Implementation Plan

### Phase 1: Site Playbook Infrastructure + Base Playbooks (Foundation)

**Goal**: Data models, storage services, cascading resolution, and prompt injection for all layers.

**Components:**
1. `src/soren/models/site_playbook.py` -- Pydantic models for all three tiers with `SCHEMA_VERSION` constant and version-aware loading (Section 17.2)
2. Evolve `src/soren/services/playbook_service.py` -- Unify into single service with YAML site playbook CRUD, cascading `resolve()`, and preserved `write_back()` (Section 17.1)
3. `data/playbooks/base/survey.yaml` -- Ship-with-Soren base survey playbook (already authored)
4. Schema versioning: version check on load, migration registry, validation of credential placeholders (Section 17.2)
5. Modify `task_discovery.py` -- inject login and discovery playbooks into `_phase1_navigate()`
6. Modify `persona_context.py` -- add `login_instructions` and `execution_instructions` parameters
7. Modify `task_executor.py` -- load cascading playbooks during `execute()`, pass to `PersonaContext`
8. Unit tests for unified playbook service, resolution, schema versioning, prompt injection, credential validation

**Dependencies**: None. Can be tested with manually-authored YAML files.

**Estimated effort**: 16 points (net neutral: removed separate `SitePlaybookService` and `PlaybookResolver` files, added schema versioning and credential validation; deferred screenshot consolidation to Phase 3 per SIMP3)

### Phase 2: Autonomous-First Learning Loop (Core Intelligence)

**Goal**: Stuck detection, auto-playbook generation from successful runs, confidence tracking.

**Components:**
1. `src/soren/services/stuck_detector.py` -- `StuckDetector` with screenshot comparison, loop detection, budget tracking
2. `src/soren/services/auto_playbook.py` -- Auto-generate site and host playbooks from step logs (extends `PlaybookService.write_back()` pattern)
3. Modify `task_executor.py` -- integrate stuck detection into execution loop
4. Modify `meta_agent.py` -- wrap execution with learning loop (try autonomous, detect stuck, offer training)
5. Confidence tracking: update `confidence` and `consecutive_successes` on each run outcome
6. Unit tests for stuck detection heuristics, auto-playbook generation, confidence updates

**Dependencies**: Phase 1 (needs playbook models and storage)

**Estimated effort**: 13 points

### Phase 3: Training Session + CLI Commands (Human Fallback)

**Goal**: Human-guided training as fallback when autonomous learning fails. CLI interface.

**Components:**
1. `src/soren/services/training_session.py` -- `TrainingSession` with callback-based I/O
2. Split `src/soren/cli.py` into `src/soren/cli/` package (pure refactor)
3. `src/soren/cli/train.py` -- Training commands (see UX section)
4. Rich-enhanced interactive loop with flow-specific prompts
5. Error recovery (retry/skip/abort) and partial save on Ctrl+C
6. Prerequisite flow auto-verification before training dependent flows
7. Unit tests with mocked callbacks

**Dependencies**: Phase 1 + Phase 2

**Estimated effort**: 13 points

### Phase 4: Multi-Tab Execution (Survey Host Handling)

**Goal**: Tab/window detection, host playbook resolution, platform return.

**Components:**
1. Extend `VMManager` with tab introspection methods (`get_tab_count`, `get_active_tab_url`, `switch_to_tab`, `find_tab_by_domain`)
2. `src/soren/services/host_playbook_service.py` -- Host playbook CRUD
3. Modify `task_executor.py` -- tab-aware execution loop with `DELEGATE_TO_HOST` handling
4. Modify `playbook_resolver.py` -- integrate host tier into resolution chain
5. Auto-learning: generate host playbooks from first successful execution on a new host domain
6. Unit tests for tab detection, host resolution, tab switching

**Dependencies**: Phase 1 (needs cascading resolution)

**Estimated effort**: 11 points

### Phase 5: Adapter Prompt Enhancement (Immediate Win)

**Goal**: Improve generic prompts for sources without any playbooks.

**Components:**
1. Wire `discovery_hint` from `TaskSource` into all adapter `navigation_prompt()` methods
2. Improve generic login/navigation instructions with more specific fallback guidance
3. Update persona YAML schema documentation

**Dependencies**: None (parallel with Phase 1)

**Estimated effort**: 2 points

### Dependency Graph

```
Phase 5 (Adapter Hints) [2 pts, parallel] ──────────────────────────────────────┐
                                                                                  v
Phase 1 (Playbook Infra + Cascade) ──> Phase 2 (Autonomous Learning) ──> Phase 3 (Training CLI)
     [16 pts]                              [13 pts]                        [13 pts]
                                    \
                                     ──> Phase 4 (Multi-Tab + Host)
                                              [11 pts]
```

**Total estimated effort**: 55 points across 5 phases.

**Recommended delivery:**
- Sprint 1: Phase 5 + Phase 1 (18 pts) -- infrastructure and immediate wins
- Sprint 2: Phase 2 + Phase 4 (24 pts, parallelizable) -- autonomous learning and multi-tab
- Sprint 3: Phase 3 (13 pts) -- training mode CLI (now a fallback, lower urgency)

Note the shift: training mode (Phase 3) is no longer the first thing to build. The autonomous learning loop (Phase 2) delivers value sooner because it works without human intervention. Training mode becomes the safety net for cases where autonomous learning fails.

---

## 9. Risk Analysis

### High Risk: Autonomous Playbook Quality

**Probability**: Medium-High. **Impact**: Auto-generated playbooks cause regressions. **Mitigation**: Auto-playbooks start at `confidence: 0.0, verified_by: "auto"`; first reuse is a verification run (discard on failure); human-trained always takes priority; cycle reports distinguish playbook-assisted vs. no-playbook failures. **Detection**: Compare with-playbook vs. without-playbook success rates.

### High Risk: Playbooks Become Stale

**Probability**: High. **Impact**: Agent follows outdated instructions, wastes step budget. **Mitigation**: `verified_at` per flow, warn at 30d (login/discovery) or 14d (execution); `soren train verify` detects staleness by replaying; `fallback_on_failure: "generic"` for graceful degradation; confidence auto-decays on failure. **Detection**: Step counts and verification failure rates in cycle reports.

### High Risk: Cascading Rule Conflicts

**Probability**: Medium. **Impact**: Agent follows wrong instruction layer. **Mitigation**: Clear priority headers in merged prompt; `DELEGATE_TO_HOST` for explicit handoff; base playbooks are generic (patterns, not click targets); track success rates per-layer-combination. **Detection**: Log active playbook layers per execution.

### Medium Risk: Multi-Tab Reliability

**Probability**: Medium (AppleScript fragility). **Impact**: Agent loses track of tabs. **Mitigation**: Poll-based detection with 5s timeout; domain matching for return (not tab index); fallback closes all tabs and navigates manually; Safari popup settings managed in VM setup. **Detection**: `action_type: "tab_switch"` in step log.

### Medium Risk: Training Mode UX Frustration

**Probability**: Medium. **Impact**: Users avoid training. **Mitigation**: Training is the fallback path (most playbooks auto-generated); Rich panels for readability; manual YAML authoring as alternative; `--preview` flag for screenshots.

### Medium Risk: Combinatorial Complexity of Execution Playbooks

**Probability**: Medium. **Impact**: Maintenance scales multiplicatively (source x task_type x host). **Mitigation**: Cascading hierarchy means base covers ~70%, host covers quirks, site only needs platform-side actions. Auto-learning reduces manual authoring. Execution playbooks are optional.

### Low Risk: CLI File Becomes Unwieldy

**Probability**: Already happening (800 lines). **Mitigation**: Split into `src/soren/cli/` package in Phase 3.

---

## 10. UX Flow Design

### 10.1 Design Principles

Four principles govern all user-facing output:

1. **Progressive disclosure** -- most users never see playbook cascade details. Power users opt in with `--verbose` or drill-down commands.
2. **Transparency without noise** -- every cycle shows what the agent is doing (flow, step count, outcome). Details hide behind verbose/debug flags.
3. **Graceful degradation** -- when a playbook fails, the agent falls back to the next tier or generic reasoning. The user sees a warning, not a crash.
4. **Learning feels invisible** -- auto-generated playbooks, confidence tracking, and staleness detection happen without user action. The agent just gets better over time.

### 10.2 Autonomous Learning State Machine

The following state machine governs every flow (login, discovery, execution) within a cycle. Each flow runs through this loop independently.

```
RESOLVE PLAYBOOKS → AUTONOMOUS ATTEMPT (with StuckDetector monitoring)
  ├─ success → AUTO-GEN PLAYBOOK → UPDATE CONFIDENCE → FLOW DONE
  ├─ failure → RETRY NEXT CYCLE (log failure) → FLOW DONE
  └─ stuck → OFFER INTERVENTION [t] train / [s] skip / [a] abort
               ├─ train → TRAINING SESSION → FLOW DONE
               ├─ skip → FLOW DONE
               └─ abort → FLOW DONE
```

**Success** auto-generates a playbook and increments confidence. **Stuck** presents the user with a choice -- training inherits browser state. **Failure** (verification failed) retries next cycle silently.

### 10.3 User Journeys

#### Journey A: First-Time Source Setup

User adds a new source and runs `soren cycle run`. No site playbooks exist, so autonomous attempt with base playbooks. When login gets stuck (same screen x3), output shows what was tried and offers `[t] Train / [s] Skip / [a] Abort`. After training (5 steps recorded), discovery succeeds autonomously (auto-saves playbook), execution completes on qualtrics.com (auto-saves host playbook). Summary: "1 task done, 3 playbooks created."

Key UX decisions: agent shows what it tried before asking for help; training inherits browser state; successful autonomous flows generate playbooks silently; summary calls out playbook creation.

#### Journey B: Steady-State Autonomous Operation

After playbooks exist, cycles run unattended. Default output: flow name, playbook step count, outcome, duration, task count. `--verbose` adds per-step execution and confidence deltas. `--debug` adds raw CUA output, screenshot paths, LLM prompts.

#### Journey C: Playbook Staleness and Re-Training

When a site changes its flow and the playbook fails, the system shows the expected vs. actual state, flags the `verified_at` age, and offers `[t] Re-train / [s] Skip / [a] Abort`. After re-training, the old playbook is replaced, confidence resets, and remaining flows resume with their existing playbooks.

#### Journey D: Multi-Tab Survey Execution

Tab transitions render as nested trees in the output: `Execute > "Start Study" -> new tab: qualtrics.com > host playbook (15 steps) > return to sproutful.com > confirm`. Tab detection uses `VMManager.get_tab_count()`, `DELEGATE_TO_HOST` triggers host playbook resolution, and return uses domain matching (not tab index).

#### Journey E: Playbook Management

`soren train list` shows a Rich table with columns: Source, Login (step count or "none"), Discovery, Execution, Confidence, and staleness warnings. Host and base playbooks render in separate sections. `soren train show <source> --cascade` shows the resolved chain as the agent sees it.

### 10.4 Information Hierarchy

| Verbosity | What Shows | Flag |
|-----------|-----------|------|
| **Default** | Flow name, playbook status (step count or "autonomous"), outcome (checkmark/X), duration, task count | (none) |
| **Verbose** | Per-step execution, stuck detector scores, playbook confidence changes, auto-generation details | `--verbose` |
| **Debug** | Raw CUA output, LLM prompts, screenshot file paths, playbook resolution chain, tab introspection logs | `--debug` |

Design rationale: a user running `just begin-soren-cycle` (which calls `soren cycle run` in a loop) sees one compact block per cycle. If something fails, they re-run with `--verbose` to diagnose. `--debug` is for development only.

### 10.5 Notification and Interruption Design

**Attended**: stuck detection prints inline prompt with terminal bell (`\a`). **Unattended**: stuck sources auto-skip after configurable timeout (default: 30s), logged as `stuck_skipped: true`; next attended run warns with fix command. **Ctrl+C**: partial playbook saved to `<source>.partial.yaml`, resumable with `--from-step N`, never used in autonomous runs.

### 10.6 Training Session Detail

Training mode enters via explicit command or stuck detection prompt. Both use the same `TrainingSession` orchestrator. **Loop**: capture screenshot -> VLM description in Rich panel -> user types instruction (or `/help`, `/done`, `/abort`, `/undo`, `/steps`) -> CUA executes -> confirm `[y/n/d]` -> prompt `expected_after` (VLM pre-filled). **State machine**: `INIT` -> `PREREQ_REPLAY` (auto-replay login if training discovery/execution) -> `NAVIGATE` -> step loop -> `FINALIZING` -> `SAVING` -> optional `VERIFY`. Error states: `SAVE_PARTIAL` (abort/Ctrl+C), `ERROR_RECOVERY` (retry/skip/abort).

### 10.7 Command Reference

| Command | Description |
|---------|-------------|
| `soren cycle run [--verbose] [--debug] [--dry-run]` | Full cycle: login, discover, execute, finalize |
| `soren train <source>` | Train all flows for a source sequentially |
| `soren train <source> --flow login\|discovery\|execution` | Train a specific flow |
| `soren train <source> --flow execution --task-type TYPE` | Train task-type-specific execution flow |
| `soren train <source> --from-step N` | Resume training from step N |
| `soren train list` | Playbook health dashboard (all tiers) |
| `soren train show <source> [--cascade]` | Show playbook; `--cascade` shows resolved chain |
| `soren train verify <source> [--flow TYPE]` | Autonomous replay to verify playbook |
| `soren train delete <source> [--flow TYPE]` | Delete playbook or specific flow |
| `soren history show [--limit N]` | Recent cycle reports with playbook and learning metadata |

---

## 11. Technical Deep Dives

### 11.1 Prompt Injection Strategy

Updated `PersonaContext.build()` injection order:
1. Demographic identity
2. Behavioral trait directives
3. Response style
4. Task context
5. **Cascading playbook instructions** (resolved via unified `PlaybookService.resolve()`: site > host > base)
6. Relevant memories
7. Step budget

Note: the legacy "Service playbook instructions" slot is removed. The unified `PlaybookService` feeds cascading instructions through slot 5. See Section 17.1.

For `TaskDiscovery._phase1_navigate()`, injection order:
1. Login playbook instructions + credential handoff
2. Discovery playbook instructions (or generic adapter nav prompt if no discovery playbook)
3. `DISCOVERY_READY` sentinel instruction

### 11.2 Credential Placeholder Resolution

Login playbooks use `field: "credential:username"` and `field: "credential:password"`. Resolution happens at prompt generation time. Passwords are NOT inlined into playbook text; they remain in the credential handoff section.

### 11.3 Single-Step CUA Execution in Training Mode

Training executes one instruction at a time by creating a `ComputerAgent` per step and breaking after the first yield from `agent.run()`. This gives the training loop screenshot-by-screenshot control.

### 11.4 Screenshot Comparison for Stuck Detection

Byte-level comparison is the fast path (VNC produces deterministic PNGs for identical framebuffer content). Per S3 recommendation, supplement with histogram similarity from `verification/baseline.py` to catch cursor-blink false negatives. Future: perceptual hashing for near-identical screens.

---

## 12. Alternative Approaches Considered

| Approach | Why Rejected |
|----------|-------------|
| Pixel-coordinate recording | Brittle -- any CSS change breaks the playbook. NL instructions survive minor UI changes. |
| DOM selector recording | Soren operates via VNC screenshots. No DOM access without violating VM isolation. |
| Full LLM-generated playbooks from screenshots | This is the current approach, and it fails for first-encounter sites. However, it works well *after* one successful run -- hence the autonomous-first design. |
| Training-first (human required for every new site) | Too labor-intensive. Most sites can be handled autonomously with good base/host playbooks. Training should be the fallback. |
| Flat playbook hierarchy (no cascading) | Requires training every single site from scratch. The 80/20 rule: base playbooks cover 80% of patterns. |
| Typer migration as prerequisite | Coupling framework migration to feature delivery creates unnecessary risk. Rich+Click is sufficient. |
| Step-level merge of cascading playbooks | Over-engineered. LLMs handle prioritized prose instructions better than programmatic step interleaving. |

---

## 13. Open Questions

1. **Multi-factor authentication**: Should training mode pause for human 2FA entry, then resume recording?
2. **Playbook history**: Should old playbooks be archived (`sproutful.v1.yaml`) or overwritten on site changes?
3. **Multiple login flows per source**: Should playbooks support alternative login flows (email/password vs. social)?
4. **Host playbook sharing**: Can auto-learned host playbooks be shared across Soren instances via a registry?
5. **Stuck detection tuning**: `same_screen_threshold` and `loop_detection_window` need empirical tuning per site type.

---

## 14. Success Metrics

| Metric | Current | Target | How Measured |
|--------|---------|--------|-------------|
| Login success rate (Sproutful) | 0% | >90% | Cycle reports: Phase 1 nav completes within 10 steps |
| Discovery success rate | ~60-75% | >85% | Cycle reports: tasks discovered per run |
| Task execution success rate | ~50-65% | >80% | Cycle reports: tasks completed per run |
| Autonomous first-attempt success | N/A | >50% | Cycle reports: success without human-trained playbook |
| Auto-playbook quality (no regression) | N/A | <5% regression rate | Compare: with-playbook vs. without-playbook runs |
| Training session duration | N/A | <5 min per flow | Training session timestamp delta |
| Playbook staleness alerts | N/A | 100% flagged | CLI warning output for stale playbooks |
| Step budget consumption (with playbook) | 30 steps (exhausted) | <15 steps avg | Cycle report step counts |
| Multi-tab return success | N/A | >90% | Tab switch success in step log |

---

## 15. Estimated Total Effort

| Phase | Description | Points | Dependencies |
|-------|-------------|--------|-------------|
| 1 | Site Playbook Infra + Cascading Resolution + Base Playbooks | 16 | None |
| 2 | Autonomous-First Learning Loop (stuck detection, auto-playbook) | 13 | Phase 1 |
| 3 | Training Session + CLI Commands (human fallback) | 13 | Phase 1, 2 |
| 4 | Multi-Tab Execution + Host Playbooks | 11 | Phase 1 |
| 5 | Adapter Prompt Enhancement | 2 | None (parallel) |
| **Total** | | **55** | |

Optional (not scoped):
- Typer migration: 5-8 points (incremental, per command group)
- Host playbook sharing/registry: 5-8 points
- Inline screenshot display (Sixel/Kitty): 3-5 points
- Perceptual hashing for stuck detection: 2-3 points

---

## 16. Technical Review

**Reviewer**: Principal Engineer Review (automated)
**Date**: March 28, 2026
**Review Against**: Discovery document revision March 28, 2026 + current codebase on `main` branch (commit `fda2435`)

### Verdict: APPROVED WITH CONDITIONS

**Complexity Rating**: Complex (8+ points -- multiple integration points, new data models, new storage format, state machine, interactive CLI loop)
**Confidence Level**: High -- claims verified against actual source files

This is a well-structured discovery document that accurately diagnoses a real production failure and proposes a layered solution. The autonomous-first philosophy is the correct call -- making training the fallback rather than the prerequisite significantly reduces the activation energy for new sources. The three-tier playbook hierarchy is architecturally sound for the problem domain. The document demonstrates unusually good awareness of the existing codebase, with most integration points correctly identified.

That said, several issues need resolution before implementation begins.

---

### Critical Issues (Must Fix Before Implementation)

**C1. The `PlaybookService` / `SitePlaybookService` split creates a parallel universe problem.** **RESOLVED -- see Section 17.1.**

Resolution: Evolve the existing `PlaybookService` into a unified service. No separate `SitePlaybookService`. Legacy markdown write-back transitions to YAML output. No migration needed (zero existing markdown playbooks in production). Sections 6.4, 8, 10.7, and 11.1 updated accordingly.

**C2. No schema versioning strategy for YAML playbooks.** **RESOLVED -- see Section 17.2.**

Resolution: `SCHEMA_VERSION` constant in code, version check on every load, migration registry for breaking changes, auto-migration on load for supported versions. Section 6.3 updated with `SCHEMA_VERSION`.

---

### Significant Concerns (Should Address, May Impact Success)

**S1. Phase 1 at 16 points is underestimated.**

Original estimate assumed 4 new files + 3 modified files + tests. With the C1 resolution (evolve `PlaybookService` instead of creating separate `SitePlaybookService` + `PlaybookResolver`), the new-file count drops to 1 new file (`models/site_playbook.py`) + 1 evolved file (`services/playbook_service.py`) + 3 modified files + tests. Still substantial but more contained. **Recommendation**: Split Phase 1 into 1a (models + storage + base playbooks, ~10 pts) and 1b (resolver integration + caller migration, ~6-8 pts).

**S2. `PersonaContext.build()` prompt length risk.**

A fully resolved cascade (site + host + base + memories + demographics + traits) could exceed 2000 tokens, competing with conversation history and screenshots in UI-TARS's context window. **Recommendation**: Add `max_instruction_tokens` parameter (default ~1500). Truncate lowest-priority sections (base playbook, then memories) when exceeded. Log a warning.

**S3. Stuck detection byte-level comparison has false negative risk.**

Cursor blink, timestamps, or animations produce byte-different screenshots of identical screens, defeating stuck detection. **Recommendation**: Supplement byte comparison with histogram similarity from existing `verification/baseline.py` (`compare_with_baseline()`). Zero new dependencies, catches the cursor-blink case.

**S4. The `DELEGATE_TO_HOST` sentinel needs error recovery specification.**

Unspecified: what happens when no host playbook exists, when delegated execution gets stuck, and what the delegated step budget is. **Recommendation**: Fallback chain: (1) host playbook, (2) base playbook with host domain as context, (3) generic CUA reasoning. Delegated execution shares parent's remaining step budget. Stuck detection runs in the delegated sub-loop with the same heuristics.

**S5. Credential placeholder validation needed.**

Credential placeholders could leak into `instruction` or `expected_after` fields (logged), and invalid credential keys would cause runtime errors. **Recommendation**: Validation on load rejects credential placeholders in any field other than `field`, and validates referenced credential keys exist. Part of Phase 1 (see also Section 17.2).

---

### Minor Observations (Nice-to-Have Improvements)

**M1.** CLI line count claim says "801 lines" but the file is 800 lines. (Trivial.)

**M2.** `discovery_hint` on `TaskSource` confirmed unreferenced in adapter prompts. Phase 5's proposal to wire it in is validated.

**M3.** Architecture diagram in Section 6.6 was incomplete -- replaced with prose summary referencing Section 10.2 state machine.

**M4.** Confidence decay unspecified. Suggested values: `confidence = max(0.0, confidence - 0.2)` on failure, flag when below 0.3. Needs concrete specification before implementation.

**M5.** `PREREQ_REPLAY` in training needs bounds: if login playbook fails during discovery training, abort with message "Login playbook failed; train login first."

---

### Simplification Opportunities

**SIMP1. Collapse host+base into "shared" tier for Phase 1.** Ship two tiers (site + shared) instead of three. Shared playbooks keyed by "qualtrics" or "survey" avoid URL pattern matching and host detection. Split into host + base in Phase 4 when multi-tab lands. Saves ~3-5 points.

**SIMP2. Defer `soren train verify` to Phase 4+.** Autonomous replay verification is nice-to-have. Priority is recording and using playbooks. Saves ~2 points from Phase 3.

**SIMP3. Defer screenshot consolidation to Phase 3.** Rule of Three: training mode is the third consumer. Consolidate when it materializes. Saves ~1 point from Phase 1.

---

### Questions for the Author

**Q1.** Unattended stuck-skip: does the 30s timeout auto-skip, and does the browser reset between sources or only at cycle end? Current `run_cycle()` resets at end only (line 290).

**Q2.** `PersonaContext.build()` new parameters: keyword-only additions or replacement of `playbook_content`? The latter requires updating `TaskExecutor` simultaneously. (Partially addressed by C1 resolution: the cascading `resolve()` output replaces `playbook_content` in Phase 2.)

**Q3.** Login-without-discovery case: does `_phase1_navigate()` become two sequential CUA runs, or one run with concatenated login+adapter prompt?

**Q4.** `FlowPlaybook.max_steps` vs. `TaskExecutor` step budget (50): does the per-flow budget subdivide the total, or is it advisory?

**Q5.** Safari popup blocker settings: manual VM setup step or automated in `VMManager.ensure_ready()`? If manual, needs runbook reference.

---

### Summary of Recommended Changes Before Implementation

| ID | Type | Action | Effort Impact |
|----|------|--------|---------------|
| C1 | Critical | ~~Define PlaybookService deprecation/migration path~~ **RESOLVED** (Section 17.1) | Net 0 pts |
| C2 | Critical | ~~Add schema version check on YAML load~~ **RESOLVED** (Section 17.2) | +1 pt to Phase 1 |
| S1 | Significant | Split Phase 1 into 1a (models) and 1b (integration) | Replan only |
| S2 | Significant | Add max_instruction_tokens safety valve to PersonaContext | +1 pt to Phase 1b |
| S3 | Significant | Use histogram similarity for stuck detection (reuse baseline.py) | +0 pts (reuse) |
| S4 | Significant | Specify DELEGATE_TO_HOST fallback chain and budget sharing | Spec only |
| S5 | Significant | Validate credential placeholders on playbook load | +1 pt to Phase 1 |
| SIMP1 | Simplification | Collapse host+base into shared tier for Phase 1 | -3-5 pts from Phase 1 |
| SIMP2 | Simplification | Defer `train verify` to Phase 4+ | -2 pts from Phase 3 |
| SIMP3 | Simplification | Defer screenshot consolidation to Phase 3 | -1 pt from Phase 1 |

**Net effect if all simplifications adopted**: Phase 1 drops from 16 to ~11-12 points (plus 2-3 for critical fixes = ~14 points). Sprint 1 becomes achievable at 16 points.

---

## 17. Critical Issue Resolutions

### 17.1 Service Unification Strategy (C1 Resolution)

#### The Problem

The discovery proposed a `SitePlaybookService` (YAML, `data/playbooks/site/`) alongside the existing `PlaybookService` (markdown, `data/playbooks/`). Two services managing playbook content through two separate storage formats, two separate APIs, both feeding into `TaskExecutor` and `PersonaContext` -- that is a parallel universe waiting to diverge.

#### Why This Is Simpler Than It Looks

The existing `PlaybookService` writes markdown files to `data/playbooks/`. In production, **there are zero markdown playbook files** -- `data/playbooks/` contains only a `.gitkeep` and the new subdirectories (`base/`, `host/`, `site/`). The `base/survey.yaml` is the only content file. There is nothing to migrate.

The existing callers (`TaskExecutor` lines 79-84, `SorenMetaAgent` lines 199-215) use two `PlaybookService` methods: `load()` returns a string, and `write_back()` takes a step log and produces a summarized playbook. Neither caller depends on the markdown format -- they pass opaque string content to `PersonaContext.build()`.

#### Chosen Approach: Evolve PlaybookService (Option A)

Evolve the existing `PlaybookService` into a unified service that handles all playbook tiers. No new `SitePlaybookService`. No `PlaybookResolver` as a separate class -- resolution becomes a method on the unified service. See Section 6.4 for the full interface definition.

#### Migration Path

**Phase 1**: Evolve `PlaybookService` with YAML methods alongside existing `load()` and `get_relevant_sections()`. Both `TaskExecutor` and `SorenMetaAgent` continue calling the legacy methods unchanged. New callers (playbook resolver, training session) use the YAML methods. The `write_back()` method transitions to producing YAML output instead of markdown -- since there are no existing markdown playbooks, this is a clean cut.

**Phase 2**: When the autonomous learning loop integrates with `TaskExecutor`, update `TaskExecutor.execute()` to call `resolve()` instead of `load()` + `get_relevant_sections()`. Remove the legacy methods. Update `SorenMetaAgent` to use `resolve()` for playbook-assisted execution. Mark `load()` and `get_relevant_sections()` as `@deprecated` in Phase 1 so IDE warnings surface any remaining callers.

**Phase 3**: Legacy methods are deleted. The unified service is the only path.

#### Impact on Existing Callers

| Caller | Current API | Phase 1 (No Change) | Phase 2 (Switch) |
|--------|-----------|---------------------|------------------|
| `TaskExecutor.execute()` | `load()` + `get_relevant_sections()` | Unchanged | `resolve()` |
| `SorenMetaAgent.run_cycle()` | `write_back()` | Output format changes (md -> yaml) | Same |
| `PersonaContext.build()` | Receives `playbook_content: str` | Unchanged | Receives `resolve()` output |
| Training session (new) | N/A | Uses `save_flow()`, `load_site()` | Same |
| CLI commands (new) | N/A | Uses `list_site_playbooks()` | Same |

#### Why Not Option B or C

**Option B** (replace entirely): Requires changing all callers in Phase 1 and introduces integration risk across `TaskExecutor`, `SorenMetaAgent`, and `PersonaContext` simultaneously. The incremental evolution of Option A isolates risk.

**Option C** (two services + facade): Adds a third abstraction (`PlaybookResolver` facade) on top of two services. More moving parts for zero benefit -- the existing `PlaybookService` already owns the directory and the LLM dependency. Evolving it keeps a single owner.

### 17.2 Schema Versioning Strategy (C2 Resolution)

#### Design Principles

1. **Playbooks are learned knowledge, not disposable config.** A human spent 5 minutes recording a login flow, or the system spent compute auto-generating one from a successful run. Data loss is unacceptable.
2. **Single operator, local files.** No API contract concerns, no backward-compatibility with external consumers. Migration complexity should match audience size.
3. **Pydantic handles the common case.** Adding a new optional field with a default value (e.g., `timeout_seconds: int = 10`) requires zero migration -- Pydantic fills the default on load. Only breaking changes (renamed fields, changed semantics, removed fields) need explicit handling.

#### Version Policy

- `SCHEMA_VERSION` constant in `src/soren/models/site_playbook.py` (set to `1` at launch)
- Every YAML playbook file has a `version: N` field
- **Support window**: Current version and N-1 (one version back)
- **Additive changes** (new optional field with default): No version bump. Pydantic handles it.
- **Breaking changes** (renamed field, removed field, changed semantics): Bump `SCHEMA_VERSION`, add migration function.

#### On-Load Behavior

```python
def _load_and_validate(self, path: Path) -> SitePlaybook:
    """Load YAML, check version, migrate if needed, validate with Pydantic."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    file_version = raw.get("version", 1)

    if file_version > SCHEMA_VERSION:
        raise PlaybookVersionError(
            f"{path.name} is version {file_version}, but this Soren "
            f"supports up to version {SCHEMA_VERSION}. Update Soren."
        )

    if file_version < SCHEMA_VERSION:
        raw = _migrate(raw, file_version, SCHEMA_VERSION)
        # Write migrated version back to disk (preserves the data)
        path.write_text(yaml.dump(raw, sort_keys=False), encoding="utf-8")

    return SitePlaybook.model_validate(raw)
```

**Key behaviors:**
- **File version > code version**: Hard error. The file was created by a newer Soren. Message tells the user to update.
- **File version < code version**: Auto-migrate on load, write the migrated file back to disk. No data loss, no manual intervention.
- **File version == code version**: Direct Pydantic validation. If it fails, raise `PlaybookValidationError` with the file path and the Pydantic error details.

#### Migration Registry

```python
# Migration functions: (from_version, to_version) -> transform function
_MIGRATIONS: dict[tuple[int, int], Callable[[dict], dict]] = {}

def _register_migration(from_v: int, to_v: int):
    """Decorator to register a migration function."""
    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        _MIGRATIONS[(from_v, to_v)] = fn
        return fn
    return decorator

def _migrate(raw: dict, from_version: int, to_version: int) -> dict:
    """Apply sequential migrations from from_version to to_version."""
    current = from_version
    while current < to_version:
        next_v = current + 1
        migration = _MIGRATIONS.get((current, next_v))
        if migration is None:
            raise PlaybookVersionError(
                f"No migration path from v{current} to v{next_v}. "
                f"Manual conversion required."
            )
        raw = migration(raw)
        raw["version"] = next_v
        current = next_v
    return raw
```

#### Concrete Example: v1 to v2 Migration

Suppose v2 renames `expected_after` to `expected_result` on `PlaybookStep` and adds a required `timeout_seconds` field:

```python
@_register_migration(1, 2)
def _migrate_v1_to_v2(raw: dict) -> dict:
    """Rename expected_after -> expected_result, add timeout_seconds default."""
    for flow_data in _iter_flows(raw):
        for step in flow_data.get("steps", []):
            if "expected_after" in step:
                step["expected_result"] = step.pop("expected_after")
            if "timeout_seconds" not in step:
                step["timeout_seconds"] = 10  # sensible default
    return raw
```

The migration runs automatically on load. The original `v1` file is overwritten with the `v2` content. If the user is concerned about data loss, `data/playbooks/` is inside the project directory which is under git (or should be backed up).

#### Validation Beyond Schema

On load, the service also validates:
- **Credential placeholders** only appear in the `field` attribute, never in `instruction` or `expected_after` (security boundary per S5)
- **Step sequences** are contiguous (no gaps in `seq` values)
- **Flow references** are valid (e.g., `host_domain` in an execution step should match a known host playbook or base playbook task type)

Validation errors are reported with file path and specific field, not silent failures.

#### What This Does NOT Include

- **File-level backup before migration**: Not needed -- files are in a git-tracked directory. Document in release notes.
- **Batch migration CLI command**: Overkill. Migrations happen on load; `soren train list` pre-migrates all files.
- **Version negotiation or compatibility matrices**: Not an API. One instance, one version, auto-migrate forward.
