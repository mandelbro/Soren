#!/usr/bin/env python3
"""Soren Memory Budget Validation Benchmark.

Measures actual memory consumption of each component in the Soren model stack
and validates the memory budget on the host machine. This script gates Phase 3
(Agent Execution Core) — if the memory arithmetic doesn't hold empirically,
the model architecture needs adjustment before building the execution pipeline.

Multi-signal monitoring approach:
    - psutil.virtual_memory() for percent and used_gb
    - macOS `memory_pressure` command (normal / warn / critical)
    - `vm_stat` for pageout count (swap canary)
    - Ollama /api/ps for loaded models and VRAM usage

Prerequisites:
    - macOS with Apple Silicon (unified memory)
    - Ollama running locally (http://localhost:11434)
    - Models pre-pulled: gemma3:12b, qwen3-vl:8b, gemma3:4b
    - Python 3.12+ with psutil and httpx installed

Usage:
    python scripts/validate_memory_budget.py
    python scripts/validate_memory_budget.py --vm-memory 8 --output memory-benchmark.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import psutil

OLLAMA_BASE_URL = "http://localhost:11434"


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
    snapshots: list[MemorySnapshot] = field(default_factory=list)
    component_costs: dict[str, float] = field(default_factory=dict)
    swap_timings: list[dict] = field(default_factory=list)
    verdict: str = ""


# ---------------------------------------------------------------------------
# Multi-signal memory monitoring
# ---------------------------------------------------------------------------

def get_memory_pressure() -> str:
    """Query macOS memory_pressure command. Returns 'normal', 'warn', or 'critical'."""
    try:
        result = subprocess.run(
            ["memory_pressure"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.lower()
        if "critical" in output:
            return "critical"
        if "warn" in output:
            return "warn"
        return "normal"
    except Exception:
        return "unknown"


def get_pageouts() -> int:
    """Parse pageout count from vm_stat."""
    try:
        result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "Pageouts" in line:
                return int(line.split(":")[1].strip().rstrip("."))
    except Exception:
        pass
    return 0


async def get_ollama_state(client: httpx.AsyncClient) -> dict:
    """Query Ollama /api/ps for loaded models and VRAM usage."""
    try:
        resp = await client.get(f"{OLLAMA_BASE_URL}/api/ps")
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"models": []}


async def take_snapshot(label: str, client: httpx.AsyncClient) -> MemorySnapshot:
    """Capture memory state from all monitoring sources."""
    vm = psutil.virtual_memory()
    pressure = get_memory_pressure()
    pageouts = get_pageouts()
    ollama = await get_ollama_state(client)

    models = ollama.get("models", [])
    total_vram = sum(m.get("size_vram", 0) for m in models) / (1024**3)

    return MemorySnapshot(
        timestamp=time.time(),
        label=label,
        psutil_percent=round(vm.percent, 1),
        psutil_used_gb=round(vm.used / (1024**3), 2),
        psutil_available_gb=round(vm.available / (1024**3), 2),
        memory_pressure=pressure,
        vm_stat_pageouts=pageouts,
        ollama_loaded_models=[
            {"name": m.get("name", ""), "size_vram_gb": round(m.get("size_vram", 0) / (1024**3), 2)}
            for m in models
        ],
        ollama_total_vram_gb=round(total_vram, 2),
    )


# ---------------------------------------------------------------------------
# Ollama model lifecycle helpers
# ---------------------------------------------------------------------------

async def ollama_load(client: httpx.AsyncClient, model: str, keep_alive: int = -1) -> None:
    """Load a model into Ollama by sending a minimal generate request."""
    await client.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={"model": model, "prompt": "Hello", "keep_alive": keep_alive, "stream": False},
    )


async def ollama_unload(client: httpx.AsyncClient, model: str) -> None:
    """Unload a model from Ollama by setting keep_alive to 0."""
    await client.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={"model": model, "keep_alive": 0, "stream": False},
    )


# ---------------------------------------------------------------------------
# Benchmark phases
# ---------------------------------------------------------------------------

async def benchmark_component_costs(
    client: httpx.AsyncClient,
    snapshots: list[MemorySnapshot],
) -> dict[str, float]:
    """Measure memory delta and load time for each model."""
    costs: dict[str, float] = {}

    baseline = await take_snapshot("baseline_for_costs", client)

    # --- Gemma3:12B ---
    print("  Loading gemma3:12b...")
    t0 = time.time()
    await ollama_load(client, "gemma3:12b")
    load_time = time.time() - t0
    snap = await take_snapshot("gemma3:12b_loaded", client)
    snapshots.append(snap)

    costs["gemma3_12b_delta_gb"] = round(snap.psutil_used_gb - baseline.psutil_used_gb, 2)
    costs["gemma3_12b_load_seconds"] = round(load_time, 2)
    costs["gemma3_12b_ollama_vram_gb"] = snap.ollama_total_vram_gb
    print(f"    Loaded in {load_time:.1f}s, delta {costs['gemma3_12b_delta_gb']:.2f} GB, "
          f"ollama reports {snap.ollama_total_vram_gb:.2f} GB VRAM")

    await ollama_unload(client, "gemma3:12b")
    await asyncio.sleep(2)

    # --- Qwen3-VL 8B ---
    print("  Loading qwen3-vl:8b...")
    unloaded_snap = await take_snapshot("after_gemma_unload", client)
    t0 = time.time()
    await ollama_load(client, "qwen3-vl:8b")
    load_time = time.time() - t0
    snap = await take_snapshot("qwen3-vl_loaded", client)
    snapshots.append(snap)

    costs["qwen3_vl_delta_gb"] = round(snap.psutil_used_gb - unloaded_snap.psutil_used_gb, 2)
    costs["qwen3_vl_load_seconds"] = round(load_time, 2)
    costs["qwen3_vl_ollama_vram_gb"] = snap.ollama_total_vram_gb
    print(f"    Loaded in {load_time:.1f}s, delta {costs['qwen3_vl_delta_gb']:.2f} GB, "
          f"ollama reports {snap.ollama_total_vram_gb:.2f} GB VRAM")

    await ollama_unload(client, "qwen3-vl:8b")
    await asyncio.sleep(2)

    # --- gemma3:4b (fallback) ---
    print("  Loading gemma3:4b (fallback)...")
    unloaded_snap = await take_snapshot("after_qwen_unload", client)
    t0 = time.time()
    await ollama_load(client, "gemma3:4b")
    load_time = time.time() - t0
    snap = await take_snapshot("gemma3_4b_loaded", client)
    snapshots.append(snap)

    costs["gemma3_4b_delta_gb"] = round(snap.psutil_used_gb - unloaded_snap.psutil_used_gb, 2)
    costs["gemma3_4b_load_seconds"] = round(load_time, 2)
    costs["gemma3_4b_ollama_vram_gb"] = snap.ollama_total_vram_gb
    print(f"    Loaded in {load_time:.1f}s, delta {costs['gemma3_4b_delta_gb']:.2f} GB, "
          f"ollama reports {snap.ollama_total_vram_gb:.2f} GB VRAM")

    await ollama_unload(client, "gemma3:4b")

    return costs


async def benchmark_swap_latency(
    client: httpx.AsyncClient,
    snapshots: list[MemorySnapshot],
) -> list[dict]:
    """Measure wall-clock time for model swap pairs."""
    swap_pairs = [
        ("gemma3:12b", "qwen3-vl:8b"),
        ("qwen3-vl:8b", "gemma3:12b"),
        ("gemma3:12b", "gemma3:4b"),
    ]
    timings: list[dict] = []

    for from_model, to_model in swap_pairs:
        print(f"  Swap: {from_model} -> {to_model}...")

        await ollama_load(client, from_model)
        await asyncio.sleep(1)

        t0 = time.time()
        await ollama_unload(client, from_model)
        await ollama_load(client, to_model)
        swap_time = time.time() - t0

        snap = await take_snapshot(f"swap_{from_model}_to_{to_model}", client)
        snapshots.append(snap)

        timings.append({
            "from": from_model,
            "to": to_model,
            "seconds": round(swap_time, 2),
        })
        print(f"    Completed in {swap_time:.1f}s")

        await ollama_unload(client, to_model)
        await asyncio.sleep(1)

    return timings


async def run_full_benchmark(vm_memory_gb: int, output_path: Path) -> None:
    """Run the complete memory budget validation."""
    total_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    snapshots: list[MemorySnapshot] = []

    print(f"\nMachine total memory: {total_gb} GB")
    print(f"VM memory allocation: {vm_memory_gb} GB")
    print(f"{'=' * 60}\n")

    async with httpx.AsyncClient(timeout=120) as client:
        # 1. Baseline snapshot
        print("[1/5] Taking baseline snapshot...")
        baseline = await take_snapshot("01_baseline", client)
        snapshots.append(baseline)
        print(f"  Used: {baseline.psutil_used_gb:.1f} GB ({baseline.psutil_percent}%), "
              f"Pressure: {baseline.memory_pressure}, Pageouts: {baseline.vm_stat_pageouts}")

        # 2. Component cost measurements
        print("\n[2/5] Measuring component memory costs...")
        component_costs = await benchmark_component_costs(client, snapshots)

        # 3. Swap latency tests
        print("\n[3/5] Measuring swap latency...")
        swap_timings = await benchmark_swap_latency(client, snapshots)

        # 4. Peak snapshot with Gemma3:12B
        print("\n[4/5] Taking peak snapshot with gemma3:12b loaded...")
        await ollama_load(client, "gemma3:12b")
        peak_snap = await take_snapshot("05_peak_gemma12b", client)
        snapshots.append(peak_snap)
        print(f"  Peak used: {peak_snap.psutil_used_gb:.1f} GB ({peak_snap.psutil_percent}%), "
              f"Pressure: {peak_snap.memory_pressure}, Pageouts: {peak_snap.vm_stat_pageouts}")
        await ollama_unload(client, "gemma3:12b")

        # 5. Calculate verdict
        print("\n[5/5] Calculating verdict...")
        peak_used = max(s.psutil_used_gb for s in snapshots)
        estimated_peak_with_vm = peak_used + vm_memory_gb
        headroom = total_gb - estimated_peak_with_vm

        if headroom > 3.0:
            verdict_label = "PASS"
        elif headroom > 1.0:
            verdict_label = "WARN"
        else:
            verdict_label = "FAIL"

        verdict = (
            f"{verdict_label}: {estimated_peak_with_vm:.1f} GB estimated peak "
            f"(incl. {vm_memory_gb} GB VM) / {total_gb} GB total "
            f"({headroom:.1f} GB headroom)"
        )

    result = BenchmarkResult(
        machine_total_gb=total_gb,
        snapshots=snapshots,
        component_costs=component_costs,
        swap_timings=swap_timings,
        verdict=verdict,
    )

    # Write full results to JSON
    result_dict = asdict(result)
    output_path.write_text(json.dumps(result_dict, indent=2, default=str))

    # Print console summary
    print(f"\n{'=' * 60}")
    print(f"VERDICT: {verdict}")
    print(f"{'=' * 60}")

    print(f"\nComponent costs:")
    for key, val in component_costs.items():
        unit = "s" if "seconds" in key else "GB"
        print(f"  {key}: {val} {unit}")

    print(f"\nSwap timings:")
    for swap in swap_timings:
        print(f"  {swap['from']} -> {swap['to']}: {swap['seconds']}s")

    print(f"\nMemory pressure at peak: {peak_snap.memory_pressure}")
    print(f"Pageouts at peak: {peak_snap.vm_stat_pageouts}")
    print(f"\nFull results written to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Soren Memory Budget Validation Benchmark"
    )
    parser.add_argument(
        "--vm-memory",
        type=int,
        default=8,
        help="Lume VM memory allocation in GB (default: 8)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="memory-benchmark.json",
        help="Output JSON file path (default: memory-benchmark.json)",
    )
    args = parser.parse_args()

    asyncio.run(run_full_benchmark(args.vm_memory, Path(args.output)))
