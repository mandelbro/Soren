# Memory Audit: Staying Under 32 GB

**Context**: A full Soren cycle caused an out-of-memory crash. This audit identifies memory hotspots and enforces safeguards for 32 GB Apple Silicon machines.

## Budget Summary (32 GB Target)

| Component | Size | Notes |
|-----------|------|-------|
| Lume VM | 8 GB | `vm.memory_gb` in settings |
| UI-TARS (MLX) | ~5.9 GB | 5.5 GB weights + 0.4 GB runtime, always resident |
| Ollama model (one at a time) | 5–8.6 GB | Qwen3-VL ~5 GB, Gemma3:12B ~8.6 GB with KV cache |
| Host macOS | 4–6 GB | Variable; 5 GB realistic |
| **Peak total** | **~27–28 GB** | **Headroom: ~4–5 GB** |

**Critical**: With `OLLAMA_MAX_LOADED_MODELS=1`, only one Ollama model loads at a time. Without it (default 3), Ollama can hold Gemma3:12B + Qwen3-VL = ~13 GB, pushing peak to ~31 GB and risking OOM.

---

## Identified Hotspots

### 1. Ollama Environment (HIGH RISK)

**Issue**: `OLLAMA_MAX_LOADED_MODELS` defaults to 3. If not set to 1, multiple models can be resident.

**Mitigation**:
- Document required env vars in README and this audit
- Add `just check-resources` to validate before cycle
- Warn at cycle start if env not set

**Required**:
```bash
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_KEEP_ALIVE=0
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
```

### 2. Discovery Screenshot Accumulation (MEDIUM)

**Issue**: `task_discovery._phase1_navigate` appends every screenshot to a list until DISCOVERY_READY or 30 steps. Up to 30 full PNGs in memory (~90 MB at 1280×720).

**Mitigation**: Cap in-memory list to last N (e.g. 5) during accumulation.

### 3. ComputerAgent Image Retention (MEDIUM)

**Issue**: `only_n_most_recent_images=10` keeps 10 images in agent context. Each ~1280×720 can be ~3 MB decoded. 10 × 3 MB = 30 MB per agent instance.

**Mitigation**: Reduce to 5 for both discovery and execution. Saves ~15 MB per agent.

### 4. Multiple Discovery Runs (LOW)

**Issue**: Meta-agent runs discovery for each persona × source. With 4 sources, 4 full discovery cycles. Each loads Qwen3-VL then Gemma3. With `keep_alive=0` models unload between runs.

**Mitigation**: Already OK if Ollama env is correct. No change.

### 5. No Pre-Cycle Memory Check (HIGH)

**Issue**: We don't check memory before loading UI-TARS. If system is already at 85%, loading 5.9 GB pushes over.

**Mitigation**: Add pre-cycle check; abort or warn if `psutil.virtual_memory().percent > 80`.

### 6. MemoryGuard Trigger Timing (MEDIUM)

**Issue**: MemoryGuard only checks before Ollama calls. UI-TARS (MLX) loads before any Ollama call. If we're tight, UI-TARS load could OOM.

**Mitigation**: Run MemoryGuard check at cycle start, before VM ensure_ready and any model use.

---

## Implemented Safeguards

1. **Pre-cycle memory check** in `SorenMetaAgent.run_cycle()` — abort with clear message if >85%
2. **OLLAMA env validation** in `just check-resources` — warns if `OLLAMA_MAX_LOADED_MODELS` != 1
3. **Discovery screenshot cap** — keep only last 5 in memory during phase 1
4. **Reduced `only_n_most_recent_images`** — 5 for discovery, 5 for execution (from 10)
5. **Fallback model** — MemoryGuard triggers gemma3:4b (~3 GB) when pressure detected

---

## Recommended User Setup (32 GB)

Soren uses a **dedicated Ollama instance on port 11435** so you can run it alongside your default Ollama (11434) with Soren-specific config.

Before running `just begin-soren-cycle` or `just test-e2e`:

1. **Copy `.env.example` to `.env`** and keep the defaults (or adjust as needed):
   ```bash
   cp .env.example .env
   ```

2. **Start the Soren Ollama instance** (port 11435, with memory-safe env vars):
   ```bash
   just ollama-serve
   ```
   Or manually: `source .env && ollama serve`

   **Note**: `~/.zshrc` does *not* apply to `brew services`; for launchd, edit the plist or use `launchctl setenv`.

3. **Pre-flight check**:
   ```bash
   just check-resources
   ```

---

## References

- `docs/discovery/model-memory-budget.md` — Full memory architecture
- `scripts/validate_memory_budget.py` — Empirical benchmark
- RFC §Model Architecture: Memory admission control
