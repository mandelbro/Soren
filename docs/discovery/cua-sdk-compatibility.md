# Discovery: CUA SDK Compatibility & Patch Audit

## The One-Liner

The CUA SDK has evolved substantially since the desktop-agent was written — the version pin in the RFC is wrong for `cua-computer`, all four patches are still needed, and the `ComputerAgent` API has changed enough to require rework in Phase 3.

## Date

February 23, 2026

## Status

Complete — ready for Phase 3 implementation

---

## 1. Current State: SDK Versions

| Package | RFC Pin | Actual Latest | Status |
|---------|---------|---------------|--------|
| `cua-agent` | `>=0.7` | **0.7.26** (Feb 17, 2026) | Pin correct |
| `cua-computer` | `>=0.7` | **0.5.14** (Feb 11, 2026) | **Pin WRONG** — must be `>=0.5` |
| `cua-core` | *(not listed)* | **0.1.x** | **Missing** — new required dependency |
| `mlx-vlm` | `>=0.3` | **0.4.x+** | Pin correct but see notes |
| Lume | v0.2.80 | **0.2.80** (Feb 11, 2026) | Matches RFC assumption |

### Version Divergence (Critical Finding)

`cua-computer` and `cua-agent` are on completely different version tracks. The RFC assumed they'd share version numbers — they don't. `cua-agent` v0.7.26 depends on `cua-computer>=0.5.0,<0.6.0`. The `cua-computer>=0.7` pin in the RFC's dependency manifest would fail to install because that version does not exist.

### New Dependency: `cua-core`

Both `cua-computer` and `cua-agent` now depend on `cua-core` (`>=0.1.0,<0.2.0`), a shared package that provides telemetry, common types, and base interfaces. This did not exist when the desktop-agent framework was written. It installs automatically as a transitive dependency but should be declared in the RFC's manifest for clarity.

### Corrected Dependency Manifest

```toml
dependencies = [
    "cua-computer>=0.5,<0.6",
    "cua-agent[uitars-mlx]>=0.7,<0.8",
    "mlx-vlm>=0.3",
    # cua-core installs transitively — no explicit pin needed
]
```

The `cua-agent[uitars-mlx]` extra is the correct installation target for Soren's use case. It pulls in `mlx-vlm>=0.1.27` without installing PyTorch, keeping the dependency footprint minimal. We still pin `mlx-vlm>=0.3` explicitly because the RFC's model loading patterns depend on APIs introduced in 0.3.

### Changelogs

Recent releases are maintenance-only (dependency bumps). No substantive feature additions or breaking API changes between 0.7.20 and 0.7.26 for `cua-agent`, or between 0.5.10 and 0.5.14 for `cua-computer`.

- [cua-agent releases](https://github.com/trycua/cua/releases?q=agent)
- [cua-computer releases](https://github.com/trycua/cua/releases?q=computer)
- [Source: libs/python/](https://github.com/trycua/cua/tree/main/libs/python)

---

## 2. Patch Status

All four patches from the desktop-agent `conftest.py` target underlying library interoperability gaps, not the CUA SDK itself. The CUA SDK's adapter code assumes these gaps don't exist. They do.

### Patch 1: Transformers Video Processor Stub

| Attribute | Value |
|-----------|-------|
| **Target** | `sys.modules` pre-registration for video processor |
| **Still needed?** | **Yes** |
| **Risk** | 🟡 Medium |

**Why it's still needed:** The `MLXVLMAdapter` (in `cua_agent/adapters/mlxvlm_adapter.py`) calls `mlx_vlm.load()`, which internally invokes `transformers.AutoProcessor`. When PyTorch is not installed (which is our case — we use `cua-agent[uitars-mlx]`, not `[uitars-hf]`), the `video_processor_class_from_name` function in transformers iterates mapping values that are `None`, causing a crash. UI-TARS only processes still images, so video processing is unused.

**What changed:** Nothing relevant. The transformers version constraint moved from unspecified to `>=4.54.0` for HF extras, but `uitars-mlx` doesn't require `transformers` as a direct dependency. The crash happens when `mlx_vlm` imports transformers' processor utilities.

**Patch target location:** Unchanged — `sys.modules` stub registration before any CUA SDK import.

### Patch 2: ProcessorMixin Lenient Type Check

| Attribute | Value |
|-----------|-------|
| **Target** | `transformers.processing_utils.ProcessorMixin.__init__` |
| **Still needed?** | **Yes** (coupled to Patch 1) |
| **Risk** | 🟡 Medium |

**Why it's still needed:** When Patch 1 stubs the video processor module, `Qwen2VLProcessor.__init__` receives `None` for the `video_processor` parameter. The type check in `ProcessorMixin` rejects `None`. Since video processing is unused, allowing `None` to pass through is safe.

**What changed:** The class path may have shifted with newer transformers versions. The patch target needs verification against the installed `transformers` version at implementation time.

**Action:** Keep the patch. Verify the exact class path (`transformers.processing_utils.ProcessorMixin`) against `transformers>=4.54.0` during Phase 3 implementation.

### Patch 3: mlx-vlm `generate()` Compatibility

| Attribute | Value |
|-----------|-------|
| **Target** | `mlx_vlm.generate` function |
| **Still needed?** | **Yes** |
| **Risk** | 🔴 High |

**Why it's still needed:** This is confirmed from source code analysis. The current `mlx_vlm.generate()` function returns a `GenerationResult` dataclass:

```python
@dataclass
class GenerationResult:
    text: str = ""
    token: Optional[int] = None
    logprobs: Optional[List[float]] = None
    prompt_tokens: int = 0
    generation_tokens: int = 0
    prompt_tps: float = 0.0
    generation_tps: float = 0.0
    peak_memory: float = 0.0
```

The CUA SDK's `MLXVLMAdapter._generate()` method (in `cua_agent/adapters/mlxvlm_adapter.py`, line ~227) unpacks the return value as a two-element tuple:

```python
text_content, usage = generate(model, tokenizer, str(prompt), images, ...)
```

`GenerationResult` is a plain `@dataclass` with no `__iter__` method, so this unpacking raises `TypeError: cannot unpack non-sequence GenerationResult`. Without the patch, MLX-based UI-TARS inference crashes immediately.

**What changed:** The `GenerationResult` dataclass gained additional fields (`logprobs`, `peak_memory`) since the desktop-agent was written, but the fundamental issue — not being tuple-unpackable — is unchanged.

**Patch implementation:** Wrap `mlx_vlm.generate` to intercept the `GenerationResult` and return `(result.text, {"prompt_tokens": result.prompt_tokens, "generation_tokens": result.generation_tokens})`.

### Patch 4: Enter Key Fix

| Attribute | Value |
|-----------|-------|
| **Target** | `convert_to_computer_actions()` output in `cua_agent/loops/uitars.py` |
| **Still needed?** | **Yes** |
| **Risk** | 🔴 High |

**Why it's still needed:** Confirmed from source. The `convert_to_computer_actions()` function in `uitars.py` (lines 242-244) handles `type` actions by passing content directly to `make_type_item()`:

```python
elif action_type == "type":
    content = action_inputs.get("content", "")
    computer_actions.append(make_type_item(content))
```

`make_type_item(text)` creates a `ResponseComputerToolCallParam` with `ActionTypeAction(text=text, type="type")`. No special handling for `\n` occurs anywhere in this path.

Meanwhile, UI-TARS' action space prompt explicitly instructs the model to use `\n` for submission:

```
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
```

When UI-TARS outputs `type(content='hello world\n')`, the `\n` is passed as literal text to the VM's `type_text()` API, which silently drops it. The user's input appears in the field but is never submitted.

**What changed:** Nothing. The `convert_to_computer_actions()` function structure is identical to the version the desktop-agent patches against.

**Patch implementation:** Intercept the action list after `convert_to_computer_actions()` returns. For any `type` action where the text ends with `\n`, strip the trailing `\n` and append an explicit `keypress(["enter"])` action. For Soren specifically, the Escape keypress before Enter (from the desktop-agent's Slack-specific patch) should be made configurable — not all apps have autocomplete dropdowns.

### Patch Summary

| Patch | Desktop-Agent Target | Current SDK Target | Still Needed | Change Required |
|-------|---------------------|--------------------|--------------|-----------------|
| 1. Video processor stub | `sys.modules` | Same | Yes | None |
| 2. ProcessorMixin | `transformers.processing_utils` | Same (verify path) | Yes | Verify class path against transformers >=4.54.0 |
| 3. mlx-vlm generate | `mlx_vlm.generate` | Same | Yes | Update `GenerationResult` field names |
| 4. Enter key fix | Action list post-processing | Same function, same location | Yes | Make Escape-before-Enter configurable |

---

## 3. API Changes

### ComputerAgent Constructor (Breaking Change)

The `ComputerAgent` constructor has changed significantly from the desktop-agent era.

**Desktop-agent pattern:**
```python
agent = ComputerAgent(computer=computer, model="mlx/mlx-community/UI-TARS-1.5-7B-6bit")
result = await agent.run(task_string)
```

**Current pattern (v0.7.26):**
```python
agent = ComputerAgent(
    model="mlx/mlx-community/UI-TARS-1.5-7B-6bit",
    tools=[computer],
    instructions="You are completing a survey...",
    screenshot_delay=0.5,
    only_n_most_recent_images=10,
    max_retries=3,
)
async for result in agent.run([{"role": "user", "content": task_description}]):
    # result is {"output": [...], "usage": {...}}
    pass
```

Key differences:

| Aspect | Desktop-Agent | Current v0.7.26 |
|--------|--------------|-----------------|
| Computer param | `computer=` kwarg | `tools=[computer]` list |
| Model string | Direct model path | liteLLM provider prefix (`mlx/`, `anthropic/`, etc.) |
| `run()` input | Plain string | Message list (OpenAI format) |
| `run()` output | `AsyncGenerator[dict, None]` | Same, but structured differently |
| Instructions | Injected into prompt manually | `instructions=` constructor param |
| Image retention | Manual | `only_n_most_recent_images=` |

**Impact on Soren:** The `TaskExecutor` wrapper needs to construct `ComputerAgent` with the new API. Persona context can use the `instructions` parameter instead of manual prompt injection. The `run()` message format change requires building message lists, not plain strings.

### ComputerAgent.run() Response Format

Each yielded result from `run()` follows this structure:

```python
{
    "output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "..."}]},
        {"type": "computer_call", "call_id": "...", "action": {"type": "click", "x": 100, "y": 200}},
        {"type": "computer_call_output", "call_id": "...", "output": {"type": "input_image", "image_url": "data:image/png;base64,..."}},
        {"type": "message", "content": [{"type": "output_text", "text": "Task completed."}]},
    ],
    "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
}
```

The `desktop-agent`'s `run_agent()` helper that consumed the generator and extracted text output needs updating to handle this structure.

### Computer Constructor (Breaking Change)

**Desktop-agent pattern:**
```python
from computer import Computer
computer = Computer()
await computer.run()
screenshot = await computer.interface.screenshot()
```

**Current pattern (v0.5.14):**
```python
from computer import Computer

computer = Computer(
    os_type="macos",
    provider_type="lume",
    name="soren-vm",
    image="macos-sequoia-cua:latest",
    display="1024x768",
    memory="8GB",
)
async with computer:
    screenshot = await computer.interface.screenshot()
```

Key differences:

| Aspect | Desktop-Agent | Current v0.5.14 |
|--------|--------------|-----------------|
| Constructor | Minimal args | Explicit `os_type`, `provider_type`, `image` |
| Default image | Unspecified | `macos-sequoia-cua:latest` |
| Lifecycle | `await computer.run()` / `await computer.stop()` | Async context manager OR manual run/stop |
| Screenshot | `interface.screenshot()` → bytes | Same (confirmed) |
| New features | — | OTEL tracing, telemetry, `run_opts`, `ephemeral` |

**`interface.screenshot()` still returns PNG bytes.** The VNC screenshot patch approach remains valid — monkey-patching `interface.screenshot()` to use vncdotool continues to work.

### Agent Loop Auto-Selection

The CUA SDK now uses a decorator-based registry to match models to agent loops:

```python
@register_agent(models=r"(?i).*ui-?tars.*", priority=-1)
class UITARSConfig:
    async def predict_step(self, messages, model, tools, ...): ...
```

For `model="mlx/mlx-community/UI-TARS-1.5-7B-6bit"`, the SDK automatically selects `UITARSConfig` because the model name matches `.*ui-?tars.*`. No manual loop selection is needed.

---

## 4. MLX Integration Status

### Model Path Format

The current SDK uses liteLLM's custom provider routing. For MLX models, the format is:

```
mlx/<huggingface-model-path>
```

Confirmed working paths:
- `mlx/mlx-community/UI-TARS-1.5-7B-6bit`
- `mlx/mlx-community/UI-TARS-1.5-7B-4bit`

The `MLXVLMAdapter` (registered as the `mlx` provider in liteLLM) handles model loading, image preprocessing (via `smart_resize`), coordinate mapping, and generation.

### MLX VLM Adapter Architecture

The adapter inherits from `litellm.llms.custom_llm.CustomLLM` and implements:
- `completion()` / `acompletion()` — synchronous and async model calls
- `streaming()` / `astreaming()` — streaming variants
- `_generate()` — core generation logic using `mlx_vlm.generate()`
- `_convert_messages()` — converts OpenAI-format messages to MLX VLM format
- `_process_coordinates()` — maps coordinates between model space and screen space

The adapter caches loaded models (`self.models`), processors (`self.processors`), and configs (`self.configs`) for reuse across calls — no need for Soren to manage model lifecycle separately.

### `uitars-mlx` Extra

Installing `cua-agent[uitars-mlx]` pulls in `mlx-vlm>=0.1.27` without PyTorch. This is the lightest-weight option for Apple Silicon. The optional dependency list:

```
mlx-vlm>=0.1.27
```

Note: `cua-agent[uitars-hf]` includes `torch`, `transformers>=4.54.0`, and `accelerate` — much heavier and unnecessary for our use case.

---

## 5. New Features to Leverage

### Built-in Callback System

The `ComputerAgent` now supports a rich callback system. Relevant built-in callbacks for Soren:

| Callback | Purpose | Soren Use Case |
|----------|---------|----------------|
| `PromptInstructionsCallback` | Injects system instructions | Persona context injection |
| `LoggingCallback` | Structured step logging | Replace manual logging in TaskExecutor |
| `TrajectorySaverCallback` | Saves screenshots + responses to disk | Playbook generation input |
| `BudgetManagerCallback` | Stops execution when cost/step budget exceeded | Step budget enforcement |
| `ImageRetentionCallback` | Keeps only N most recent images | Memory management for long sessions |

**Recommendation:** Use `instructions=` for persona context instead of manual prompt construction. Use `TrajectorySaverCallback` to capture step data for playbook write-back. Use `BudgetManagerCallback` as the step budget enforcer instead of a custom counter.

### `predict_click` Capability

`ComputerAgent` now supports `predict_click(instruction, image_b64)` which returns `(x, y)` coordinates for a click target. This could be useful for Soren's verification system — asking "where is the submit button?" to verify task completion.

### liteLLM Routing

All model calls go through liteLLM, which means:
- Model switching is a string change (`"mlx/..."` → `"anthropic/..."`)
- Cost tracking is built-in
- Retry logic with exponential backoff is built-in
- Multiple provider support without code changes

For Soren's future cloud LLM fallback (post-v1), this is free infrastructure.

### Multiple Agent Loops

The SDK now ships with loops for: UI-TARS, Anthropic Claude, OpenAI, Gemini, GLM-4.5V, InternVL, Moondream3, and more. While Soren v1 uses UI-TARS exclusively, the loop registry means switching to a different VLM requires only changing the model string.

### `screenshot_delay` Parameter

Built-in delay before screenshots (default 0.5s). The desktop-agent handled this with manual `asyncio.sleep()` calls. Using the built-in parameter is cleaner.

---

## 6. Recommendations

### Dependency Manifest Update

Replace the RFC's dependency section:

```toml
dependencies = [
    "cua-computer>=0.5,<0.6",       # was >=0.7 (wrong)
    "cua-agent[uitars-mlx]>=0.7",    # add uitars-mlx extra
    "mlx-vlm>=0.3",                  # unchanged
    "ollama>=0.4",
    "pydantic>=2.0",
    "cryptography>=42.0",
    "pyyaml>=6.0",
    "click>=8.1",
    "aiosqlite>=0.20",
    "opencv-python>=4.9",
    "rich>=13.0",
    "structlog>=24.0",
    "vncdotool>=1.2",
    "psutil>=5.9",
]
```

### Patch Strategy

Extract all four patches into `src/soren/patches/`:

| File | Patch | Notes |
|------|-------|-------|
| `src/soren/patches/video_processor.py` | Patches 1 & 2 | Stub video processor + relax ProcessorMixin type check |
| `src/soren/patches/mlx_generate.py` | Patch 3 | Wrap `mlx_vlm.generate()` to return tuple |
| `src/soren/patches/enter_key.py` | Patch 4 | Post-process action list, configurable Escape behavior |
| `src/soren/patches/__init__.py` | — | `apply_all()` function called before any CUA SDK import |

**Patch application order matters.** Patches 1-2 must be applied before any `transformers` import. Patch 3 must be applied before any `mlx_vlm` import. Patch 4 operates at runtime on action lists, so it can be applied later.

**Escape-before-Enter configurability:** The desktop-agent's Patch 4 always sends Escape before Enter (Slack autocomplete workaround). Soren should make this configurable per-service in `settings.yaml`:

```yaml
services:
  betapanel:
    enter_key_behavior: "enter_only"   # no Escape prefix
  slack:
    enter_key_behavior: "escape_enter"  # Escape then Enter
```

### API Migration Plan

1. **Computer creation:** Update `VMManager.connect_computer()` to use the new `Computer(os_type="macos", provider_type="lume", ...)` constructor with explicit parameters.

2. **Agent creation:** Update `TaskExecutor.execute()` to construct `ComputerAgent` with:
   ```python
   agent = ComputerAgent(
       model="mlx/mlx-community/UI-TARS-1.5-7B-6bit",
       tools=[computer],
       instructions=persona_context + playbook_instructions,
       screenshot_delay=0.5,
       only_n_most_recent_images=10,
       callbacks=[TrajectorySaverCallback(str(report_dir))],
   )
   ```

3. **Agent loop:** Replace the desktop-agent's `run_agent()` helper with direct `async for` consumption of `agent.run()`, extracting reasoning text and actions from the structured output.

4. **Step budget:** Use `BudgetManagerCallback` or implement a custom callback that counts steps and stops execution at 50.

5. **VNC screenshot patch:** Continue monkey-patching `computer.interface.screenshot()` after Computer initialization, same approach as desktop-agent.

### Existing Components & Reuse Plan

| Component | Source | Reuse? | Notes |
|-----------|--------|--------|-------|
| VNC screenshot patch | `desktop-agent/helpers/vnc_screenshot.py` | **Yes** | Core approach unchanged; extract to `src/soren/patches/` |
| VM Manager | `desktop-agent/helpers/vm_manager.py` | **Partial** | Constructor API changed; lifecycle management pattern reusable |
| Agent runner | `desktop-agent/helpers/agent_runner.py` | **Rewrite** | Response format changed; needs new parser |
| Action verifier | `desktop-agent/helpers/action_verifier.py` | **Yes** | Independent of SDK changes |
| Obstacle handler | `desktop-agent/helpers/obstacle_handler.py` | **Yes** | Independent of SDK changes |
| Baseline comparison | `desktop-agent/helpers/baseline_manager.py` | **Yes** | Independent of SDK changes |
| CUA SDK patches (4) | `desktop-agent/conftest.py` | **Yes, all 4** | Extract to `src/soren/patches/`, update Patch 4 for configurability |
| Report writer | `desktop-agent/helpers/report_writer.py` | **Extend** | Good base; add backlog snapshot, playbook changes |

---

## 7. Risk Assessment

| Area | Risk | Rationale |
|------|------|-----------|
| **Version pin correction** | 🟢 Low | Simple fix — change `cua-computer>=0.7` to `>=0.5,<0.6` |
| **Patches 1-2 (video processor)** | 🟡 Medium | Patches are straightforward but tied to transformers internals that could change in future versions. Pin `transformers` version in dev to reduce surprise. |
| **Patch 3 (mlx-vlm generate)** | 🔴 High | The CUA SDK adapter and mlx-vlm are out of sync. This is a known interop gap that the CUA SDK team hasn't fixed. If mlx-vlm changes `GenerationResult` fields again, the patch breaks. Monitor mlx-vlm releases. |
| **Patch 4 (Enter key)** | 🔴 High | This is a fundamental design gap — UI-TARS uses `\n` for Enter, the SDK doesn't translate it. Until this is fixed upstream, every UI-TARS integration needs this patch. High impact because Enter not working means forms never submit. |
| **ComputerAgent API** | 🟡 Medium | The API change is well-documented and the new API is cleaner. Migration is mechanical but touches core TaskExecutor code. |
| **Computer constructor** | 🟢 Low | Straightforward parameter changes. New defaults are sensible. |
| **MLX integration** | 🟢 Low | The `mlx/` model prefix routing works. The adapter handles model caching internally. |
| **Upstream fix timeline** | 🟡 Medium | The CUA SDK is actively maintained (12.7k stars, weekly releases) but patch-worthy issues remain unfixed across multiple versions. Don't count on upstream fixes arriving before Phase 3. |

### Overall Assessment

**Phase 3 can proceed.** All four patches are still needed but well-understood. The API changes are breaking but mechanical to migrate. The biggest risk is Patch 3 (mlx-vlm generate compatibility) because it bridges two actively-evolving packages that aren't testing their integration edge case. Pinning exact versions in `uv.lock` during Phase 3 is strongly recommended to avoid surprise breakage from a mid-sprint mlx-vlm release.

### Recommended Version Pins for Phase 3

```toml
# Pin these in pyproject.toml for stability during implementation
"cua-agent[uitars-mlx]==0.7.26",
"cua-computer==0.5.14",
"mlx-vlm>=0.3,<0.5",
```

Relax to range pins (`>=`) after Phase 3 is stable and integration tests pass.
