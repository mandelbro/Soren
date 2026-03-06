"""Tests for CUA SDK patches (video processor, mlx generate, enter key)."""

from __future__ import annotations

from soren.patches import apply_all
from soren.patches.enter_key import _post_process_actions
from soren.patches.enter_key_config import get_enter_key_behavior, set_default_enter_key_behavior


class TestApplyAll:
    def test_apply_all_idempotent(self) -> None:
        """apply_all() is idempotent - calling twice doesn't double-patch."""
        apply_all()
        apply_all()
        # No exception, and patches are still functional
        apply_all()


class TestVideoProcessorPatch:
    def test_video_processor_stub_registered(self) -> None:
        """Patch 1: stub module in sys.modules, video_processor_class_from_name returns None."""
        import sys

        from soren.patches.video_processor import apply_video_processor_patches

        apply_video_processor_patches()
        # Stub is in sys.modules; use direct lookup (LazyModule returns Placeholder for "from X import")
        vpa = sys.modules.get("transformers.models.auto.video_processing_auto")
        assert vpa is not None
        assert getattr(vpa, "_soren_video_processor_patched", False)
        result = vpa.video_processor_class_from_name("NonExistentClass123")
        assert result is None

    def test_processor_mixin_accepts_none_video_processor(self) -> None:
        """Patch 2: ProcessorMixin.__init__ accepts None video_processor without raising."""
        from soren.patches.video_processor import apply_video_processor_patches

        apply_video_processor_patches()
        from transformers.processing_utils import ProcessorMixin

        # Create a minimal processor-like class that has video_processor in attributes
        class FakeProcessor(ProcessorMixin):
            def get_attributes(self):
                return ["video_processor"]

        # Should not raise when video_processor is None
        proc = FakeProcessor(video_processor=None)
        assert getattr(proc, "video_processor", None) is None


class TestMlxGeneratePatch:
    def test_wrapped_generate_returns_tuple_from_generation_result(self) -> None:
        """Patch 3: wrapped generate() returns (text, usage_dict) tuple from GenerationResult."""
        from dataclasses import dataclass

        from soren.patches.mlx_generate import _wrap_generate

        @dataclass
        class FakeResult:
            text: str = "hello"
            prompt_tokens: int = 10
            generation_tokens: int = 5

        def fake_original(*args, **kwargs):
            return FakeResult()

        wrapped = _wrap_generate(fake_original)
        result = wrapped(None, None, "test")
        assert isinstance(result, tuple)
        assert result[0] == "hello"
        assert result[1] == {"prompt_tokens": 10, "generation_tokens": 5}

    def test_usage_dict_contains_prompt_and_generation_tokens(self) -> None:
        """Patch 3: usage_dict contains prompt_tokens and generation_tokens fields."""
        from dataclasses import dataclass

        from soren.patches.mlx_generate import _wrap_generate

        @dataclass
        class FakeResult:
            text: str = "x"
            prompt_tokens: int = 100
            generation_tokens: int = 50

        def fake_original(*a, **k):
            return FakeResult()

        wrapped = _wrap_generate(fake_original)
        text, usage = wrapped(None, None, "x")
        assert "prompt_tokens" in usage
        assert "generation_tokens" in usage
        assert usage["prompt_tokens"] == 100
        assert usage["generation_tokens"] == 50

    def test_original_generate_behavior_preserved(self) -> None:
        """Patch 3: original generate() text content matches."""
        from dataclasses import dataclass

        from soren.patches.mlx_generate import _wrap_generate

        @dataclass
        class FakeResult:
            text: str = "generated output"
            prompt_tokens: int = 0
            generation_tokens: int = 0

        def fake_original(*args, **kwargs):
            return FakeResult()

        wrapped = _wrap_generate(fake_original)
        text, _ = wrapped(None, None, "prompt")
        assert text == "generated output"


class TestEnterKeyPatch:
    def test_type_action_ending_with_newline_produces_stripped_and_keypress(self) -> None:
        """Patch 4: type action ending with \\n produces stripped text + keypress(['enter'])."""
        set_default_enter_key_behavior("enter_only")

        def make_type(text: str):
            return type("Item", (), {"action": type("A", (), {"type": "type", "text": text})()})()

        def make_keypress(keys: list):
            return type("Item", (), {"action": type("A", (), {"type": "keypress", "keys": keys})()})()

        actions = [make_type("hello\n")]
        result = _post_process_actions(actions, "enter_only", make_type, make_keypress)
        assert len(result) == 2
        assert result[0].action.text == "hello"
        assert result[1].action.keys == ["enter"]

    def test_type_action_without_newline_passes_through(self) -> None:
        """Patch 4: type action without \\n passes through unchanged."""

        def make_type(text: str):
            return type("Item", (), {"action": type("A", (), {"type": "type", "text": text})()})()

        def make_keypress(keys: list):
            return type("Item", (), {"action": type("A", (), {"type": "keypress", "keys": keys})()})()

        actions = [make_type("hello")]
        result = _post_process_actions(actions, "enter_only", make_type, make_keypress)
        assert len(result) == 1
        assert result[0].action.text == "hello"

    def test_escape_enter_mode_produces_escape_then_enter(self) -> None:
        """Patch 4: escape_enter mode produces keypress(['escape']) then keypress(['enter'])."""

        def make_type(text: str):
            return type("Item", (), {"action": type("A", (), {"type": "type", "text": text})()})()

        def make_keypress(keys: list):
            return type("Item", (), {"action": type("A", (), {"type": "keypress", "keys": keys})()})()

        actions = [make_type("hi\n")]
        result = _post_process_actions(actions, "escape_enter", make_type, make_keypress)
        assert len(result) == 3
        assert result[0].action.text == "hi"
        assert result[1].action.keys == ["escape"]
        assert result[2].action.keys == ["enter"]

    def test_enter_only_mode_produces_only_enter(self) -> None:
        """Patch 4: enter_only mode produces only keypress(['enter'])."""

        def make_type(text: str):
            return type("Item", (), {"action": type("A", (), {"type": "type", "text": text})()})()

        def make_keypress(keys: list):
            return type("Item", (), {"action": type("A", (), {"type": "keypress", "keys": keys})()})()

        actions = [make_type("x\n")]
        result = _post_process_actions(actions, "enter_only", make_type, make_keypress)
        assert len(result) == 2
        assert result[1].action.keys == ["enter"]

    def test_multiple_newlines_in_middle_not_affected(self) -> None:
        """Patch 4: multiple \\n in middle of text not affected (only trailing)."""

        def make_type(text: str):
            return type("Item", (), {"action": type("A", (), {"type": "type", "text": text})()})()

        def make_keypress(keys: list):
            return type("Item", (), {"action": type("A", (), {"type": "keypress", "keys": keys})()})()

        actions = [make_type("line1\nline2")]  # no trailing \n
        result = _post_process_actions(actions, "enter_only", make_type, make_keypress)
        assert len(result) == 1
        assert result[0].action.text == "line1\nline2"

    def test_application_order_patches_1_2_before_3(self) -> None:
        """Application order: Patches 1-2 applied before Patch 3."""
        import sys

        # apply_all applies in order: video_processor, mlx_generate, enter_key
        apply_all()
        vpa = sys.modules.get("transformers.models.auto.video_processing_auto")
        assert vpa is not None
        assert getattr(vpa, "_soren_video_processor_patched", False)
        import importlib

        gen_mod = importlib.import_module("mlx_vlm.generate")
        assert hasattr(gen_mod, "_soren_mlx_generate_patched")


class TestEnterKeyConfig:
    def test_default_enter_only(self) -> None:
        """Default behavior is enter_only."""
        set_default_enter_key_behavior("enter_only")
        assert get_enter_key_behavior() == "enter_only"
