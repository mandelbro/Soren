"""LLMService — Ollama wrapper with keep_alive=0 and MemoryGuard integration."""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

from ollama import AsyncClient
from pydantic import BaseModel

from soren.services.config_loader import SorenSettings
from soren.services.memory_guard import MemoryGuard

T = TypeVar("T", bound=BaseModel)

# Soren uses a dedicated Ollama instance on 11435 by default (see .env.example)
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11435"


class LLMService:
    """Single interface for all Ollama interactions with keep_alive=0."""

    KEEP_ALIVE = 0  # Immediate unload after response

    def __init__(self, settings: SorenSettings) -> None:
        self._settings = settings
        host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        self._client = AsyncClient(host=host)
        self._memory_guard = MemoryGuard(settings.llm)
        self._last_model_used: str | None = None

    @property
    def memory_guard(self) -> MemoryGuard:
        return self._memory_guard

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        images: list[bytes] | None = None,
    ) -> str:
        """Generate text with keep_alive=0. Uses MemoryGuard model when model is None."""
        model = model or await self._memory_guard.get_reasoning_model(self._client)
        self._last_model_used = model

        if images:
            resp = await self._client.generate(
                model=model,
                prompt=prompt,
                images=images,
                keep_alive=self.KEEP_ALIVE,
            )
        else:
            resp = await self._client.generate(
                model=model,
                prompt=prompt,
                keep_alive=self.KEEP_ALIVE,
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
        self._last_model_used = model
        full_prompt = f"{prompt}\n\nRespond with valid JSON matching this schema: {schema.model_json_schema()}"
        resp = await self._client.generate(
            model=model,
            prompt=full_prompt,
            format="json",
            keep_alive=self.KEEP_ALIVE,
        )
        text = resp.response or "{}"
        try:
            data = json.loads(text)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Invalid JSON response from LLM: {e}") from e

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
