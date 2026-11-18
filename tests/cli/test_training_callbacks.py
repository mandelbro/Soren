"""RichTrainingCallbacks: terminal prompts for training (step confirm, stuck handling, service-change confirm)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from soren.cli.training_callbacks import RichTrainingCallbacks


@pytest.mark.parametrize(("answer", "expected"), [("", True), ("y", True), ("YES", True), ("n", False), ("no", False)])
async def test_confirm_defaults_to_yes(answer: str, expected: bool) -> None:
    with patch("soren.cli.training_callbacks.console") as console:
        console.input.return_value = answer
        result = await RichTrainingCallbacks().confirm_service_update(
            Path("config/services/gcal.yaml"), ["config.chrome_height_px: 90 (/crop)"], ["comments will be lost"]
        )
    assert result is expected
    assert "[Y/n]" in console.input.call_args.args[0]


async def test_confirm_shows_path_changes_and_warnings() -> None:
    with patch("soren.cli.training_callbacks.console") as console:
        console.input.return_value = ""
        await RichTrainingCallbacks().confirm_service_update(
            Path("config/services/gcal.yaml"), ["config.instructions += Tip [x]"], ["overridden by settings"]
        )
    parts: list[str] = []
    for call in console.print.call_args_list:
        arg = call.args[0]
        parts += [str(getattr(arg, "title", "")), str(getattr(arg, "renderable", arg))]
    printed = " ".join(parts)
    assert "config/services/gcal.yaml" in printed
    assert "config.instructions += Tip" in printed
    assert "overridden by settings" in printed


class TestConfirmPromptDisplaysShortcutKeys:
    """Regression: Rich markup [y], [n], [d] must render as visible text, not get stripped as style tags."""

    async def test_prompt_shows_shortcut_keys(self) -> None:
        from rich.text import Text

        from soren.cli.training_callbacks import RichTrainingCallbacks

        cb = RichTrainingCallbacks()
        with patch("soren.cli.training_callbacks.console") as mock_console:
            mock_console.input.return_value = "y"
            await cb.confirm_step(b"fake-screenshot", "A login form")

            prompt_markup = mock_console.input.call_args[0][0]
            rendered = Text.from_markup(prompt_markup).plain
            assert "[y]" in rendered, f"Expected '[y]' in prompt, got: {rendered!r}"
            assert "[n]" in rendered, f"Expected '[n]' in prompt, got: {rendered!r}"
            assert "[d]" in rendered, f"Expected '[d]' in prompt, got: {rendered!r}"


class TestRichTrainingCallbacks:
    def test_callbacks_class_importable(self):
        from soren.cli.training_callbacks import RichTrainingCallbacks

        cb = RichTrainingCallbacks()
        assert hasattr(cb, "show_screenshot")
        assert hasattr(cb, "get_instruction")
        assert hasattr(cb, "confirm_step")
        assert hasattr(cb, "get_expected_after")
        assert hasattr(cb, "show_message")
        assert hasattr(cb, "show_error")
