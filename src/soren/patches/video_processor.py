"""Patches 1 & 2: Video processor stub and ProcessorMixin compatibility.

Patch 1: Pre-register stub in sys.modules before transformers imports.
transformers >=5.1 loads video_processing_auto internally; when PyTorch is absent,
values are None and 'class_name in extractors' crashes. UI-TARS only processes
still images, so the video processor is never used. The stub prevents the crash.

Patch 2: Relax ProcessorMixin.check_argument_for_proper_class to accept None
for video_processor, since Qwen2VLProcessor receives None when video is unused.

Tracked upstream: https://github.com/huggingface/transformers/issues/35719
"""

from __future__ import annotations

import sys
import types
from typing import Any

# Marker to avoid double-patching
_PATCHED_VIDEO_PROCESSOR = "_soren_video_processor_patched"
_PATCHED_PROCESSOR_MIXIN = "_soren_processor_mixin_patched"
_VPA_MODULE = "transformers.models.auto.video_processing_auto"


def _apply_video_processor_stub() -> None:
    """Patch 1: Pre-register stub module in sys.modules before transformers import."""
    if _VPA_MODULE in sys.modules:
        existing = sys.modules[_VPA_MODULE]
        if getattr(existing, _PATCHED_VIDEO_PROCESSOR, False):
            return

    _vpa_stub = types.ModuleType(_VPA_MODULE)
    _vpa_stub.VIDEO_PROCESSOR_MAPPING_NAMES = {}
    _vpa_stub.VIDEO_PROCESSOR_MAPPING = {}
    _vpa_stub.video_processor_class_from_name = lambda class_name: None

    class _StubAutoVideoProcessor:
        @classmethod
        def from_pretrained(cls, *args: Any, **kwargs: Any) -> None:
            return None

    _vpa_stub.AutoVideoProcessor = _StubAutoVideoProcessor
    setattr(_vpa_stub, _PATCHED_VIDEO_PROCESSOR, True)
    sys.modules[_VPA_MODULE] = _vpa_stub


def _apply_processor_mixin_patch() -> None:
    """Patch 2: Allow None for video_processor in ProcessorMixin."""
    try:
        from transformers.processing_utils import ProcessorMixin
    except ImportError:
        return

    if getattr(ProcessorMixin, _PATCHED_PROCESSOR_MIXIN, False):
        return

    _original_check = ProcessorMixin.check_argument_for_proper_class

    def _lenient_check(self: Any, argument_name: str, argument: Any) -> Any:
        if argument is None:
            return
        return _original_check(self, argument_name, argument)

    ProcessorMixin.check_argument_for_proper_class = _lenient_check
    setattr(ProcessorMixin, _PATCHED_PROCESSOR_MIXIN, True)


def apply_video_processor_patches() -> None:
    """Apply Patches 1 and 2. Must run before any mlx_vlm/transformers usage."""
    _apply_video_processor_stub()
    _apply_processor_mixin_patch()
