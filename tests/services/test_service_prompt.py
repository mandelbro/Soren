"""Tests for rendering service guidance (instructions, shortcuts, optional notes) into agent instructions."""

from __future__ import annotations

from pathlib import Path

from soren.models.persona import Persona
from soren.models.task import Task
from soren.services.persona_context import PersonaContext
from soren.services.service_prompt import render_service_guidance
from tests.service_helpers import make_resolved_service

GCAL_CONFIG = {
    "keyboard_shortcuts": [
        {"keys": "w", "action": "Switch to week view", "when": "no text field is focused"},
        {"keys": "cmd+k", "action": "Open quick search"},
    ],
    "instructions": ["The Create button is in the top-left.", "Events open in a side panel."],
}


class TestRenderServiceGuidance:
    def test_nothing_to_render_returns_none(self, tmp_path: Path) -> None:
        assert render_service_guidance(make_resolved_service(tmp_path)) is None

    def test_shortcuts_use_ui_tars_hotkey_syntax(self, tmp_path: Path) -> None:
        text = render_service_guidance(make_resolved_service(tmp_path, "gcal", config=GCAL_CONFIG))
        assert text is not None
        assert "hotkey(key='w') — Switch to week view (only when no text field is focused)" in text
        assert "hotkey(key='cmd k') — Open quick search" in text

    def test_instructions_are_bulleted(self, tmp_path: Path) -> None:
        text = render_service_guidance(make_resolved_service(tmp_path, "gcal", config=GCAL_CONFIG))
        assert text is not None
        assert "- The Create button is in the top-left." in text
        assert "- Events open in a side panel." in text

    def test_header_names_the_service(self, tmp_path: Path) -> None:
        text = render_service_guidance(make_resolved_service(tmp_path, "gcal", config=GCAL_CONFIG))
        assert text is not None and text.startswith("Service guidance for gcal:")

    def test_notes_excluded_by_default(self, tmp_path: Path) -> None:
        svc = make_resolved_service(tmp_path, "gcal", config=GCAL_CONFIG, notes="Secret sauce notes")
        text = render_service_guidance(svc)
        assert text is not None and "Secret sauce notes" not in text

    def test_notes_included_when_enabled(self, tmp_path: Path) -> None:
        svc = make_resolved_service(
            tmp_path,
            "gcal",
            config={**GCAL_CONFIG, "include_notes_in_prompt": True},
            notes="Login opens a modal.\nEmail first.\n",
        )
        text = render_service_guidance(svc)
        assert text is not None
        assert "Notes:\nLogin opens a modal.\nEmail first." in text

    def test_notes_only_service_renders_when_enabled(self, tmp_path: Path) -> None:
        svc = make_resolved_service(tmp_path, config={"include_notes_in_prompt": True}, notes="Only notes")
        text = render_service_guidance(svc)
        assert text is not None and "Only notes" in text

    def test_enabled_without_notes_renders_nothing(self, tmp_path: Path) -> None:
        svc = make_resolved_service(tmp_path, config={"include_notes_in_prompt": True})
        assert render_service_guidance(svc) is None

    def test_none_service_renders_none(self) -> None:
        assert render_service_guidance(None) is None


class TestPersonaContextServiceSection:
    def _task(self) -> Task:
        return Task(id="t", dedup_key="k", description="Book a meeting", source_name="gcal")

    def test_section_absent_when_none(self) -> None:
        persona = Persona(name="P")
        assert PersonaContext.build(persona, self._task(), service_guidance=None) == PersonaContext.build(
            persona, self._task()
        )

    def test_section_between_task_context_and_playbook(self) -> None:
        out = PersonaContext.build(
            Persona(name="P"),
            self._task(),
            playbook_content="## Site-Specific Instructions\n1. Click",
            service_guidance="Service guidance for gcal:\n- tip",
        )
        assert out.index("Task context:") < out.index("Service guidance for gcal:") < out.index("Playbook instructions")

    def test_section_never_truncated(self) -> None:
        guidance = "Service guidance for gcal:\n" + "\n".join(f"- tip {i}" for i in range(200))
        out = PersonaContext.build(
            Persona(name="P"),
            self._task(),
            playbook_content="## Site-Specific Instructions\n" + ("x " * 5000),
            service_guidance=guidance,
            max_instruction_tokens=50,
        )
        assert guidance in out
