# Discovery: Step Latency Optimization for Training & Execution Pipelines

## TL;DR

A single "type URL and press Enter" action takes 30–60 seconds because the pipeline runs 4–6 VLM inferences per step, each prefilling 2,000–5,000 tokens through a 7B model at ~450 tok/s. We can cut per-step latency 40–60% by eliminating redundant VLM calls, tuning settle delays, and introducing a fast-path for deterministic actions.

## Problem

During interactive training (`just train <source> --flow login`) and autonomous execution (`meta_agent.run_cycle`), the wall-clock time per UI action is dominated by **model inference**, not browser interaction. A user entering a URL and pressing Enter — a sub-second physical action — triggers this pipeline:

| Phase | What happens | Typical cost |
|-------|-------------|-------------|
| CUA Turn 1 | UI-TARS prefills prompt + screenshot (~2.4k tokens) to plan action | **~5s** |
| CUA Turn 2 | SDK executes action on VM, waits `screenshot_delay` (0.5s), captures screenshot | **~1s** |
| CUA Turn 3 | UI-TARS prefills expanded context (~3.5k tokens) to emit completion message | **~8s** |
| Post-action settle | `_POST_ACTION_SETTLE_DELAY` sleep | **1.5s** |
| Screen description | `llm.generate()` with screenshot → Ollama VLM call | **3–8s** |
| Verification | `ActionVerifier.verify()` → Tier 2 VLM call (Qwen3-VL) via Ollama | **5–10s** |
| **Total** | | **~24–34s** |

For training, verification may retry up to 3 times (`_DEFAULT_MAX_VERIFY_RETRIES = 3`), potentially tripling the verification cost.

**Evidence from terminal output:**
- `Prefill: 100% 2444/2445 [00:05, 453.92tok/s]` — 5.4s for CUA planning
- `Prefill: 100% 3527/3528 [00:07, 450.07tok/s]` — 7.8s for CUA completion
- Dozens of `Sentry SDK not installed` / `OpenTelemetry packages not installed` warnings between each phase (noise, not the cause, but indicating many import cycles)

### Why This Matters

- **Training sessions**: A 5-step login flow takes 2–3 minutes instead of 30 seconds. Human trainers wait idly.
- **Execution cycles**: A 10-step task at 30s/step = 5 minutes. With 50-step budgets, worst case is 25 minutes per task.
- **Iteration speed**: Slow feedback loops discourage playbook refinement and make debugging painful.

---

## Current State

### Location Map

| Component | File | Key Lines |
|-----------|------|-----------|
| Training step execution | `src/soren/services/training_session.py` | L537–641 (`_execute_single_step`) |
| Post-action settle delay | `src/soren/services/training_session.py` | L56 (`_POST_ACTION_SETTLE_DELAY = 1.5`) |
| Screen description (redundant) | `src/soren/services/training_session.py` | L636–640 |
| Verification (Tier 2 VLM) | `src/soren/verification/verifier.py` | L79–105 |
| Task execution CUA loop | `src/soren/services/task_executor.py` | L93–124 |
| CUA SDK agent loop | `agent/agent.py` (vendored) | L825–945 (`run`) |
| MLX VLM adapter | `agent/adapters/mlxvlm_adapter.py` | L222–279 (`_generate`) |
| ComputerAgent screenshot delay | `agent/agent.py` (vendored) | L722–726 |
| LLM service (Ollama) | `src/soren/services/llm_service.py` | L45–69 |
| Settings | `config/settings.yaml` | L25–31 (vlm, verification) |

### Actual Behavior

Each training step runs **`_execute_single_step`** which:
1. Creates a fresh `ComputerAgent` instance per step (model load overhead on first call)
2. Runs the CUA loop (minimum 3 VLM turns per action)
3. Sleeps 1.5s (`_POST_ACTION_SETTLE_DELAY`)
4. Takes a fresh screenshot
5. Calls `llm.generate()` to describe the screen (another VLM call)
6. Returns to `_execute_and_verify` which runs `ActionVerifier.verify()` (yet another VLM call)

### Expected Behavior

Simple, deterministic actions (navigate to URL, click a known button, type text) should complete in 3–5 seconds. Complex, judgment-requiring actions can take longer but should not pay for redundant inference.

### Gap Reason

The pipeline was designed for correctness-first with generous safety margins. Every step gets the same heavyweight treatment regardless of complexity. No fast-path exists for deterministic actions, and several VLM calls are redundant.

---

## Dependencies

- **Hard**: CUA SDK's `ComputerAgent` API — the `run()` generator protocol, `screenshot_delay` parameter, and the 3-turn minimum per action are SDK constraints.
- **Soft**: MLX VLM inference speed — bounded by Apple Silicon Neural Engine throughput (~450 tok/s for 7B-6bit).
- **Hidden**: Ollama model swap latency — switching between UI-TARS (MLX) and Qwen3-VL (Ollama) may trigger a 3–8s model load if `OLLAMA_MAX_LOADED_MODELS=1`. The `keep_alive=0` setting in `LLMService` forces immediate unload, guaranteeing a cold load on every verification call.

## Reuse Plan

- **Available**: `BaselineManager` (Tier 1 OpenCV verification), `StuckDetector`, `MemoryGuard`
- **Will Reuse**: `BaselineManager` — invest in building baselines during training so execution can skip Tier 2 VLM verification for known-good states. This is the single highest-leverage reuse opportunity.
- **Won't Reuse**: The current `_capture_and_describe` helper — it's a convenience for human-facing training output but adds a full VLM call per step. Should be made optional.
- **Consolidation**: The `ComputerAgent` is instantiated fresh in `_execute_single_step` (training), `execute` (task executor), and `execute_with_learning` (task executor). Three near-identical construction sites → candidate for a shared factory.

---

## Options

### Option 1: Conservative — Eliminate Redundant VLM Calls

**What**: Remove the screen-description VLM call during training steps; make it opt-in. Skip Tier 2 verification when Tier 1 baseline matches.

**Changes**:
- `_execute_single_step`: Remove the `llm.generate("Describe what you see...")` call at L636–640. The CUA agent's own completion message already describes what happened.
- `ActionVerifier.verify()`: Already has Tier 1 → Tier 2 escalation. Invest in capturing baselines during training so execution hits Tier 1 (OpenCV, ~10ms) instead of Tier 2 (VLM, ~5–10s).
- Remove `_POST_ACTION_SETTLE_DELAY` for non-navigation actions (clicks, typing). Keep 1.5s only for page-load actions.

**Estimated savings**: 8–18s per step (one fewer VLM call + conditional settle skip).

**Effort**: 1–2 days.

**Pros**: Minimal risk. No architectural changes. Backward compatible.

**Cons**: Doesn't address the fundamental 3-turn CUA overhead (~13s minimum per step).

### Option 2: Modern — Direct-Action Fast Path

**What**: For deterministic, playbook-scripted actions (navigate to URL, type into field, click coordinates), bypass the CUA agent entirely and use the `Computer.interface` directly.

**Changes**:
- Add a `DirectActionExecutor` that interprets structured playbook steps (e.g., `{"action": "navigate", "url": "..."}`, `{"action": "type", "selector": "url_bar", "text": "..."}`) and calls `computer.interface.run_command()` or equivalent.
- Training records structured action metadata alongside natural-language instructions.
- Execution tries the direct path first; falls back to CUA if the action is ambiguous or fails.
- Verification still runs (Tier 1 preferred) to confirm the action succeeded.

**Estimated savings**: 20–28s per deterministic step (eliminates all 3 CUA VLM turns). CUA is reserved for judgment-heavy actions.

**Effort**: 3–5 days.

**Pros**: Massive speedup for common flows (login, navigation). Playbooks become more reliable (no VLM hallucination on simple actions).

**Cons**: Requires structured playbook format alongside natural language. Two execution paths to maintain. Direct actions are brittle if UI changes.

### Option 3: YOLO — Aggressive Model & Pipeline Tuning

**What**: Switch to a 4-bit quantized UI-TARS (saving ~1.5 GB, ~15% faster prefill), reduce `only_n_most_recent_images` from 5 to 2 (shorter prompts), cut `screenshot_delay` to 0.2s, eliminate settle delay entirely, and run verification asynchronously (don't block the next step).

**Estimated savings**: 30–50% reduction in per-step time across the board.

**Effort**: 1–2 days.

**Pros**: Fast. Simple config changes.

**Cons**: Quality degradation risk. Fewer images = less context for complex multi-step actions. Shorter delays = more flaky screenshots. Async verification means errors are detected late.

---

## Choice

**Option 1 (Conservative) immediately, with Option 2 (Direct-Action Fast Path) as a follow-up.**

Option 1 is low-risk, high-impact, and can ship in a day. The screen-description call is pure waste during automated execution — the CUA agent already provides action summaries. Baseline investment pays compound returns across every future execution cycle.

Option 2 is the real win for production but requires playbook format changes and a new executor. It should be designed after Option 1 validates the baseline-first verification approach.

Option 3's quality trade-offs are unacceptable for a system that needs reliable task completion. Individual tuning knobs from Option 3 (like `screenshot_delay` reduction to 0.3s) can be cherry-picked into Option 1.

### Trade-offs Accepted

- **Giving up** the screen description on every step **for** 5–10s savings per step. Human trainers lose the automatic description but can request it via a `/describe` command.
- **Giving up** guaranteed Tier 2 verification on every step **for** Tier 1 baseline speed when baselines exist. New/unknown states still escalate to Tier 2.
- **Giving up** uniform settle delay **for** action-type-aware delays. Risk: some fast actions may capture mid-transition screenshots. Mitigation: verification catches these and triggers retry.

---

## Implementation Plan

### Phase 1: Eliminate Redundant Work (1–2 days)

**Goal**: Remove unnecessary VLM calls without touching the CUA pipeline.

**Changes**:

1. **`training_session.py` — `_execute_single_step`**: Remove the `llm.generate("Describe what you see...")` call. Return the CUA agent's own completion text as the description instead.

2. **`training_session.py` — `_execute_single_step`**: Make `_POST_ACTION_SETTLE_DELAY` configurable via settings. Default to 1.0s (down from 1.5s). Add an `action_type` parameter so navigation actions keep the full delay while click/type actions use 0.5s.

3. **`training_session.py` — `_execute_and_verify`**: When verification passes on attempt 1, skip the remaining retry budget immediately (already does this — confirm no regression).

4. **`verifier.py`**: Add a `skip_tier2` flag that callers can set when baselines are known to exist and Tier 1 passed. This avoids the Ollama round-trip entirely for known-good states.

5. **`training_session.py`**: During training, auto-save the verification screenshot as a baseline via `BaselineManager.save()` when the human confirms a step. This builds the Tier 1 corpus.

**Success Criteria**:
- Per-step latency for "type URL + Enter" drops from ~30s to ~15s.
- No change in verification accuracy (Tier 2 still fires when Tier 1 is inconclusive).

**Rollback Plan**: Revert the 3 files. No data migration needed.

### Phase 2: Configurable Pipeline (1 day)

**Goal**: Expose latency-relevant knobs in `settings.yaml`.

**New settings**:
```yaml
performance:
  screenshot_delay: 0.5          # CUA SDK screenshot delay (seconds)
  settle_delay_navigation: 1.0   # Post-action delay for page loads
  settle_delay_interaction: 0.3  # Post-action delay for clicks/typing
  skip_screen_description: true  # Skip VLM description in automated execution
  verification_tier1_only: false # Skip Tier 2 when Tier 1 baseline passes
  max_recent_images: 5           # CUA only_n_most_recent_images
```

**Success Criteria**: All delays are configurable without code changes. Operators can tune for their hardware.

### Phase 3: Direct-Action Fast Path (3–5 days)

**Goal**: Bypass CUA for deterministic playbook steps.

**Approach**:
1. Extend `PlaybookStep` model with optional structured action metadata (`action_type`, `target`, `value`).
2. Build `DirectActionExecutor` that maps structured actions to `computer.interface` calls.
3. Training UI offers "Record as direct action" for simple steps.
4. Execution tries direct path → verify → fall back to CUA on failure.

**Success Criteria**:
- Deterministic steps (navigate, type, click known element) complete in 2–4s.
- CUA fallback fires <5% of the time for well-trained playbooks.

**Monitoring**: Log `execution_path` (direct vs. CUA) per step in `step_log` for analysis.

---

## Performance Analysis

### Current Baseline

| Metric | Value | Target |
|--------|-------|--------|
| Per-step latency (simple action) | 25–35s | <10s |
| Per-step latency (complex action) | 35–50s | <20s |
| 5-step login flow | 2–3 min | <45s |
| VLM calls per simple step | 4–6 | 1–2 |
| Verification latency (Tier 2) | 5–10s | <0.5s (Tier 1) |

### Bottleneck Analysis

| Bottleneck | % of step time | Addressable? |
|-----------|---------------|-------------|
| CUA VLM inference (3 turns) | ~55% | Partially (Option 2 eliminates for deterministic steps) |
| Screen description VLM call | ~15% | Yes (Option 1 — remove) |
| Verification VLM call | ~20% | Yes (Tier 1 baselines) |
| Settle delay + screenshot delay | ~10% | Yes (tuning) |

### Expected Improvement

| Phase | Savings per step | Cumulative |
|-------|-----------------|-----------|
| Phase 1 (redundant calls) | 8–18s | 8–18s |
| Phase 2 (tuning) | 1–3s | 9–21s |
| Phase 3 (direct actions) | 15–25s (deterministic only) | 2–4s total for simple steps |

---

## Risk Register

### 🔴 High Risk: Tier 1 Baseline Brittleness

- **Probability**: Likely — screenshots vary with window position, time-of-day (dark mode), and Safari rendering differences.
- **Impact**: False Tier 1 passes → undetected failures; false Tier 1 failures → unnecessary Tier 2 escalation (slower, not dangerous).
- **Mitigation**: Use structural similarity (SSIM) with a conservative threshold (0.85 already configured). Capture baselines at multiple resolutions. Always escalate to Tier 2 on Tier 1 failure (current behavior).
- **Detection**: Track Tier 1 hit rate in cycle reports. Alert if <50% of verifications resolve at Tier 1.

### 🟡 Medium Risk: Settle Delay Too Aggressive

- **Probability**: Possible — some pages have 2–3s animations (modals, spinners).
- **Impact**: Flaky verification screenshots → false failures → wasted retries.
- **Mitigation**: Action-type-aware delays (navigation gets full delay, clicks get reduced delay). Verification retry loop already handles transient failures.
- **Detection**: Monitor retry rate per step. If retry rate increases >20% after tuning, revert delay changes.

### 🟡 Medium Risk: Direct-Action Executor Brittleness (Phase 3)

- **Probability**: Possible — UI changes break coordinate-based or selector-based actions.
- **Impact**: Direct action fails, falls back to CUA (slower but correct).
- **Mitigation**: CUA fallback is automatic. Direct actions are best-effort optimization, not correctness-critical.
- **Detection**: Track `execution_path` in step logs. Alert if CUA fallback rate exceeds 20%.

### 🟢 Low Risk: Removing Screen Description Degrades Training UX

- **Probability**: Low — the description is shown in the training UI but rarely read by trainers who can see the VNC window.
- **Impact**: Minor — trainers lose automatic text description of each screen.
- **Mitigation**: Add `/describe` command to training session for on-demand description. Keep description in execution mode where there's no human watching.
- **Detection**: User feedback during training sessions.

---

## Dependency Risk Assessment

| Package | Version | Maintainer | Last Update | Bus Factor | Alternative |
|---------|---------|-----------|-------------|-----------|------------|
| `cua-agent` (CUA SDK) | 0.5.x | trycua | Active (2026) | Medium (small team) | Fork + maintain |
| `mlx-vlm` | latest | MLX community | Active | Medium | Direct MLX calls |
| `ollama` (Python client) | latest | Ollama Inc | Active | Low risk | `httpx` direct API |
| `opencv-python` (baselines) | latest | OpenCV | Active | Low risk | `scikit-image` |

---

## Success Metrics

- **Technical**: Per-step latency for simple actions < 10s (Phase 1+2), < 4s (Phase 3). VLM calls per simple step reduced from 4–6 to 1–2.
- **Delivery**: Phase 1 ships within 2 days. Phase 2 within 3 days. Phase 3 within 2 weeks.
- **Quality**: Verification accuracy unchanged (Tier 2 still available). Task success rate unchanged or improved (fewer timeout failures from slow steps).

---

## Existing Components & Reuse Plan

| Component | Location | Reuse Strategy |
|-----------|----------|---------------|
| `BaselineManager` | `src/soren/verification/baseline.py` | Invest in populating baselines during training (Phase 1) |
| `ActionVerifier` Tier 1 | `src/soren/verification/verifier.py` | Already implemented; needs baseline data to be effective |
| `StuckDetector` | `src/soren/services/stuck_detector.py` | No change needed; benefits from faster steps (more budget headroom) |
| `MemoryGuard` | `src/soren/services/memory_guard.py` | No change needed; fewer model swaps = less memory pressure |
| `ComputerAgent` factory | 3 call sites | Consolidate into shared helper (Rule of Three) |

### Rule-of-Three Consolidation

`ComputerAgent` is constructed identically in three places:
1. `training_session.py:_execute_single_step` (L577–582)
2. `task_executor.py:execute` (L94–100)
3. `task_executor.py:execute_with_learning` (L203–209)

All three use the same pattern: `ComputerAgent(model=vlm_model, tools=[computer], instructions=..., screenshot_delay=0.5, only_n_most_recent_images=5)`. Extract to a shared factory function in a `cua_helpers.py` module.

---

## Appendix A: Bot Detection Considerations (Option 2)

A natural concern with the Direct-Action Fast Path (Option 2) is whether bypassing the VLM makes Soren more detectable as a bot. The short answer: **no — both paths produce identical input events to the browser.**

### Why the CUA Path Is Not "More Human"

The CUA agent (UI-TARS) decides *what* to click and type, but it does not control *how* the input reaches the browser. Regardless of whether the VLM or a direct executor initiates an action, the call chain is:

```
[VLM decision OR direct executor]
  → cuaComputerHandler.type() / .click()
    → computer.interface.type_text() / .left_click()
      → GenericComputerInterface._send_command() over WebSocket
        → Computer API server inside VM
          → macOS CGEvent (OS-level synthetic input)
            → Safari receives keyboard/mouse event
```

The VLM does not introduce mouse jitter, variable typing speed, realistic pauses, or cursor movement paths. It sends the same instantaneous `type_text("https://acmepanel.example")` call that a direct executor would. If anything, VLM-driven actions can be *more* suspicious — the model produces pixel-perfect coordinate clicks with zero preceding mouse movement, which is a known bot fingerprint.

### What Actually Matters for Bot Detection

Browser-side bot detection (Cloudflare Turnstile, reCAPTCHA, DataDome, etc.) looks at:

| Signal | CUA path | Direct path | Difference |
|--------|----------|-------------|-----------|
| Input event source | CGEvent (synthetic) | CGEvent (synthetic) | **None** |
| Mouse movement patterns | None (teleport clicks) | None (teleport clicks) | **None** |
| Typing cadence | Instant (`type_text` bulk) | Instant (`type_text` bulk) | **None** |
| `navigator.webdriver` flag | Not set (real Safari) | Not set (real Safari) | **None** |
| JavaScript execution context | Normal (no Playwright) | Normal (no Playwright) | **None** |
| Browser fingerprint | Real Safari in VM | Real Safari in VM | **None** |

The one path that *would* increase detection risk is `computer.interface.playwright_exec()`, which drives a Playwright browser instance. Playwright sets `navigator.webdriver = true` and has other detectable signatures. **Option 2 must use the same `interface.left_click` / `interface.type_text` / `interface.press_key` primitives as the CUA path, not Playwright.**

### Recommendation

If human-like input patterns become necessary in the future (e.g., a target site deploys behavioral biometrics), the correct place to add them is in the `GenericComputerInterface` layer — adding configurable inter-keystroke delays to `type_text`, mouse movement curves to `left_click`, etc. This would benefit *both* the CUA and direct-action paths equally.

---

## Appendix B: Noise Reduction

The terminal output is dominated by repeated `Sentry SDK not installed` and `OpenTelemetry packages not installed` warnings. These are emitted by the CUA SDK's callback system on every import cycle. While not a performance issue (each warning is <1ms), they obscure meaningful output.

**Quick fix**: Set `PYTHONWARNINGS=ignore::UserWarning` in the environment, or add `sentry-sdk` and `opentelemetry-api` as optional dependencies to suppress the warnings. Alternatively, patch the CUA SDK's callback imports to use `importlib.util.find_spec()` checks instead of try/except with warnings.
