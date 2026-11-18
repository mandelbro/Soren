"""Parsing helpers for CUA ``ComputerAgent.run()`` output items.

Shared by the task executor, discovery, and training so the SDK's output
shapes are interpreted in one place. Items are plain dicts:

- ``computer_call``: the VLM's planned action (``action`` directly, or nested
  under ``call``)
- ``computer_call_output``: the post-action screenshot, under ``output`` as
  ``image_url`` / ``image`` / ``screenshot`` (base64, optionally a ``data:`` URL)
- ``message`` with ``role == "assistant"``: text, or a list of
  ``{"type": "output_text", "text": ...}`` parts
"""

from __future__ import annotations

import base64
from typing import Any

_IMAGE_KEYS = ("image_url", "image", "screenshot")


def decode_image(data: Any) -> bytes | None:
    """Decode base64 image data (a plain string, bytes, or ``data:...;base64,`` URL). None if invalid."""
    if not data:
        return None
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        return base64.b64decode(data)
    except Exception:
        return None


def screenshot_from_item(item: Any) -> bytes | None:
    """Screenshot bytes from a ``computer_call_output`` item, else None."""
    if not isinstance(item, dict) or item.get("type") != "computer_call_output":
        return None
    output = item.get("output", {})
    if not isinstance(output, dict):
        return None
    return decode_image(next((output[k] for k in _IMAGE_KEYS if output.get(k)), None))


def parse_output_item(item: Any, step: int) -> dict[str, Any] | None:
    """Step-log entry (action type/data and reasoning) for an action or assistant message, else None."""
    if not isinstance(item, dict):
        return None
    kind = item.get("type") or item.get("role")
    if kind == "computer_call":
        call = item.get("call", item)
        action = call.get("action", {}) if isinstance(call, dict) else {}
        if not isinstance(action, dict):
            action = {}  # unexpected SDK shape: log as an unknown action rather than failing the task
        return {
            "step": step,
            "action_type": action.get("type", "unknown"),
            "action_data": action,
            "vlm_reasoning": item.get("summary", item.get("reasoning")),
        }
    if kind == "message" and item.get("role") == "assistant":
        content = item.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "output_text"
            )
        return {
            "step": step,
            "action_type": "message",
            "action_data": {"content": str(content)},
            "vlm_reasoning": str(content)[:500],
        }
    return None


def is_completion_message(output: list) -> bool:
    """True if an assistant message signals completion ("done"/"complete"/"finished").

    List-form content checks only "done"/"complete" (historical behavior, kept as-is).
    """
    for item in output:
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            lower = content.lower()
            if "done" in lower or "complete" in lower or "finished" in lower:
                return True
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text", "").lower()
                    if "done" in text or "complete" in text:
                        return True
    return False


def has_assistant_final(output: list) -> bool:
    """True if any output item is an assistant message."""
    return any(isinstance(item, dict) and item.get("role") == "assistant" for item in reversed(output))


def contains_text(item: dict, token: str) -> bool:
    """True if ``token`` appears in the item's text content (string or list of text parts)."""
    content = item.get("content", "")
    if isinstance(content, str):
        return token in content
    if isinstance(content, list):
        return any(isinstance(p, dict) and token in p.get("text", "") for p in content)
    return False
