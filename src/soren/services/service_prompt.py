"""Render a resolved service's agent-facing guidance for the ComputerAgent instructions."""

from __future__ import annotations

from soren.models.service import KeyboardShortcut
from soren.services.service_resolution import ResolvedService

_SHORTCUTS_HEADER = "Keyboard shortcuts (use these instead of clicking through menus when they save steps):"


def format_shortcut(shortcut: KeyboardShortcut) -> str:
    """One shortcut in UI-TARS action syntax, e.g. ``hotkey(key='cmd k') — Open search``.

    UI-TARS emits ``hotkey(key='...')`` with space-separated key names, which
    the CUA loop splits into a keypress list, so the hint matches exactly
    what the model has to output.
    """
    line = f"hotkey(key='{' '.join(shortcut.keys)}') — {shortcut.action}"
    if shortcut.when:
        line += f" (only when {shortcut.when})"
    return line


def render_service_guidance(service: ResolvedService | None) -> str | None:
    """Instructions, keyboard shortcuts, and (when enabled) notes; None if there is nothing to say."""
    if service is None:
        return None
    cfg = service.config
    blocks: list[str] = []
    if cfg.instructions:
        blocks.append("Instructions:\n" + "\n".join(f"- {i}" for i in cfg.instructions))
    if cfg.keyboard_shortcuts:
        blocks.append(_SHORTCUTS_HEADER + "\n" + "\n".join(f"- {format_shortcut(s)}" for s in cfg.keyboard_shortcuts))
    if cfg.include_notes_in_prompt and service.notes and service.notes.strip():
        blocks.append("Notes:\n" + service.notes.strip())
    if not blocks:
        return None
    return f"Service guidance for {service.name}:\n" + "\n".join(blocks)
