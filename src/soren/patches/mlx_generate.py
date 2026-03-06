"""Patch 3: mlx-vlm generate() compatibility.

mlx-vlm >=0.3 returns a GenerationResult dataclass, but the CUA SDK's
MLXVLMAdapter unpacks the return value as (text, usage) tuple. This patch
wraps mlx_vlm.generate to return a tuple compatible with the adapter.
"""

from __future__ import annotations

from typing import Any

# Marker to avoid double-patching
_PATCHED_MLX_GENERATE = "_soren_mlx_generate_patched"


def _wrap_generate(original: Any) -> Any:
    """Wrap generate to return (text, usage_dict) from GenerationResult."""

    def patched_generate(*args: Any, **kwargs: Any) -> tuple[str, dict[str, int]]:
        result = original(*args, **kwargs)
        # GenerationResult has text, prompt_tokens, generation_tokens
        if hasattr(result, "text") and hasattr(result, "prompt_tokens") and hasattr(result, "generation_tokens"):
            usage = {
                "prompt_tokens": getattr(result, "prompt_tokens", 0),
                "generation_tokens": getattr(result, "generation_tokens", 0),
            }
            return (getattr(result, "text", ""), usage)
        # Already a tuple (older mlx-vlm)
        return result

    return patched_generate


def apply_mlx_generate_patch() -> None:
    """Apply Patch 3. Must run before any mlx_vlm import that uses generate."""
    import importlib

    gen_module = importlib.import_module("mlx_vlm.generate")

    if getattr(gen_module, _PATCHED_MLX_GENERATE, False):
        return

    original = gen_module.generate
    patched = _wrap_generate(original)
    gen_module.generate = patched
    setattr(gen_module, _PATCHED_MLX_GENERATE, True)
    # Also update mlx_vlm package's generate (adapter may use "from mlx_vlm import generate")
    import mlx_vlm

    mlx_vlm.generate = patched
