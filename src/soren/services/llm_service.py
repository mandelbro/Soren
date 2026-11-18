"""LLMService — Ollama wrapper with MemoryGuard integration."""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

from ollama import AsyncClient
from pydantic import BaseModel

from soren.models.llm import is_cloud_model
from soren.services.config_loader import SorenSettings
from soren.services.credential_redaction import ensure_safe_for_cloud
from soren.services.memory_guard import MemoryGuard

T = TypeVar("T", bound=BaseModel)

# Soren uses a dedicated Ollama instance on 11435 by default (see .env.example)
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11435"

# Cloud models run on remote infrastructure — no need to force immediate unload.
# Local models use keep_alive=0 to free VRAM immediately after each call.
KEEP_ALIVE_CLOUD: int | None = None  # Ollama server default (typically 5m)
KEEP_ALIVE_LOCAL: int = 0  # Immediate unload


class LLMService:
    """Single interface for all Ollama interactions."""

    def __init__(self, settings: SorenSettings) -> None:
        self._settings = settings
        self._is_cloud = settings.llm.is_cloud_model
        host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        self._client = AsyncClient(host=host)
        self._memory_guard = MemoryGuard(settings.llm)
        self._last_model_used: str | None = None

    @property
    def memory_guard(self) -> MemoryGuard:
        return self._memory_guard

    def _keep_alive(self) -> int | None:
        """Return the keep_alive value appropriate for the current model type."""
        return KEEP_ALIVE_CLOUD if self._is_cloud else KEEP_ALIVE_LOCAL

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        images: list[bytes] | None = None,
    ) -> str:
        """Generate text. Uses MemoryGuard model when model is None."""
        model = model or await self._memory_guard.get_reasoning_model(self._client)
        self._guard_cloud_prompt(prompt, model)
        self._last_model_used = model
        ka = self._keep_alive()

        if images:
            resp = await self._client.generate(
                model=model,
                prompt=prompt,
                images=images,
                keep_alive=ka,
            )
        else:
            resp = await self._client.generate(
                model=model,
                prompt=prompt,
                keep_alive=ka,
            )
        return resp.response or ""

    async def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        model: str | None = None,
    ) -> T:
        """Generate structured output via JSON mode + Pydantic parsing."""
        model = model or await self._memory_guard.get_reasoning_model(self._client)
        self._guard_cloud_prompt(prompt, model)
        self._last_model_used = model
        full_prompt = f"{prompt}\n\nRespond with valid JSON matching this schema: {schema.model_json_schema()}"
        resp = await self._client.generate(
            model=model,
            prompt=full_prompt,
            format="json",
            keep_alive=self._keep_alive(),
        )
        text = resp.response or "{}"
        try:
            data = json.loads(text)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Invalid JSON response from LLM: {e}") from e

    @staticmethod
    def _guard_cloud_prompt(prompt: str, model: str) -> None:
        """Credentials must never reach a cloud model (RFC §Local or Ollama Cloud Models)."""
        if is_cloud_model(model):
            ensure_safe_for_cloud(prompt, model)

    async def is_available(self) -> bool:
        """Check Ollama server reachability."""
        try:
            await self._client.list()
            return True
        except Exception:
            return False

    async def get_loaded_models(self) -> list[dict[str, Any]]:
        """Query GET /api/ps for loaded models."""
        try:
            resp = await self._client.ps()
            models = getattr(resp, "models", []) or []
            return [{"name": getattr(m, "name", ""), "size_vram": getattr(m, "size_vram", 0)} for m in models]
        except Exception:
            return []

    async def unload_model(self, model: str) -> None:
        """Send keep_alive=0 to force unload."""
        try:
            await self._client.generate(model=model, prompt="", keep_alive=0)
        except Exception:
            pass

    def get_telemetry(self) -> dict[str, Any]:
        """Return model selection and memory-guard telemetry for cycle reports."""
        return {
            "model_used": self._last_model_used,
            "fallback_triggered": self._memory_guard.fallback_triggered,
            "memory_metrics": self._memory_guard.last_metrics or None,
        }
