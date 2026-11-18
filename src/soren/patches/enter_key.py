"""Patch 4: UI-TARS Enter key fix.

UI-TARS appends \\n to type(content) when it wants to submit. The CUA SDK
passes \\n as literal text to type_text(), which the VM REST API silently drops.
This patch post-processes the action list: strips trailing \\n from type actions
and appends explicit keypress(["enter"]) (and optionally keypress(["escape"])).

Configurable via enter_key_behavior: "enter_only" (default) or "escape_enter",
per service (config/services/<name>.yaml) or globally (settings.yaml); see ADR-0002.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from soren.models.service import EnterKeyBehavior
from soren.patches.enter_key_config import get_enter_key_behavior

# Marker to avoid double-patching
_PATCHED_ENTER_KEY = "_soren_enter_key_patched"


def _post_process_actions(
    actions: list[Any],
    behavior: EnterKeyBehavior,
    make_type_item: Callable[[str], Any],
    make_keypress_item: Callable[[list[str]], Any],
) -> list[Any]:
    """Post-process action list: strip trailing \\n from type, append keypress.

    Items are cua_agent ``ResponseComputerToolCallParam`` TypedDicts (plain dicts).
    """
    result: list[Any] = []
    for item in actions:
        action = item.get("action") if isinstance(item, dict) else None
        if not isinstance(action, dict) or action.get("type") != "type":
            result.append(item)
            continue
        text = action.get("text", "")
        if not text.endswith("\n"):
            result.append(item)
            continue
        stripped = text.rstrip("\n")
        result.append(make_type_item(stripped))
        if behavior == "escape_enter":
            result.append(make_keypress_item(["escape"]))
        result.append(make_keypress_item(["enter"]))
    return result


def _wrap_convert_to_computer_actions(original: Any) -> Any:
    """Wrap convert_to_computer_actions to post-process type actions."""

    def patched_convert(parsed_responses: list[Any], image_width: int, image_height: int) -> list[Any]:
        actions = original(parsed_responses, image_width, image_height)
        behavior = get_enter_key_behavior()
        from cua_agent.responses import make_keypress_item, make_type_item

        return _post_process_actions(actions, behavior, make_type_item, make_keypress_item)

    return patched_convert


def apply_enter_key_patch() -> None:
    """Apply Patch 4. Patches cua_agent.loops.uitars.convert_to_computer_actions."""
    try:
        import cua_agent.loops.uitars as uitars_module
    except ImportError:
        return

    if getattr(uitars_module, _PATCHED_ENTER_KEY, False):
        return

    original = uitars_module.convert_to_computer_actions
    uitars_module.convert_to_computer_actions = _wrap_convert_to_computer_actions(original)
    setattr(uitars_module, _PATCHED_ENTER_KEY, True)
