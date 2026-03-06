"""Soren patches for CUA SDK and VM integration."""

# Patch 1 stub must be registered before any transformers import (e.g. from mlx_vlm)
from soren.patches.video_processor import _apply_video_processor_stub

_apply_video_processor_stub()

# Imports after stub; order intentional (E402)
from soren.patches.enter_key import apply_enter_key_patch  # noqa: E402
from soren.patches.mlx_generate import apply_mlx_generate_patch  # noqa: E402
from soren.patches.video_processor import apply_video_processor_patches  # noqa: E402
from soren.patches.vnc_screenshot import patch_computer_screenshot  # noqa: E402

__all__ = [
    "apply_all",
    "patch_computer_screenshot",
]


def apply_all() -> None:
    """Apply all CUA SDK patches in correct order. Idempotent.

    Order: Patches 1-2 (video processor) before transformers usage;
    Patch 3 (mlx generate) before mlx_vlm import; Patch 4 (enter key) at runtime.
    """
    apply_video_processor_patches()
    apply_mlx_generate_patch()
    apply_enter_key_patch()
