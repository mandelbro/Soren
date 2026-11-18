"""agent_output: shared parsing of CUA ComputerAgent output items (executor, discovery, training)."""

from __future__ import annotations

import base64

import pytest

from soren.services.agent_output import (
    contains_text,
    decode_image,
    has_assistant_final,
    is_completion_message,
    parse_output_item,
    screenshot_from_item,
)

PNG = b"\x89PNG fake"
B64 = base64.b64encode(PNG).decode()


def _output_item(**output) -> dict:
    return {"type": "computer_call_output", "output": output}


class TestDecodeImage:
    @pytest.mark.parametrize("data", [B64, f"data:image/png;base64,{B64}", B64.encode()])
    def test_decodes_base64_and_data_urls(self, data) -> None:
        assert decode_image(data) == PNG

    @pytest.mark.parametrize("data", [None, "", "not base64!!"])
    def test_invalid_or_empty(self, data) -> None:
        assert decode_image(data) is None


class TestScreenshotFromItem:
    @pytest.mark.parametrize("key", ["image_url", "image", "screenshot"])
    def test_reads_each_supported_key(self, key: str) -> None:
        assert screenshot_from_item(_output_item(**{key: B64})) == PNG

    def test_image_url_preferred(self) -> None:
        other = base64.b64encode(b"other").decode()
        assert screenshot_from_item(_output_item(image_url=B64, screenshot=other)) == PNG

    @pytest.mark.parametrize(
        "item",
        [
            {"type": "computer_call", "output": {"image_url": B64}},
            {"type": "computer_call_output", "output": "not a dict"},
            _output_item(),
            "not a dict",
            None,
        ],
    )
    def test_non_screenshot_items(self, item) -> None:
        assert screenshot_from_item(item) is None


class TestParseOutputItem:
    def test_computer_call_with_nested_call(self) -> None:
        item = {"type": "computer_call", "call": {"action": {"type": "click", "x": 1}}, "summary": "why"}
        assert parse_output_item(item, 3) == {
            "step": 3,
            "action_type": "click",
            "action_data": {"type": "click", "x": 1},
            "vlm_reasoning": "why",
        }

    def test_computer_call_flat_action_and_reasoning_fallback(self) -> None:
        item = {"type": "computer_call", "action": {"type": "type", "text": "hi"}, "reasoning": "r"}
        parsed = parse_output_item(item, 1)
        assert parsed is not None
        assert (parsed["action_type"], parsed["vlm_reasoning"]) == ("type", "r")

    def test_assistant_message_with_parts(self) -> None:
        item = {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "All"},
                {"type": "other"},
                {"type": "output_text", "text": "done"},
            ],
        }
        assert parse_output_item(item, 2) == {
            "step": 2,
            "action_type": "message",
            "action_data": {"content": "All done"},
            "vlm_reasoning": "All done",
        }

    def test_reasoning_truncated_to_500_chars(self) -> None:
        parsed = parse_output_item({"type": "message", "role": "assistant", "content": "x" * 900}, 1)
        assert parsed is not None and len(parsed["vlm_reasoning"]) == 500

    @pytest.mark.parametrize(
        "item",
        [
            {"type": "computer_call", "action": "click(100, 200)"},
            {"type": "computer_call", "action": None},
            {"type": "computer_call", "action": ["click"]},
            {"type": "computer_call", "call": {"action": "click"}},
            {"type": "computer_call", "call": "not a dict"},
        ],
    )
    def test_non_dict_actions_logged_as_unknown(self, item) -> None:
        """An unexpected SDK shape must not crash the task: log the step as an unknown action."""
        parsed = parse_output_item(item, 4)
        assert parsed is not None
        assert (parsed["step"], parsed["action_type"], parsed["action_data"]) == (4, "unknown", {})

    @pytest.mark.parametrize("item", ["text", {"type": "message", "role": "user", "content": "hi"}, {"type": "other"}])
    def test_ignored_items(self, item) -> None:
        assert parse_output_item(item, 1) is None


class TestCompletion:
    @pytest.mark.parametrize("text", ["Task DONE", "completed it", "I have finished"])
    def test_string_content_keywords(self, text: str) -> None:
        assert is_completion_message([{"role": "assistant", "content": text}])

    def test_list_content_done_or_complete(self) -> None:
        assert is_completion_message([{"role": "assistant", "content": [{"text": "Complete"}]}])

    def test_list_content_ignores_finished(self) -> None:
        """Pins existing behavior: list-form content checks only 'done'/'complete' (see batch C notes)."""
        assert not is_completion_message([{"role": "assistant", "content": [{"text": "finished"}]}])

    @pytest.mark.parametrize(
        "output",
        [[], [{"role": "user", "content": "done"}], [{"role": "assistant", "content": "working"}], ["done"]],
    )
    def test_not_completion(self, output) -> None:
        assert not is_completion_message(output)

    def test_has_assistant_final(self) -> None:
        assert has_assistant_final([{"role": "user"}, {"role": "assistant"}])
        assert not has_assistant_final([{"role": "user"}, "text"])


class TestContainsText:
    @pytest.mark.parametrize(
        ("item", "expected"),
        [
            ({"content": "ok DISCOVERY_READY"}, True),
            ({"content": [{"text": "x"}, {"text": "DISCOVERY_READY now"}]}, True),
            ({"content": [{"text": "nope"}, "DISCOVERY_READY"]}, False),
            ({"content": None}, False),
            ({}, False),
        ],
    )
    def test_finds_token(self, item, expected: bool) -> None:
        assert contains_text(item, "DISCOVERY_READY") is expected
