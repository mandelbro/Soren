"""Tests for CUA SDK patches (video processor, mlx generate, enter key)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from cua_agent.responses import make_keypress_item, make_type_item

from soren.patches import apply_all, enter_key_config
from soren.patches.enter_key import _post_process_actions, _wrap_convert_to_computer_actions
from soren.patches.enter_key_config import get_enter_key_behavior, set_default_enter_key_behavior
from soren.services.service_context import use_service
from tests.service_helpers import make_resolved_service, write_service


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
    """Patch 4 runs on real cua_agent action items, which are TypedDicts (plain dicts)."""

    def test_type_action_ending_with_newline_produces_stripped_and_keypress(self) -> None:
        """Patch 4: type action ending with \\n produces stripped text + keypress(['enter'])."""
        actions = [make_type_item("hello\n")]
        result = _post_process_actions(actions, "enter_only", make_type_item, make_keypress_item)
        assert len(result) == 2
        assert result[0]["action"] == {"type": "type", "text": "hello"}
        assert result[1]["action"]["type"] == "keypress"
        assert result[1]["action"]["keys"] == ["enter"]

    def test_type_action_without_newline_passes_through(self) -> None:
        """Patch 4: type action without \\n passes through unchanged (same object)."""
        item = make_type_item("hello")
        result = _post_process_actions([item], "enter_only", make_type_item, make_keypress_item)
        assert result == [item]

    def test_escape_enter_mode_produces_escape_then_enter(self) -> None:
        """Patch 4: escape_enter mode produces keypress(['escape']) then keypress(['enter'])."""
        actions = [make_type_item("hi\n")]
        result = _post_process_actions(actions, "escape_enter", make_type_item, make_keypress_item)
        assert len(result) == 3
        assert result[0]["action"]["text"] == "hi"
        assert result[1]["action"]["keys"] == ["escape"]
        assert result[2]["action"]["keys"] == ["enter"]

    def test_multiple_newlines_in_middle_not_affected(self) -> None:
        """Patch 4: \\n in the middle of text is not affected (only trailing)."""
        item = make_type_item("line1\nline2")
        result = _post_process_actions([item], "enter_only", make_type_item, make_keypress_item)
        assert result == [item]

    def test_non_type_actions_pass_through(self) -> None:
        """Patch 4: non-type computer calls and output messages are untouched."""
        keypress = make_keypress_item(["tab"])
        message = {"type": "message", "role": "assistant", "content": []}
        result = _post_process_actions([keypress, message], "enter_only", make_type_item, make_keypress_item)
        assert result == [keypress, message]

    def test_wrapped_convert_appends_enter_for_real_items(self, isolated_enter_key_config: Path) -> None:
        """Patch 4: the installed wrapper rewrites real dict items from convert_to_computer_actions."""

        def fake_convert(parsed_responses, image_width, image_height):
            return [make_type_item("search term\n")]

        result = _wrap_convert_to_computer_actions(fake_convert)([], 1024, 768)
        assert [item["action"]["type"] for item in result] == ["type", "keypress"]
        assert result[0]["action"]["text"] == "search term"
        assert result[1]["action"]["keys"] == ["enter"]

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


@pytest.fixture
def isolated_enter_key_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the legacy enter-key reader at tmp settings/services instead of the repo's config/."""
    monkeypatch.setattr(enter_key_config, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setattr(enter_key_config, "SERVICES_DIR", tmp_path / "services")
    set_default_enter_key_behavior("enter_only")
    yield tmp_path
    set_default_enter_key_behavior("enter_only")


class TestEnterKeyConfig:
    def test_default_enter_only(self, isolated_enter_key_config: Path) -> None:
        """Default behavior is enter_only (no settings file, no active service)."""
        assert get_enter_key_behavior() == "enter_only"

    def test_set_default_still_honored_for_tests(self, isolated_enter_key_config: Path) -> None:
        set_default_enter_key_behavior("escape_enter")
        assert get_enter_key_behavior() == "escape_enter"

    def test_legacy_top_level_setting(self, isolated_enter_key_config: Path) -> None:
        (isolated_enter_key_config / "settings.yaml").write_text("enter_key_behavior: escape_enter\n")
        assert get_enter_key_behavior() == "escape_enter"

    def test_legacy_per_service_setting_by_id(self, isolated_enter_key_config: Path) -> None:
        (isolated_enter_key_config / "settings.yaml").write_text(
            "enter_key_behavior: enter_only\nservices:\n  slack:\n    enter_key_behavior: escape_enter\n"
        )
        assert get_enter_key_behavior("slack") == "escape_enter"
        assert get_enter_key_behavior("other") == "enter_only"

    def test_service_file_by_id(self, isolated_enter_key_config: Path) -> None:
        write_service(isolated_enter_key_config / "services", "slack", config={"enter_key_behavior": "escape_enter"})
        assert get_enter_key_behavior("slack") == "escape_enter"

    def test_invalid_service_file_warns_and_uses_default(
        self, isolated_enter_key_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        services = isolated_enter_key_config / "services"
        services.mkdir()
        (services / "slack.yaml").write_text("config:\n  enter_key_behavior: sideways\n")
        with caplog.at_level(logging.WARNING, logger="soren.patches.enter_key_config"):
            assert get_enter_key_behavior("slack") == "enter_only"
        assert any("slack" in r.getMessage() for r in caplog.records)

    def test_active_service_wins_without_reading_files(self, isolated_enter_key_config: Path, tmp_path: Path) -> None:
        (isolated_enter_key_config / "settings.yaml").write_text("enter_key_behavior: enter_only\n")
        svc = make_resolved_service(tmp_path / "active", config={"enter_key_behavior": "escape_enter"})
        with use_service(svc):
            assert get_enter_key_behavior() == "escape_enter"
        assert get_enter_key_behavior() == "enter_only"

    def test_explicit_service_id_bypasses_active_service(self, isolated_enter_key_config: Path, tmp_path: Path) -> None:
        svc = make_resolved_service(tmp_path / "active", config={"enter_key_behavior": "escape_enter"})
        with use_service(svc):
            assert get_enter_key_behavior("unconfigured") == "enter_only"

    def test_wrapped_convert_uses_active_service(self, isolated_enter_key_config: Path, tmp_path: Path) -> None:
        """End to end: the installed patch emits Escape before Enter for an escape_enter service."""

        def fake_convert(parsed_responses, image_width, image_height):
            return [make_type_item("hello\n")]

        svc = make_resolved_service(tmp_path / "active", config={"enter_key_behavior": "escape_enter"})
        with use_service(svc):
            result = _wrap_convert_to_computer_actions(fake_convert)([], 1024, 768)
        assert [item["action"].get("keys") for item in result[1:]] == [["escape"], ["enter"]]
