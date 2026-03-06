# Discovery: Model Memory Budget & Hot-Swap Validation

## The One-Liner

Validate whether Soren's three-model architecture actually fits in 32 GB of unified memory, and determine the real cost of hot-swapping models through Ollama on Apple Silicon.

## The Problem

The RFC proposes running three AI models (UI-TARS 5.5 GB, Gemma3:12B 8.1 GB, Qwen3-VL 5 GB) alongside a Lume VM (8 GB) and macOS (~4 GB) on 32 GB Apple Silicon machines. The memory budget math looks plausible on paper — 25.6 GB peak, 6.4 GB headroom — but several assumptions remain unvalidated:

- Does `OLLAMA_MAX_LOADED_MODELS=1` actually free memory when unloading?
- Can `psutil.virtual_memory().percent` see unified memory allocations from MLX and Ollama?
- What's the real wall-clock cost of swapping 5–8 GB models 2–3 times per cycle?
- Is `gemma3:4b` capable enough to serve as a fallback without degrading task quality?

This discovery investigates each question with concrete evidence rather than assumptions.

## The Solution

A validated memory architecture with specific Ollama configuration, accurate monitoring tools, and a calibrated swap strategy that keeps Soren within budget on 32 GB machines while maintaining task quality.

## The Work

- **Effort**: 1–2 days implementation (Ollama configuration + monitoring integration + benchmark script)
- **Timeline**: Complete before Phase 3 (Agent Execution Core) begins
- **Team Needs**: One engineer with access to a 32 GB Apple Silicon machine running Ollama and MLX

## The Risks

| Area | Risk | Rating |
|------|------|--------|
| Memory monitoring accuracy | psutil misreports unified memory | 🟡 Medium |
| Model swap latency | 2–3 swaps add 15–25s per cycle | 🟡 Medium |
| Fallback model quality | gemma3:4b struggles with complex personas | 🟡 Medium |
| Memory budget headroom | 6.4 GB thin with macOS background processes | 🟡 Medium |
| Ollama memory release | mmap behavior may retain pages after unload | 🟢 Low |
| Budget arithmetic | RFC's 25.6 GB estimate is realistic | 🟢 Low |

---

## 1. Ollama Model Management

### How `OLLAMA_MAX_LOADED_MODELS` Works

This environment variable controls the maximum number of models Ollama keeps in memory simultaneously. The defaults and behavior, drawn directly from [Ollama's FAQ](https://docs.ollama.com/faq) and [concurrent request documentation](https://www.glukhov.org/post/2025/05/how-ollama-handles-parallel-requests/):

- **Default value**: 3 × GPU count (or 3 for CPU-only inference)
- **When the limit is reached** and a new model is requested: Ollama queues the incoming request, unloads idle models to free memory, then loads the new model and processes the queued request
- **On Apple Silicon**: all memory is unified — there's no separate VRAM pool. Ollama sees the full system memory as available

Setting `OLLAMA_MAX_LOADED_MODELS=1` for Soren means Ollama will keep at most one model resident. When Soren requests Qwen3-VL after using Gemma3:12B, Ollama will:

1. Queue the Qwen3-VL request
2. Unload Gemma3:12B from memory
3. Load Qwen3-VL
4. Process the request

This is the behavior the RFC assumes, and it is correct.

### Unload Behavior and Memory Release

Models unload through two mechanisms:

**Automatic (idle timeout):** After `keep_alive` duration (default 5 minutes), Ollama unloads the model. The `OLLAMA_KEEP_ALIVE` environment variable controls the global default.

**Explicit unload:** Three methods available:

```bash
# CLI
ollama stop gemma3:12b

# API — POST with keep_alive: 0
curl http://localhost:11434/api/generate -d '{"model": "gemma3:12b", "keep_alive": 0}'
# Response includes: "done_reason": "unload"

# Per-request — set keep_alive: 0 on every API call
# Model unloads immediately after generating the response
```

**Memory release timing**: When Ollama unloads a model, memory is freed to the OS. However, there's a documented caveat: when `use_mmap=true` (the default on macOS), the OS may retain memory-mapped pages in its file cache. These pages are reclaimable under memory pressure, but they inflate reported "used" memory in tools like `vm_stat` and Activity Monitor. This was the subject of [Ollama issue #10539](https://github.com/ollama/ollama/issues/10539) and earlier [PR #2146](https://github.com/ollama/ollama/pull/2146).

**Practical impact for Soren**: The OS will reclaim these cached pages when the next model loads, so mmap caching doesn't prevent loading — it just means memory reporting between loads will look higher than actual committed usage.

### The `/api/ps` Endpoint — Model State Inspection

The `GET /api/ps` endpoint returns currently loaded models with memory details:

```json
{
  "models": [{
    "name": "gemma3:12b",
    "model": "gemma3:12b",
    "size": 8100000000,
    "size_vram": 8100000000,
    "digest": "sha256:...",
    "expires_at": "2026-02-23T20:00:00Z",
    "details": {
      "format": "gguf",
      "family": "gemma3",
      "parameter_size": "12B",
      "quantization_level": "Q4_K_M"
    }
  }]
}
```

Key fields for Soren's admission control:

- `size_vram`: bytes of GPU/unified memory consumed
- `expires_at`: when idle unload will trigger
- Empty `models` array means no model is loaded

**Recommendation**: Before each Ollama call, Soren should `GET /api/ps` to verify the current model state rather than relying solely on psutil. This provides ground truth about Ollama's actual memory commitment.

### `keep_alive` Semantics — The Critical Configuration

For Soren's hot-swap strategy, `keep_alive` is the most important parameter:

| Value | Behavior |
|-------|----------|
| `"5m"` (default) | Model stays loaded 5 minutes after last request |
| `0` | Model unloads **immediately** after generating the response |
| `"-1"` or `-1` | Model stays loaded indefinitely until explicitly unloaded |
| `"24h"` | Model stays loaded for 24 hours |

**Recommended Soren configuration**:

```python
# Set keep_alive: 0 on every Ollama API call
response = ollama.generate(
    model="gemma3:12b",
    prompt=prompt,
    keep_alive=0  # Unload immediately after response
)
```

This ensures the previous model's memory is released before Soren requests the next model, preventing the scenario where both models briefly coexist in memory during the 5-minute default window.

**Global override** (belt-and-suspenders):

```bash
# Set via launchctl on macOS
launchctl setenv OLLAMA_KEEP_ALIVE "0"
launchctl setenv OLLAMA_MAX_LOADED_MODELS "1"
launchctl setenv OLLAMA_FLASH_ATTENTION "1"
```

### Additional Memory Optimizations

Two Ollama server settings can significantly reduce runtime memory:

**Flash Attention** (`OLLAMA_FLASH_ATTENTION=1`): Reduces memory usage as context size grows. Supported by most modern models including Gemma3 and Qwen3-VL. No quality impact — this is a mathematically equivalent computation with better memory access patterns.

**KV Cache Quantization** (`OLLAMA_KV_CACHE_TYPE=q8_0`): Reduces the key-value cache memory by ~50% compared to the default `f16`. The `q8_0` option has negligible quality impact for most tasks. The `q4_0` option saves ~75% but may degrade quality at higher context sizes. For Soren's typical context lengths (under 8K tokens), `q8_0` is the safe choice.

Combined, these two settings can reduce Ollama's runtime memory overhead by 1–2 GB beyond the base model weight, widening the headroom from 6.4 GB to roughly 7.5–8.5 GB.

---

## 2. Hot-Swap Analysis

### Expected Swap Latency

Model swapping on Apple Silicon involves two phases: unloading the current model (fast — memory deallocation) and loading the new model (slower — reading weights from SSD into memory).

**Load time benchmarks from research**:

| Model Size | Framework | Load Time (Apple Silicon) | Source |
|------------|-----------|--------------------------|--------|
| 7B fp16 | MLX | ~4.0s | [Deep AI benchmark](https://deepai.tn/glossary/ollama/mlx-faster-than-ollama/) |
| 7B 4-bit | MLX | ~1.7s | Deep AI benchmark |
| 7B fp16 | llama.cpp | ~2.8s | Deep AI benchmark |
| 7B 4-bit | llama.cpp | ~0.9s | Deep AI benchmark |
| 7B (Ollama) | Ollama | ~3–5s estimated | Ollama wraps llama.cpp + HTTP overhead |
| 12B Q4 (Ollama) | Ollama | ~5–8s estimated | Proportional to model size |

Ollama adds overhead beyond raw llama.cpp because it manages an HTTP server, model registry lookup, and GGUF metadata parsing. Based on available benchmarks, expect Ollama to be 30–50% slower than raw llama.cpp for model loading.

**Estimated swap latency for Soren's models**:

| Swap | Unload Time | Load Time | Total |
|------|-------------|-----------|-------|
| Gemma3:12B → Qwen3-VL 8B | <0.5s | ~4–6s | ~5–7s |
| Qwen3-VL 8B → Gemma3:12B | <0.5s | ~6–8s | ~7–9s |
| Gemma3:12B → gemma3:4b (fallback) | <0.5s | ~2–3s | ~3–4s |

### Per-Cycle Swap Overhead

The RFC describes this swap sequence per cycle:

1. **Reasoning** (Gemma3:12B): Generate content, plan actions
2. **Verification** (Qwen3-VL): Validate screenshot after action
3. **Playbook write-back** (Gemma3:12B): Summarize successful steps

That's 2 swaps minimum per cycle (Gemma3 → Qwen3-VL → Gemma3). In practice, a multi-step task with N verification checks requires N+1 swaps. For a 10-step task with verification every 3 steps:

| Scenario | Swaps | Estimated Overhead |
|----------|-------|--------------------|
| Minimal (1 verification) | 2 | ~12–16s |
| Typical (3 verifications) | 6 | ~36–48s |
| Heavy (5+ verifications) | 10+ | ~60–80s |

For a cycle that takes 2–15 minutes total, 36–48 seconds of swap overhead represents 4–25% of cycle time. That's noticeable but not devastating.

### Strategies to Minimize Swap Overhead

**Strategy 1: Batch reasoning calls before verification** — Instead of interleaving reasoning and verification, batch all reasoning operations (content generation, planning, playbook analysis) into a single Gemma3:12B session, then switch to Qwen3-VL for all verification passes, then switch back for write-back. This reduces a typical cycle from 6 swaps to 2.

**Strategy 2: Preload the next model** — After the last Gemma3:12B call, immediately send an empty preload request to Qwen3-VL:

```python
# After last reasoning call
ollama.generate(model="gemma3:12b", prompt=prompt, keep_alive=0)

# Preload verification model while processing reasoning output
ollama.generate(model="qwen3-vl:8b", prompt="", keep_alive="5m")
```

The preload overlaps model loading with CPU-bound post-processing of the reasoning output.

**Strategy 3: Skip verification on high-confidence actions** — The RFC's Tier 1 verification (OpenCV histogram correlation) costs ~1ms and doesn't need Ollama. Reserve Tier 2 (Qwen3-VL) verification for low-confidence or high-stakes actions. If only 30% of actions need Tier 2, swap count drops significantly.

**Strategy 4: On 64 GB machines, skip swapping entirely** — With `OLLAMA_MAX_LOADED_MODELS=2`, both Qwen3-VL and Gemma3:12B stay resident. Zero swap overhead. The RFC already accounts for this.

**Recommended approach**: Combine strategies 1 and 3. Batch reasoning, use Tier 1 verification for most steps, and only invoke Qwen3-VL for terminal verification (after the task appears complete) or when Tier 1 confidence is below threshold. This brings typical swap count down to 2 per cycle (~12–16s overhead).

### Disk Caching Behavior

Ollama stores model files in `~/.ollama/models/` as GGUF blobs. When a model is loaded, Ollama reads the file into memory. On subsequent loads, macOS's unified buffer cache may retain portions of the file in memory (especially with mmap), making reloads faster than cold loads. On Apple Silicon with fast NVMe storage (3–7 GB/s sequential read), even cold loads of 8 GB model files complete in 1–2 seconds at the I/O layer — the remaining load time is initialization overhead (GGUF parsing, tensor allocation, Metal shader compilation).

After the first load in a session, expect subsequent loads of the same model to be 20–40% faster due to OS-level disk caching.

---

## 3. Memory Monitoring on Apple Silicon

### The psutil Problem

`psutil.virtual_memory()` has documented accuracy issues on Apple Silicon:

**Known issues**:
- **VMS inflation** ([psutil issue #1908](https://github.com/giampaolo/psutil/issues/1908)): On M1 Macs, `Process.memory_info().vms` reports values ~10x higher than Activity Monitor. A process using 47 GB in Activity Monitor reported 389 GB via psutil.
- **Available memory miscalculation** ([psutil issue #1277](https://github.com/giampaolo/psutil/issues/1277)): psutil's available memory formula (`inactive + free`) doesn't properly account for macOS's `speculative_count` pages, which should be counted as available.
- **`percent` directional accuracy**: Despite the above, `virtual_memory().percent` is *directionally correct* — it trends upward as memory fills and downward as memory frees. The absolute value may be off by 3–8 percentage points.

**Does psutil see MLX GPU allocations?** Yes, but indirectly. Apple Silicon uses unified memory — there's one physical RAM pool shared between CPU and GPU. MLX allocates via Metal, which draws from the same pool. psutil's `virtual_memory()` reports total system memory utilization, which includes Metal allocations because they reduce available physical pages. However, psutil doesn't distinguish *what* consumed the memory (CPU malloc vs. Metal allocation vs. mmap).

**Does psutil see Ollama allocations?** Yes. Ollama runs as a regular process. Its memory-mapped model files appear in system memory statistics. However, mmap'd pages that are reclaimable may inflate the "used" count.

### Recommended Monitoring Approach

Don't rely on a single metric. Use a layered strategy:

**Layer 1 — Ollama self-reporting (most reliable for model memory)**:

```python
import httpx

async def get_ollama_memory() -> int:
    """Returns total bytes of models loaded in Ollama."""
    resp = await httpx.AsyncClient().get("http://localhost:11434/api/ps")
    data = resp.json()
    return sum(m.get("size_vram", 0) for m in data.get("models", []))
```

This tells you exactly how much memory Ollama has committed to models. No guessing.

**Layer 2 — macOS `memory_pressure` command (best system-wide metric)**:

```python
import subprocess

def get_memory_pressure() -> str:
    """Returns 'normal', 'warn', or 'critical'."""
    result = subprocess.run(
        ["memory_pressure"], capture_output=True, text=True, timeout=5
    )
    output = result.stdout.lower()
    if "critical" in output:
        return "critical"
    elif "warn" in output:
        return "warn"
    return "normal"
```

This is Apple's own assessment of memory state. It accounts for unified memory, compressed pages, swap pressure, and file cache reclaimability — things psutil cannot reason about.

**Layer 3 — psutil as a fallback (coarse threshold)**:

```python
import psutil

def get_psutil_pressure() -> float:
    """Returns memory usage percentage. Directionally correct, not precise."""
    return psutil.virtual_memory().percent
```

Use psutil's `percent` as a coarse signal. Given its tendency to slightly over-report, using a 90% threshold (as the RFC specifies) provides a conservative trigger — the actual memory pressure may be somewhat lower than reported.

**Layer 4 — `vm_stat` for swap detection (canary metric)**:

```python
import subprocess

def get_swap_activity() -> dict:
    """Returns pageins and pageouts from vm_stat."""
    result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
    stats = {}
    for line in result.stdout.splitlines():
        if "Pageins" in line:
            stats["pageins"] = int(line.split(":")[1].strip().rstrip("."))
        elif "Pageouts" in line:
            stats["pageouts"] = int(line.split(":")[1].strip().rstrip("."))
    return stats
```

Non-zero `pageouts` means the system is writing memory to swap — the strongest signal that physical RAM is exhausted. If Soren sees growing pageouts during a cycle, that's a harder trigger for fallback than psutil's percentage.

### Recommended Admission Control Implementation

Replace the RFC's single `psutil.virtual_memory().percent > 90%` check with a composite signal:

```python
async def should_use_fallback_model() -> bool:
    """Multi-signal memory pressure check."""
    ollama_mem = await get_ollama_memory()
    pressure = get_memory_pressure()
    psutil_pct = get_psutil_pressure()
    swap = get_swap_activity()

    if pressure == "critical":
        return True
    if pressure == "warn" and psutil_pct > 85:
        return True
    if psutil_pct > 90:
        return True
    if swap.get("pageouts", 0) > _last_pageout_count:
        return True
    return False
```

This approach is more resilient than any single metric. The `memory_pressure` command catches situations psutil misses, while pageout detection catches cases where both tools underreport.

---

## 4. Fallback Model Assessment

### gemma3:4b Capabilities

Gemma 3 4B is a strong performer for its size class. Key data points from [Google's Gemma 3 report](https://storage.googleapis.com/deepmind-media/gemma/Gemma3Report.pdf) and [benchmark comparisons](https://markaicode.com/gemma-3-model-size-comparison-guide/):

| Benchmark | Gemma3:4B | Gemma3:12B | Gap |
|-----------|-----------|------------|-----|
| IFEval (instruction following) | ~82% | ~89% | -7pp |
| GSM8k (math reasoning) | ~88% | ~94.4% | -6pp |
| HumanEval (code gen) | ~75% | ~85.4% | -10pp |
| ARC-Challenge | ~78% | ~85% | -7pp |

Notably, Gemma 3 4B is competitive with Gemma 2 27B across multiple benchmarks — a generational leap in efficiency.

### Suitability for Soren's Fallback Tasks

The fallback model handles three task types:

**Survey responses**: Requires persona adherence and coherent multi-paragraph text. gemma3:4b handles this adequately for simple personas ("34-year-old marketing professional who prefers formal language"). Complex, multi-trait personas with nuanced behavioral requirements will see degradation — expect flatter personality expression and occasional instruction drift on longer responses.

**Email replies**: Requires tone matching, context awareness, and appropriate length. gemma3:4b performs well here. Email replies are typically short (1–3 paragraphs) with clear structural expectations. The 128K context window means it can process the full email thread.

**Form content generation**: Requires structured output matching specific field requirements. gemma3:4b handles structured generation well — JSON output, field-specific responses, and constrained generation are among its strengths.

**Verdict**: gemma3:4b is an acceptable fallback for most tasks. Quality degradation will be noticeable on complex persona instructions and long-form content generation, but not enough to cause task failures. The 7-point IFEval gap means roughly 1 in 14 additional instruction-following failures compared to 12B.

### Alternative Fallback Candidates

| Model | Size (GGUF Q4) | IFEval | Multimodal | Notes |
|-------|-----------------|--------|------------|-------|
| gemma3:4b | ~3 GB | ~82% | Yes | Best balance of quality and size |
| phi-3.5-mini | ~2.3 GB | ~77% | No | Smaller but measurably worse (-4.6% avg) |
| qwen2.5:3b | ~2 GB | ~79% | No | Strong instruction following, smallest option |
| gemma3:1b | ~1 GB | ~68% | Yes | Emergency-only; significant quality loss |

**Recommendation**: Stick with gemma3:4b as the primary fallback. Its multimodal capability provides future flexibility (e.g., lightweight screenshot analysis), and it leads the 4B class on instruction following. If memory is truly catastrophic (>95% with gemma3:4b), a secondary fallback to qwen2.5:3b at ~2 GB provides an escape hatch at acceptable quality.

Add to `settings.yaml`:

```yaml
llm:
  primary_model: "gemma3:12b"
  fallback_model: "gemma3:4b"
  emergency_model: "qwen2.5:3b"  # Only if fallback still triggers pressure
```

---

## 5. Budget Validation

### RFC Arithmetic Check

The RFC claims a peak budget of 25.6 GB on 32 GB machines:

| Component | Claimed | Validated | Notes |
|-----------|---------|-----------|-------|
| Lume macOS VM | 8 GB | 8 GB | Configurable via Lume; this is a hard allocation |
| UI-TARS 1.5 7B (6-bit MLX) | 5.5 GB | ~5.5 GB | Confirmed from prior Graphiti memory: "UI-TARS 1.5 7B 6-bit uses about 5.5GB" |
| Gemma3:12B (Q4_K_M via Ollama) | 8.1 GB | ~7.5–8.5 GB | Depends on quantization level and KV cache |
| Host macOS | 4 GB | 3.5–6 GB | Highly variable; 4 GB is optimistic with any background apps |
| **Total** | **25.6 GB** | **24.5–28.5 GB** | Range reflects real-world variability |

### Where the Budget Gets Tight

**macOS host memory is the wildcard**. A clean macOS install with no user apps uses 3–4 GB. But developers rarely run bare systems. Safari with a few tabs adds 500 MB–1 GB. Spotlight indexing, iCloud sync, and system daemons fluctuate. A realistic "developer's Mac" baseline is 4.5–6 GB.

**Ollama runtime overhead beyond model weights**: The GGUF model file size is the base cost, but Ollama also allocates:
- KV cache: scales with context length. At default 4096 tokens with fp16 KV cache, this adds ~500 MB for a 12B model. With `q8_0` KV cache, ~250 MB.
- Metal shader buffers and compute scratch space: ~100–300 MB.

**MLX runtime overhead**: Beyond model weights, MLX allocates compute buffers for inference. Typically ~200–400 MB for a 7B model.

**Realistic peak on 32 GB**:

```
Lume VM:         8.0 GB (hard allocation)
Host macOS:      5.0 GB (realistic, not optimistic)
UI-TARS (MLX):   5.9 GB (5.5 GB weights + 0.4 GB runtime)
Gemma3:12B:      8.6 GB (8.1 GB weights + 0.5 GB KV/runtime)
─────────────────────────
Total:          27.5 GB
Headroom:        4.5 GB
```

That's 86% utilization, leaving 4.5 GB. Workable, but not comfortable. macOS starts compressing memory aggressively around 85–90% utilization and will begin swapping to SSD above ~90–95%. The 90% threshold in the RFC is a reasonable trigger point — it catches the system before swap activity becomes disruptive.

**With fallback (gemma3:4b)**:

```
Lume VM:         8.0 GB
Host macOS:      5.0 GB
UI-TARS (MLX):   5.9 GB
gemma3:4b:       3.3 GB (3 GB weights + 0.3 GB runtime)
─────────────────────────
Total:          22.2 GB
Headroom:        9.8 GB
```

Comfortable 69% utilization. The fallback provides genuine relief.

### Budget Verdict

The RFC's 25.6 GB estimate is **slightly optimistic** — realistic peak is closer to 27–28 GB — but the architecture is sound. The admission control system with fallback to gemma3:4b ensures the system degrades gracefully rather than crashing. The key insight: the 90% threshold should be checked not just before Ollama calls but before any model-loading operation, including the initial UI-TARS load during cycle startup.

---

## 6. Recommendations

### Optimal Ollama Configuration

```bash
# Set via launchctl on macOS (persistent across Ollama restarts)
launchctl setenv OLLAMA_MAX_LOADED_MODELS "1"
launchctl setenv OLLAMA_KEEP_ALIVE "0"
launchctl setenv OLLAMA_FLASH_ATTENTION "1"
launchctl setenv OLLAMA_KV_CACHE_TYPE "q8_0"
launchctl setenv OLLAMA_CONTEXT_LENGTH "4096"
```

| Setting | Value | Rationale |
|---------|-------|-----------|
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Single-model residency; prevents memory overcommit |
| `OLLAMA_KEEP_ALIVE` | `0` | Immediate unload after each request; fastest memory turnover |
| `OLLAMA_FLASH_ATTENTION` | `1` | Reduces KV cache memory; no quality impact |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Halves KV cache memory with negligible quality loss |
| `OLLAMA_CONTEXT_LENGTH` | `4096` | Match RFC's default; prevents accidental large-context OOM |

### Monitoring Implementation

Implement a `MemoryGuard` service that Soren calls before every model-related operation:

```python
class MemoryGuard:
    def __init__(self, settings: Settings):
        self._fallback_triggered = False
        self._primary_model = settings.llm.primary_model
        self._fallback_model = settings.llm.fallback_model
        self._last_pageout_count = 0

    async def get_reasoning_model(self) -> str:
        if self._fallback_triggered:
            return self._fallback_model

        if await self._check_pressure():
            self._fallback_triggered = True
            return self._fallback_model

        return self._primary_model

    async def _check_pressure(self) -> bool:
        pressure = self._get_memory_pressure()
        psutil_pct = psutil.virtual_memory().percent
        swap = self._get_swap_activity()

        if pressure == "critical":
            return True
        if pressure == "warn" and psutil_pct > 85:
            return True
        if psutil_pct > 90:
            return True
        if swap["pageouts"] > self._last_pageout_count:
            self._last_pageout_count = swap["pageouts"]
            return True
        return False

    def reset_cycle(self):
        """Call at start of each cycle to allow re-evaluation."""
        self._fallback_triggered = False
```

### Swap Strategy

**Phase order within each Soren cycle**:

1. **Load UI-TARS** (MLX, always resident — no Ollama involvement)
2. **Batch all reasoning** with Gemma3:12B — content generation, planning, persona shaping
3. **Run CUA agent loop** — UI-TARS drives actions; no Ollama needed during GUI automation
4. **Single verification pass** with Qwen3-VL — after task completion, verify final state
5. **Swap back to Gemma3:12B** — playbook write-back

This reduces swaps from the worst case (6–10 per cycle) to exactly 2 per cycle.

**Pre-call pattern for Ollama**:

```python
async def call_ollama(self, model: str, prompt: str) -> str:
    # Verify current state
    loaded = await self._get_loaded_models()
    if loaded and loaded[0]["name"] != model:
        # Explicitly unload first for deterministic behavior
        await self._unload_model(loaded[0]["name"])

    response = await ollama.generate(
        model=model,
        prompt=prompt,
        keep_alive=0
    )
    return response["response"]

async def _unload_model(self, model: str):
    await httpx.AsyncClient().post(
        "http://localhost:11434/api/generate",
        json={"model": model, "keep_alive": 0}
    )
```

### 64 GB+ Configuration

On machines with 64 GB or more, eliminate swap overhead entirely:

```bash
launchctl setenv OLLAMA_MAX_LOADED_MODELS "2"
launchctl setenv OLLAMA_KEEP_ALIVE "-1"  # Keep loaded indefinitely
```

With both Ollama models resident (~13 GB combined), total memory usage is:

```
Lume VM:         8.0 GB
Host macOS:      5.0 GB
UI-TARS (MLX):   5.9 GB
Gemma3:12B:      8.6 GB (resident)
Qwen3-VL:       5.3 GB (resident)
─────────────────────────
Total:          32.8 GB
Headroom:       31.2 GB
```

51% utilization. No swaps, no pressure, no fallback needed.

---

## 7. Risk Assessment

### 🟢 Low Risk: Budget Arithmetic

**Probability**: Already validated
**Impact**: N/A
**Assessment**: The RFC's 25.6 GB estimate is slightly optimistic but directionally correct. Realistic peak is 27–28 GB. The architecture handles this through admission control, and the fallback path (gemma3:4b) provides genuine relief (drops to ~22 GB).
**Mitigation**: Already designed into the system.

### 🟢 Low Risk: Ollama Memory Release

**Probability**: Low (fixed in PR #2146, mmap pages are reclaimable)
**Impact**: Temporary memory inflation between model swaps
**Assessment**: macOS will reclaim mmap-cached pages when the next model needs memory. The practical risk is that psutil sees inflated numbers between swaps, potentially triggering false-positive fallback. This is handled by the multi-signal monitoring approach.
**Detection**: Monitor `/api/ps` — if it shows no loaded models but psutil reports high usage, the gap is mmap cache.

### 🟡 Medium Risk: Memory Monitoring Accuracy

**Probability**: Medium (psutil known issues are documented)
**Impact**: False-positive fallback triggers (annoyance) or false-negative OOM (crash)
**Assessment**: psutil will likely over-report usage on Apple Silicon, making false positives more likely than false negatives. This is the safer failure mode — unnecessarily using gemma3:4b is better than hitting OOM.
**Mitigation**: Multi-signal monitoring (psutil + `memory_pressure` + `vm_stat` pageouts + Ollama `/api/ps`). The composite check catches both directions.
**Detection**: Log all memory metrics at each checkpoint; compare psutil vs. `memory_pressure` in early cycles to calibrate.

### 🟡 Medium Risk: Model Swap Latency

**Probability**: Medium (2 swaps per cycle is achievable with batching)
**Impact**: 12–16s overhead per cycle (2 swaps), up to 60–80s worst case
**Assessment**: With the recommended batching strategy, swap overhead stays at ~12–16s per cycle — roughly 3–8% of a 2–8 minute cycle. Acceptable for v1. The real risk is that verification needs to happen mid-task (not just at the end), forcing additional swaps.
**Mitigation**: Tier 1 (OpenCV) verification for most steps; Tier 2 (Qwen3-VL) only for terminal state and low-confidence actions. Preload next model during CPU-bound processing.
**Detection**: Track swap count and cumulative swap time in cycle reports.

### 🟡 Medium Risk: Fallback Model Quality

**Probability**: Medium (7-point IFEval gap is material)
**Impact**: Lower task success rate when memory-constrained
**Assessment**: gemma3:4b will handle most tasks adequately, but complex personas and long-form content generation will show quality degradation. Roughly 1 in 14 additional instruction-following failures compared to 12B. Tasks requiring nuanced personality simulation are most at risk.
**Mitigation**: Log which model was used for each task. Track success rate by model. If fallback success rate drops below 60%, consider reducing VM memory allocation or upgrading hardware.
**Detection**: Cycle reports already include model used and task result.

### 🟡 Medium Risk: Budget Headroom on Active Machines

**Probability**: Medium (developers run background apps)
**Impact**: More frequent fallback triggers on busy systems
**Assessment**: The 6.4 GB headroom (RFC) / 4.5 GB (realistic) leaves room for only minimal background processes. Running Cursor, a browser, and Soren simultaneously on 32 GB will reliably trigger fallback. This isn't a bug — it's the admission control working as designed — but users should know that 32 GB is the minimum, not the comfortable target.
**Mitigation**: Documentation should clearly state: "32 GB: functional with memory management. 64 GB: recommended for uncompromised performance." Consider adding a `just check-resources` command that validates available memory before starting a cycle.

---

## 8. Benchmark Script Outline

A validation script to run before Phase 3 begins, confirming the memory budget empirically.

### Script Structure

```python
#!/usr/bin/env python3
"""
Soren Memory Budget Validation Benchmark

Measures actual memory consumption of each component
in the Soren model stack on the host machine.

Usage:
    just validate-memory-budget
    # or
    python scripts/validate_memory_budget.py --vm-memory 8
"""

import argparse
import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil


@dataclass
class MemorySnapshot:
    timestamp: float
    label: str
    psutil_percent: float
    psutil_used_gb: float
    psutil_available_gb: float
    memory_pressure: str
    vm_stat_pageouts: int
    ollama_loaded_models: list[dict] = field(default_factory=list)
    ollama_total_vram_gb: float = 0.0


@dataclass
class BenchmarkResult:
    machine_total_gb: float
    snapshots: list[MemorySnapshot]
    component_costs: dict[str, float]
    swap_timings: list[dict]
    verdict: str


async def take_snapshot(label: str) -> MemorySnapshot:
    """Capture memory state from all monitoring sources."""
    vm = psutil.virtual_memory()
    pressure = _get_memory_pressure()
    pageouts = _get_pageouts()
    ollama = await _get_ollama_state()

    return MemorySnapshot(
        timestamp=time.time(),
        label=label,
        psutil_percent=vm.percent,
        psutil_used_gb=vm.used / (1024**3),
        psutil_available_gb=vm.available / (1024**3),
        memory_pressure=pressure,
        vm_stat_pageouts=pageouts,
        ollama_loaded_models=ollama.get("models", []),
        ollama_total_vram_gb=sum(
            m.get("size_vram", 0) for m in ollama.get("models", [])
        ) / (1024**3),
    )


async def benchmark_component_costs() -> dict[str, float]:
    """Measure memory delta for each component load."""
    costs = {}

    # Baseline (nothing loaded)
    baseline = await take_snapshot("baseline")

    # Load Gemma3:12B
    t0 = time.time()
    await _ollama_generate("gemma3:12b", "Hello", keep_alive=-1)
    gemma_load_time = time.time() - t0
    snap = await take_snapshot("gemma3:12b loaded")
    costs["gemma3:12b"] = snap.psutil_used_gb - baseline.psutil_used_gb
    costs["gemma3:12b_load_seconds"] = gemma_load_time

    # Unload Gemma3:12B
    await _ollama_unload("gemma3:12b")
    await asyncio.sleep(2)

    # Load Qwen3-VL
    t0 = time.time()
    await _ollama_generate("qwen3-vl:8b", "Hello", keep_alive=-1)
    qwen_load_time = time.time() - t0
    snap = await take_snapshot("qwen3-vl loaded")
    unloaded = await take_snapshot("after gemma unload")
    costs["qwen3-vl"] = snap.psutil_used_gb - unloaded.psutil_used_gb
    costs["qwen3-vl_load_seconds"] = qwen_load_time

    # Unload Qwen3-VL
    await _ollama_unload("qwen3-vl:8b")
    await asyncio.sleep(2)

    # Load gemma3:4b (fallback)
    t0 = time.time()
    await _ollama_generate("gemma3:4b", "Hello", keep_alive=-1)
    fallback_load_time = time.time() - t0
    snap = await take_snapshot("gemma3:4b loaded")
    costs["gemma3:4b_load_seconds"] = fallback_load_time

    await _ollama_unload("gemma3:4b")

    return costs


async def benchmark_swap_latency() -> list[dict]:
    """Measure wall-clock time for model swaps."""
    swaps = []

    for from_model, to_model in [
        ("gemma3:12b", "qwen3-vl:8b"),
        ("qwen3-vl:8b", "gemma3:12b"),
        ("gemma3:12b", "gemma3:4b"),
    ]:
        # Load from_model
        await _ollama_generate(from_model, "Hello", keep_alive=-1)
        await asyncio.sleep(1)

        # Time the swap
        t0 = time.time()
        await _ollama_unload(from_model)
        await _ollama_generate(to_model, "Hello", keep_alive=-1)
        swap_time = time.time() - t0

        swaps.append({
            "from": from_model,
            "to": to_model,
            "seconds": round(swap_time, 2),
        })

        await _ollama_unload(to_model)
        await asyncio.sleep(1)

    return swaps


async def run_full_benchmark(vm_memory_gb: int = 8) -> BenchmarkResult:
    """Run complete memory budget validation."""
    total_gb = psutil.virtual_memory().total / (1024**3)
    snapshots = []

    # Phase 1: Baseline
    snapshots.append(await take_snapshot("01_baseline"))

    # Phase 2: Simulate VM allocation impact
    # (VM is managed by Lume; we estimate its cost)
    snapshots.append(await take_snapshot("02_vm_estimated"))

    # Phase 3: Component costs
    costs = await benchmark_component_costs()
    snapshots.append(await take_snapshot("03_post_component_tests"))

    # Phase 4: Swap latency
    swap_timings = await benchmark_swap_latency()
    snapshots.append(await take_snapshot("04_post_swap_tests"))

    # Phase 5: Peak simulation (load largest model)
    await _ollama_generate("gemma3:12b", "Hello", keep_alive=-1)
    snapshots.append(await take_snapshot("05_peak_gemma12b"))
    await _ollama_unload("gemma3:12b")

    # Calculate verdict
    peak_used = max(s.psutil_used_gb for s in snapshots)
    estimated_peak_with_vm = peak_used + vm_memory_gb
    headroom = total_gb - estimated_peak_with_vm
    verdict = (
        "PASS" if headroom > 3.0
        else "WARN" if headroom > 1.0
        else "FAIL"
    )

    return BenchmarkResult(
        machine_total_gb=round(total_gb, 1),
        snapshots=snapshots,
        component_costs=costs,
        swap_timings=swap_timings,
        verdict=f"{verdict}: {round(estimated_peak_with_vm, 1)} GB peak "
                f"/ {round(total_gb, 1)} GB total "
                f"({round(headroom, 1)} GB headroom)",
    )


# --- Helper functions (implementation sketched) ---

def _get_memory_pressure() -> str:
    result = subprocess.run(
        ["memory_pressure"], capture_output=True, text=True, timeout=5
    )
    output = result.stdout.lower()
    if "critical" in output:
        return "critical"
    if "warn" in output:
        return "warn"
    return "normal"


def _get_pageouts() -> int:
    result = subprocess.run(
        ["vm_stat"], capture_output=True, text=True, timeout=5
    )
    for line in result.stdout.splitlines():
        if "Pageouts" in line:
            return int(line.split(":")[1].strip().rstrip("."))
    return 0


async def _get_ollama_state() -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://localhost:11434/api/ps")
        return resp.json()


async def _ollama_generate(model: str, prompt: str, keep_alive: int = 0):
    async with httpx.AsyncClient(timeout=120) as client:
        await client.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "keep_alive": keep_alive},
        )


async def _ollama_unload(model: str):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "keep_alive": 0},
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Soren Memory Budget Validator")
    parser.add_argument("--vm-memory", type=int, default=8, help="VM memory in GB")
    parser.add_argument("--output", type=str, default="memory-benchmark.json")
    args = parser.parse_args()

    result = asyncio.run(run_full_benchmark(args.vm_memory))

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result.__dict__, indent=2, default=str))

    print(f"\n{'='*60}")
    print(f"VERDICT: {result.verdict}")
    print(f"{'='*60}")
    print(f"\nSwap timings:")
    for swap in result.swap_timings:
        print(f"  {swap['from']} → {swap['to']}: {swap['seconds']}s")
    print(f"\nFull results written to {output_path}")
```

### What the Benchmark Validates

| Check | Pass Criteria |
|-------|---------------|
| Peak memory with Gemma3:12B | < 90% of total RAM (accounting for VM) |
| Peak memory with gemma3:4b fallback | < 75% of total RAM (accounting for VM) |
| Gemma3:12B load time | < 10 seconds |
| Qwen3-VL load time | < 8 seconds |
| Full swap (unload + load) | < 12 seconds |
| Pageout activity during peak | Zero pageouts |
| `memory_pressure` during peak | "normal" or "warn" (not "critical") |

### Running the Benchmark

```bash
# Add to justfile
validate-memory-budget:
    uv run python scripts/validate_memory_budget.py --vm-memory 8

# Output: memory-benchmark.json with full results
# Console: PASS/WARN/FAIL verdict with swap timings
```

This should run during Phase 2 or early Phase 3, before the full execution pipeline depends on the memory architecture.

---

## Existing Components & Reuse Plan

| Component | Source | Reuse? | Rationale |
|-----------|--------|--------|-----------|
| Ollama client wrapper | `ollama` Python package | Yes | Standard API client; no custom wrapper needed |
| psutil | `psutil` package (already in RFC deps) | Yes, with caveats | Use as one signal among many; don't rely on it exclusively |
| `memory_pressure` command | macOS built-in | Yes (new integration) | Shell out to native command for best memory pressure signal |
| `vm_stat` command | macOS built-in | Yes (new integration) | Pageout detection as OOM canary |
| httpx | `httpx` package | Yes (new dependency) | Async HTTP client for Ollama `/api/ps` endpoint |
| MLX memory monitoring | MLX framework | Investigate | Check if `mlx.core` exposes memory usage stats |
| Desktop-agent action verifier | `tests/uat/desktop-agent/` | Yes | Tier 1/Tier 2 verification pattern directly reusable |

**Not reused**: No existing memory monitoring or model management code exists in the Soren codebase (greenfield). The `MemoryGuard` service is new. The benchmark script is new.

**Consolidation opportunity**: The `LLMService` wrapper (Phase 3 deliverable) should encapsulate both Ollama communication and model lifecycle management (preload, unload, swap). Don't split model management into a separate service — keep it coupled with the inference client, since every inference call needs to be aware of the current model state.
