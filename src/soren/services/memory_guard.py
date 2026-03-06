"""MemoryGuard — multi-signal admission control for model selection."""

from __future__ import annotations

import subprocess
from typing import Any

import psutil

from soren.models.llm import LLMConfig


class MemoryGuard:
    """Multi-signal memory pressure detection for reasoning model selection."""

    def __init__(self, llm_config: LLMConfig) -> None:
        self._config = llm_config
        self._fallback_triggered = False

    def reset_cycle(self) -> None:
        """Clear fallback flag for next cycle."""
        self._fallback_triggered = False

    async def get_reasoning_model(self, ollama_client: Any | None = None) -> str:
        """Return primary or fallback model based on memory pressure."""
        if self._fallback_triggered:
            return self._config.fallback_model

        if await self._check_pressure(ollama_client):
            self._fallback_triggered = True
            return self._config.fallback_model

        return self._config.primary_model

    async def _check_pressure(self, ollama_client: Any | None) -> bool:
        """Return True if memory pressure triggers fallback."""
        # Signal 1: Ollama /api/ps memory (captured for metrics; threshold TBD)
        _ = await self._get_ollama_memory(ollama_client)

        # Signal 2: macOS memory_pressure
        pressure = self._get_memory_pressure()

        # Signal 3: psutil
        psutil_pct = self._get_psutil_pressure()

        # Signal 4: vm_stat pageouts
        pageouts = self._get_pageout_count()

        # Trigger: critical pressure
        if pressure == "critical":
            return True

        # Trigger: warn + psutil > 85%
        if pressure == "warn" and psutil_pct > 85:
            return True

        # Trigger: psutil > 90% alone
        if psutil_pct > 90:
            return True

        # Trigger: pageout growth (simplified: high absolute count as proxy)
        if pageouts > 1_000_000:
            return True

        return False

    async def _get_ollama_memory(self, client: Any | None) -> float:
        """Query Ollama /api/ps for total size_vram in GB."""
        if client is None:
            return 0.0
        try:
            resp = await client.ps()
            total = 0.0
            for m in getattr(resp, "models", []) or []:
                size = getattr(m, "size_vram", 0) or 0
                total += size
            return total / (1024**3)  # bytes to GB
        except Exception:
            return 0.0

    def _get_memory_pressure(self) -> str:
        """Shell out to macOS memory_pressure. Returns normal/warn/critical."""
        try:
            result = subprocess.run(
                ["memory_pressure"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = (result.stdout or "") + (result.stderr or "")
            out_lower = out.lower()
            if "critical" in out_lower:
                return "critical"
            if "warn" in out_lower:
                return "warn"
            return "normal"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "normal"

    def _get_psutil_pressure(self) -> float:
        """Return psutil virtual_memory().percent."""
        try:
            return psutil.virtual_memory().percent
        except Exception:
            return 0.0

    def _get_pageout_count(self) -> int:
        """Parse vm_stat for pageouts."""
        try:
            result = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in (result.stdout or "").splitlines():
                if "pageouts" in line.lower():
                    parts = line.split(":")
                    if len(parts) == 2:
                        return int(parts[1].strip().rstrip("."))
            return 0
        except Exception:
            return 0
