# Phase 3: Agent Execution Core — Work Items

**Phase Goal**: Execute a single task inside the VM using the CUA ComputerAgent with persona context, verification, and correction strategies.

**Estimated Phase Duration**: 3 weeks (Mar 24 – Apr 13, 2026)

**Total Story Points**: 26

**Exit Criteria (Phase-Level)**:
- A manually-created task executes successfully inside the VM via ComputerAgent
- ComputerAgent takes screenshots, reasons about UI, and completes the task
- All four CUA SDK patches are applied and functional
- LLMService communicates with Ollama using `keep_alive: 0` and MemoryGuard admission control
- Verification system detects task completion (Tier 1 + Tier 2)
- Correction strategies recover from common failures
- A JSON CycleReport is produced in `data/reports/`

---

## Dependency Graph

```
WI 3.1 (CUA SDK Patches)
  └── WI 3.4 (TaskExecutor) ◄── WI 3.3 (PersonaContext)
WI 3.2 (LLMService & MemoryGuard)
  ├── WI 3.4 (TaskExecutor)
  └── WI 3.5 (Verification & Correction)
```

**Parallel tracks**:
- **Track A** (patches + executor): WI 3.1 → WI 3.4
- **Track B** (LLM infrastructure): WI 3.2 → WI 3.5
- **Track C** (prompt construction): WI 3.3 → WI 3.4

**Phase dependency**: Phase 2 must be complete (VMManager, VNC screenshot, Computer connection).

---

## Work Items

### WI 3.1: [Patches] CUA SDK Patches Module — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phase 1 (WI 1.1 project structure)
- **Completed**: Yes

**User Story**: As a developer, I want all four CUA SDK interoperability patches extracted into a maintainable module with an `apply_all()` entry point, so that the ComputerAgent works correctly with MLX-based UI-TARS without crashes from known library incompatibilities.

**Description**: Extract the four CUA SDK patches from the desktop-agent's `conftest.py` into `src/soren/patches/`, updated for current library versions. Patch 1 (video processor stub) and Patch 2 (ProcessorMixin lenient type check) prevent crashes when PyTorch is absent. Patch 3 (mlx-vlm generate compatibility) wraps `GenerationResult` for tuple unpacking. Patch 4 (Enter key fix) appends explicit `keypress(["enter"])` when UI-TARS outputs `\n`, with configurable Escape-before-Enter behavior per service. Application order matters: Patches 1-2 before any `transformers` import, Patch 3 before any `mlx_vlm` import.

**Deliverables:**

1. **Patch Modules** — `src/soren/patches/`
   - `video_processor.py`: Patches 1 & 2 — stub video processor module in `sys.modules`, relax `ProcessorMixin.__init__` type check to accept `None` for video_processor
   - `mlx_generate.py`: Patch 3 — wrap `mlx_vlm.generate` to intercept `GenerationResult` dataclass and return `(result.text, {"prompt_tokens": result.prompt_tokens, "generation_tokens": result.generation_tokens})`
   - `enter_key.py`: Patch 4 — post-process action list from `convert_to_computer_actions()`; strip trailing `\n` from type actions, append `keypress(["enter"])`; support `escape_enter` mode (sends Escape before Enter) and `enter_only` mode (just Enter), configurable per service
   - `__init__.py`: `apply_all()` function that applies patches in correct order; called before any CUA SDK import; idempotent (safe to call multiple times)

2. **Configuration Integration**
   - `enter_key_behavior` field in service/adapter config: `"enter_only"` (default) or `"escape_enter"`
   - Patch 4 reads the configured behavior at runtime

3. **Test Suite** — minimum 12 tests (`tests/test_patches.py`)
   - `apply_all()` is idempotent (calling twice doesn't double-patch)
   - Patch 1: video processor stub registered in `sys.modules`
   - Patch 2: `ProcessorMixin.__init__` accepts `None` video_processor without raising
   - Patch 3: wrapped `generate()` returns `(text, usage_dict)` tuple from `GenerationResult`
   - Patch 3: `usage_dict` contains `prompt_tokens` and `generation_tokens` fields
   - Patch 3: original `generate()` behavior preserved (text content matches)
   - Patch 4: type action ending with `\n` produces stripped text + `keypress(["enter"])`
   - Patch 4: type action without `\n` passes through unchanged
   - Patch 4: `escape_enter` mode produces `keypress(["escape"])` then `keypress(["enter"])`
   - Patch 4: `enter_only` mode produces only `keypress(["enter"])`
   - Patch 4: multiple `\n` in middle of text not affected (only trailing)
   - Application order enforced: Patches 1-2 applied before Patch 3

**Acceptance Criteria:**
- [ ] `apply_all()` can be called before any CUA SDK import without errors
- [ ] After patching, `mlx_vlm.generate()` returns a tuple `(text, usage_dict)` compatible with `MLXVLMAdapter._generate()`
- [ ] After patching, type actions ending with `\n` produce explicit Enter keypress
- [ ] Enter key behavior is configurable: `enter_only` (default) or `escape_enter`
- [ ] Patches are idempotent — safe to call `apply_all()` multiple times
- [ ] Verify against installed `transformers` version that Patch 2 targets the correct class path
- [ ] Test suite includes minimum 12 tests, 100% coverage for all patch modules
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🔴 **Patch 3 fragility (mlx-vlm generate)**: `GenerationResult` fields may change in future mlx-vlm releases. **Mitigation**: Pin `mlx-vlm>=0.3,<0.5` in `pyproject.toml`; add assertion in patch that expected fields exist.
- 🔴 **Patch 4 criticality (Enter key)**: Without this patch, forms never submit. **Mitigation**: Integration test that verifies form submission via Enter key in a live VM.
- 🟡 **transformers class path drift**: Patch 2 targets `transformers.processing_utils.ProcessorMixin`. Path may change. **Mitigation**: Verify path at patch application time; log warning if target class not found.

**References:** Discovery §CUA SDK Compatibility (Section 2: Patch Status); RFC §Appendix: Prior Art (CUA SDK patches); Discovery §CUA SDK (Section 6: Patch Strategy)

---

### WI 3.2: [Infrastructure] LLMService & MemoryGuard — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: Phase 1 (WI 1.5 settings with LLMConfig)
- **Completed**: Yes

**User Story**: As a developer, I want an LLMService that wraps Ollama with automatic model lifecycle management and memory-aware admission control, so that all downstream LLM consumers (executor, verifier, discovery) share deterministic model loading behavior and graceful fallback under memory pressure.

**Description**: Implement the `LLMService` as the single interface for all Ollama interactions. Every API call includes `keep_alive: 0` for immediate model unload after response. The `MemoryGuard` provides multi-signal memory pressure detection using four signals: Ollama `/api/ps` (model memory commitment), macOS `memory_pressure` command (system assessment), `psutil.virtual_memory().percent` (coarse threshold), and `vm_stat` pageout detection (swap canary). When pressure triggers, the reasoning model downgrades to the configured fallback — this downgrade is sticky per-cycle to avoid thrashing.

**Deliverables:**

1. **LLMService** — `src/soren/services/llm_service.py`
   - `LLMService(settings: SorenSettings)`: wraps Ollama Python client
   - `async generate(prompt: str, model: str | None = None, images: list[bytes] | None = None) -> str`: text generation with `keep_alive=0`
   - `async generate_structured(prompt: str, schema: type[BaseModel], model: str | None = None) -> BaseModel`: structured output via JSON mode + Pydantic parsing
   - `async is_available() -> bool`: checks Ollama server reachability
   - `async get_loaded_models() -> list[dict]`: queries `GET /api/ps`
   - `async unload_model(model: str) -> None`: sends `keep_alive: 0` request to force unload
   - Model selection: uses `MemoryGuard.get_reasoning_model()` when no explicit model provided
   - Timeout handling: configurable per-call timeout with clear error on Ollama unresponsiveness

2. **MemoryGuard** — `src/soren/services/memory_guard.py`
   - `MemoryGuard(settings: SorenSettings)`: multi-signal admission control
   - `async get_reasoning_model() -> str`: returns primary or fallback model based on pressure
   - `_fallback_triggered: bool`: sticky per-cycle flag
   - `reset_cycle()`: called at cycle start to allow re-evaluation
   - Signal 1 — `_get_ollama_memory()`: query `/api/ps` for total `size_vram`
   - Signal 2 — `_get_memory_pressure()`: shell out to macOS `memory_pressure` command → "normal" / "warn" / "critical"
   - Signal 3 — `_get_psutil_pressure()`: `psutil.virtual_memory().percent`
   - Signal 4 — `_get_pageout_count()`: parse `vm_stat` for pageout growth
   - Trigger logic: fallback if `critical`, or `warn AND psutil > 85%`, or `psutil > 90%`, or pageout count growing
   - Metrics capture: all four signals logged at each check for cycle report and threshold calibration

3. **Test Suite** — minimum 14 tests (`tests/test_llm_service.py`, `tests/test_memory_guard.py`)
   - `LLMService.generate()` sends request with `keep_alive=0`
   - `LLMService.generate()` passes images when provided
   - `LLMService.generate_structured()` parses response into Pydantic model
   - `LLMService.generate_structured()` raises on invalid JSON response
   - `LLMService.is_available()` returns True when Ollama responds
   - `LLMService.is_available()` returns False on connection error
   - `LLMService.unload_model()` sends unload request
   - `MemoryGuard` returns primary model when no pressure
   - `MemoryGuard` returns fallback model on `memory_pressure == "critical"`
   - `MemoryGuard` returns fallback on `warn` + `psutil > 85%`
   - `MemoryGuard` returns fallback on `psutil > 90%` alone
   - `MemoryGuard` returns fallback on pageout growth
   - `MemoryGuard` fallback is sticky within a cycle (doesn't revert to primary)
   - `MemoryGuard.reset_cycle()` clears the fallback flag

**Acceptance Criteria:**
- [ ] Every Ollama API call includes `keep_alive: 0` (verified in tests)
- [ ] `MemoryGuard` uses all four signals: Ollama `/api/ps`, `memory_pressure`, `psutil`, `vm_stat` pageouts
- [ ] Fallback model selection is sticky per-cycle (once triggered, stays for entire cycle)
- [ ] `MemoryGuard.reset_cycle()` re-enables primary model evaluation for the next cycle
- [ ] `LLMService.generate_structured()` returns validated Pydantic model instances
- [ ] Ollama connection failures produce actionable error messages
- [ ] All memory metrics logged at each check point for cycle report integration
- [ ] Test suite includes minimum 14 tests, 100% coverage for both modules
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **psutil accuracy on Apple Silicon**: Over-reports by 3-8pp. **Mitigation**: psutil is one of four signals; composite check is more resilient. 90% threshold is conservative.
- 🟡 **memory_pressure command availability**: macOS-specific. **Mitigation**: Gracefully degrade to psutil-only if `memory_pressure` not available; log warning.
- 🟢 **Ollama keep_alive semantics**: Well-documented behavior. `keep_alive: 0` = immediate unload after response.

**References:** Discovery §Model Memory Budget (Sections 1, 3, 6: Ollama Management, Monitoring, Recommendations); RFC §Operational Contracts: Model Admission Control; RFC §Model Architecture

---

### WI 3.3: [Agent] PersonaContext Builder & Prompt Construction — Complete Implementation

- **Estimated Points**: 3
- **Dependencies**: Phase 1 (WI 1.4 Persona models)
- **Completed**: Yes

**User Story**: As a developer, I want a PersonaContext builder that transforms persona YAML data into effective ComputerAgent instructions, so that the agent behaves consistently with the persona's identity, traits, and preferences during task execution.

**Description**: Implement the `PersonaContext` builder that constructs the `instructions` parameter for the CUA `ComputerAgent` constructor. The context includes demographic identity, Big Five personality traits mapped to behavioral directives, response style preferences, and task-specific guidance. Also includes integration points (currently stubs) for playbook content and memory recall, which will be activated in Phase 6.

**Deliverables:**

1. **PersonaContext Builder** — `src/soren/services/persona_context.py`
   - `PersonaContext.build(persona: Persona, task: Task, playbook_content: str | None = None, memories: list[str] | None = None) -> str`: assembles the full instructions string
   - Demographic section: age, occupation, education, location → natural language identity statement
   - Trait mapping: Big Five scores → behavioral directives (e.g., high conscientiousness → "Be thorough and detail-oriented; double-check your work before submitting")
   - Response style: formality level, response length preferences, decision speed
   - Task context: task description, task type, service-specific guidance
   - Playbook integration: appends playbook instructions if available (stub returns empty in Phase 3)
   - Memory integration: appends relevant memories if available (stub returns empty in Phase 3)
   - Step budget notice: "You have a maximum of {budget} steps to complete this task"

2. **Trait-to-Behavior Mapping** — within `persona_context.py`
   - Each Big Five dimension (conscientiousness, openness, agreeableness, extraversion, neuroticism) maps to high/medium/low behavioral directives
   - Thresholds: 0.0-0.33 = low, 0.34-0.66 = medium, 0.67-1.0 = high
   - Mapping table is configurable but ships with sensible defaults

3. **Test Suite** — minimum 10 tests (`tests/test_persona_context.py`)
   - Build produces non-empty instruction string for valid persona + task
   - Demographic identity statement includes age, occupation, education
   - High conscientiousness persona includes thoroughness directive
   - Low extraversion persona includes reserved/thoughtful directive
   - Playbook content appended when provided
   - Playbook content omitted when None
   - Memory recall content appended when provided
   - Step budget notice included with correct budget number
   - Task description included in context
   - Different personas produce meaningfully different instructions

**Acceptance Criteria:**
- [ ] `PersonaContext.build()` returns a coherent instruction string suitable for `ComputerAgent(instructions=...)`
- [ ] All five Big Five traits produce behavioral directives at high/medium/low levels
- [ ] Demographic information is included as a natural language identity statement
- [ ] Playbook content is appended when provided, omitted when `None`
- [ ] Memory content is appended when provided, omitted when `None`
- [ ] Step budget notice is always included
- [ ] Test suite includes minimum 10 tests, 100% coverage for `persona_context.py`
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟢 **Prompt quality**: Behavioral directives are natural language — quality is tunable over time. **Mitigation**: Ship reasonable defaults; iterate based on task execution quality in Phase 5.
- 🟢 **Instruction length**: ComputerAgent `instructions` parameter accepts arbitrary-length strings. No known limit.

**References:** RFC §TaskExecutor (Prompt construction); RFC §Persona YAML Example; PRD §6.2 (Persona System)

---

### WI 3.4: [Agent] TaskExecutor with CUA ComputerAgent — Complete Implementation

- **Estimated Points**: 8
- **Dependencies**: WI 3.1 (patches), WI 3.2 (LLMService), WI 3.3 (PersonaContext), Phase 2 (VMManager)
- **Completed**: Yes

**User Story**: As a system operator, I want a TaskExecutor that drives the CUA ComputerAgent through a complete task inside the VM with step budgeting, step logging, and report generation, so that a manually-created task can be executed autonomously and its outcome recorded.

**Description**: Implement the core `TaskExecutor` that wraps the CUA SDK's `ComputerAgent.run()` with Soren-specific concerns. Uses the updated v0.7.26 API: `tools=[computer]` constructor, `instructions=` for persona context, message-list input to `run()`. The executor enforces a step budget (default 50), logs each step with screenshots and VLM reasoning, handles the async generator output from `agent.run()`, and produces a `CycleReport` JSON file. This is the highest-complexity work item in Phase 3 because it integrates all prior deliverables.

**Deliverables:**

1. **TaskExecutor** — `src/soren/services/task_executor.py`
   - `TaskExecutor(settings: SorenSettings, llm: LLMService, vm: VMManager)`: core execution service
   - `async execute(task: Task, persona: Persona, computer: Computer) -> TaskResult`: full task execution lifecycle
   - ComputerAgent construction with v0.7.26 API:
     ```python
     agent = ComputerAgent(
         model="mlx/mlx-community/UI-TARS-1.5-7B-6bit",
         tools=[computer],
         instructions=persona_context,
         screenshot_delay=0.5,
         only_n_most_recent_images=10,
     )
     ```
   - Agent loop: `async for result in agent.run(messages)` where `messages` is OpenAI-format message list
   - Step budget enforcement: count steps, terminate at budget limit
   - Step logging: extract reasoning, action type, coordinates from structured output
   - Screenshot capture: save before/after screenshots per step to `data/screenshots/`
   - TaskResult model: `success: bool`, `steps_taken: int`, `step_log: list[TaskStep]`, `duration_seconds: float`, `error: str | None`

2. **Agent Output Parser** — within `task_executor.py`
   - Parse the `ComputerAgent.run()` yield format: extract `reasoning`, `computer_call` actions, `computer_call_output`, and `message` outputs
   - Map CUA action types to Soren's `ActionType` enum
   - Extract reasoning text from `summary` blocks
   - Detect task completion from agent's final message output

3. **CycleReport Writer** — `src/soren/services/report_writer.py`
   - `write_report(report: CycleReport, output_dir: Path) -> Path`: serialize to JSON, write to `data/reports/{cycle_id}.json`
   - Maintain `data/reports/latest.json` symlink to most recent report
   - Include: timing data, step counts, screenshots captured, task result, model used

4. **Test Suite** — minimum 14 tests (`tests/test_task_executor.py`, `tests/test_report_writer.py`)
   - TaskExecutor constructs ComputerAgent with `tools=[computer]` (not `computer=`)
   - TaskExecutor constructs ComputerAgent with `instructions=` parameter
   - TaskExecutor sends message-list input to `agent.run()`
   - Step budget terminates execution at limit
   - Step budget allows completion before limit
   - Agent output parser extracts reasoning text from result
   - Agent output parser extracts action type and coordinates
   - Agent output parser detects completion message
   - TaskResult records correct step count and duration
   - Screenshot saved for each step with correct naming
   - Report writer produces valid JSON file
   - Report writer creates `latest.json` symlink
   - Report writer includes all CycleReport fields
   - TaskExecutor handles agent error without crashing (returns failed TaskResult)
   - @pytest.mark.integration: execute a simple task ("open Safari, navigate to example.com") in live VM

**Acceptance Criteria:**
- [ ] ComputerAgent constructed with v0.7.26 API: `tools=[computer]`, `instructions=`, message-list `run()` input
- [ ] Step budget (default 50) enforces hard termination of the agent loop
- [ ] Each agent step is logged as a `TaskStep` with action type, VLM reasoning, and screenshots
- [ ] `TaskResult` captures success/failure, steps taken, duration, and error details
- [ ] CycleReport JSON written to `data/reports/` with `latest.json` symlink
- [ ] Agent errors (timeout, VLM crash, unexpected exception) produce a failed `TaskResult` without crashing the executor
- [ ] Persona context from WI 3.3 is passed via `instructions=` parameter
- [ ] Test suite includes minimum 14 tests, 100% coverage for executor and report writer
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **ComputerAgent API changes**: The v0.7.26 API is documented but may shift in minor releases. **Mitigation**: Pin `cua-agent==0.7.26` during Phase 3; relax after stable.
- 🟡 **Agent output format parsing**: The structured output format is complex with multiple item types. **Mitigation**: Comprehensive parser tests; graceful handling of unexpected output types.
- 🔴 **Integration complexity**: This WI integrates patches, LLMService, PersonaContext, VMManager, and the CUA SDK. **Mitigation**: Each dependency has its own test suite; integration test validates the full chain.

**References:** Discovery §CUA SDK Compatibility (Sections 3, 5: API Changes, New Features); RFC §TaskExecutor interface; RFC §Execution Cycle Sequence

---

### WI 3.5: [Agent] Verification & Correction System — Complete Implementation

- **Estimated Points**: 5
- **Dependencies**: WI 3.2 (LLMService for Qwen3-VL), Phase 2 (VMManager for running commands)
- **Completed**: Yes

**User Story**: As a system operator, I want a two-tier verification system that detects task completion and a four-level correction system that recovers from common failures, so that the agent can self-assess its work and retry intelligently without human intervention.

**Description**: Implement the verification and correction systems adapted from `desktop-agent.md`. Tier 1 verification uses OpenCV histogram correlation against stored baselines for fast (~1ms) visual comparison. Tier 2 uses Qwen3-VL via Ollama for semantic screenshot analysis (~2-5s). The correction system escalates through four strategies: re-prompt with failure context, programmatic keypress (Escape + Enter), osascript window activation + input replay, and full osascript sequence (activate, select-all, delete, retype, submit).

**Deliverables:**

1. **Verification System** — `src/soren/verification/`
   - `verifier.py`: `ActionVerifier` class with `async verify(screenshot: bytes, expected_state: str) -> VerificationResult`
   - `baseline.py`: `BaselineManager` — store, load, compare baselines using OpenCV histogram correlation
   - Tier 1 — `compare_with_baseline(screenshot, baseline) -> float`: OpenCV histogram correlation, returns similarity score (0.0-1.0), ~1ms
   - Tier 2 — `semantic_verify(screenshot, prompt) -> VerificationResult`: send screenshot to Qwen3-VL via LLMService with verification prompt, parse pass/fail + reasoning
   - `VerificationResult` model: `passed: bool`, `confidence: float`, `tier_used: int`, `reasoning: str | None`, `duration_ms: float`
   - Decision logic: Tier 1 first; if confidence > threshold (default 0.85), accept; otherwise escalate to Tier 2

2. **Correction System** — `src/soren/verification/correction.py`
   - `CorrectionStrategy` enum: `REPROMPT`, `KEYPRESS`, `OSASCRIPT_ACTIVATE`, `FULL_OSASCRIPT`
   - `async apply_correction(strategy, computer, context) -> bool`: execute the correction and return success/failure
   - Level 1 — Re-prompt: return failure context to ComputerAgent for retry
   - Level 2 — Keypress: send `Escape` (dismiss modals) + `Enter` (submit)
   - Level 3 — osascript activation: `tell application "Safari" to activate` + replay last input
   - Level 4 — Full osascript: activate, select-all, delete, retype, submit
   - Escalation logic: try each level in order; stop on first success; return failure after all exhausted

3. **Test Suite** — minimum 12 tests (`tests/test_verification.py`, `tests/test_correction.py`)
   - Tier 1 baseline comparison returns high similarity for identical images
   - Tier 1 baseline comparison returns low similarity for different images
   - Tier 1 threshold gates Tier 2 escalation (below threshold → escalate)
   - Tier 1 threshold gates Tier 2 escalation (above threshold → accept)
   - Tier 2 sends screenshot to Qwen3-VL with verification prompt
   - Tier 2 parses pass/fail from VLM response
   - `VerificationResult` captures tier used and duration
   - Correction Level 1 returns context for re-prompt
   - Correction Level 2 sends Escape + Enter keypresses
   - Correction Level 3 sends osascript activation command
   - Correction Level 4 sends full osascript sequence
   - Escalation stops at first successful correction

**Acceptance Criteria:**
- [ ] Tier 1 verification completes in < 5ms for a single comparison
- [ ] Tier 2 verification sends screenshot to Qwen3-VL and parses semantic pass/fail
- [ ] Verification automatically escalates from Tier 1 to Tier 2 when confidence is below threshold
- [ ] Correction strategies escalate through all four levels in order
- [ ] Correction stops at the first successful level (doesn't over-correct)
- [ ] OpenCV histogram correlation produces meaningful similarity scores (near 1.0 for identical, near 0.0 for unrelated)
- [ ] Test suite includes minimum 12 tests, 100% coverage for verification and correction modules
- [ ] Build/lint/type-check pass

**Technical Risks:**
- 🟡 **Tier 1 false positives**: Histogram correlation is a coarse measure — visually different screens can score high if color distributions match. **Mitigation**: Tier 1 is a fast gate, not final judgment; Tier 2 catches false positives. Conservative threshold (0.85).
- 🟡 **Qwen3-VL verification reliability**: VLM may hallucinate pass/fail. **Mitigation**: Structured prompts with explicit "PASS" / "FAIL" tokens; parse response with regex, not free-text interpretation.
- 🟢 **Correction strategies**: Directly adapted from desktop-agent where they are proven working.

**References:** Discovery §CUA SDK Compatibility (Section 5: Callback System); RFC §TaskExecutor (Verification, Correction strategies); RFC §Appendix: Prior Art (action_verifier, obstacle_handler)

---

## Summary

| WI | Title | Points | Dependencies | Track |
|----|-------|--------|-------------|-------|
| 3.1 | CUA SDK Patches Module | 5 | Phase 1 | A (Patches) |
| 3.2 | LLMService & MemoryGuard | 5 | Phase 1 | B (LLM) |
| 3.3 | PersonaContext Builder & Prompt Construction | 3 | Phase 1 | C (Prompt) |
| 3.4 | TaskExecutor with CUA ComputerAgent | 8 | 3.1, 3.2, 3.3, Phase 2 | A+B+C (Integration) |
| 3.5 | Verification & Correction System | 5 | 3.2, Phase 2 | B (LLM) |
| | **Total** | **26** | | |

### Velocity Assumptions

- **Solo developer, 3-week phase**: ~9 points/week. The reduced velocity vs Phase 1 accounts for integration test overhead (live VM + real models required).
- **Week 1 target** (10 pts): WI 3.1 (5) + WI 3.2 (5) — patches and LLM infrastructure (parallelizable)
- **Week 2 target** (8 pts): WI 3.3 (3) + WI 3.5 (5) — persona context and verification
- **Week 3 target** (8 pts): WI 3.4 (8) — TaskExecutor integration (depends on all prior WIs)

### Test Coverage Summary

| WI | Minimum Tests | Coverage Target |
|----|--------------|-----------------|
| 3.1 | 12 | 100% for `patches/video_processor.py`, `mlx_generate.py`, `enter_key.py` |
| 3.2 | 14 | 100% for `services/llm_service.py`, `services/memory_guard.py` |
| 3.3 | 10 | 100% for `services/persona_context.py` |
| 3.4 | 14 | 100% for `services/task_executor.py`, `services/report_writer.py` |
| 3.5 | 12 | 100% for `verification/verifier.py`, `verification/correction.py`, `verification/baseline.py` |
| **Total** | **62** | |

### Exit Criteria Traceability

| Phase Exit Criterion | Verified By |
|---------------------|-------------|
| Manually-created task executes successfully in VM | WI 3.4 integration test |
| ComputerAgent takes screenshots and completes task | WI 3.4 AC |
| All four CUA SDK patches applied and functional | WI 3.1 AC |
| LLMService uses `keep_alive: 0` with MemoryGuard | WI 3.2 AC |
| Verification detects task completion | WI 3.5 AC |
| Correction strategies recover from failures | WI 3.5 AC |
| JSON CycleReport produced | WI 3.4 AC (report writer) |
