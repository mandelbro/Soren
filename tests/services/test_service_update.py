"""ServiceUpdate: changes staged during training, applied to a service definition at /done."""

from __future__ import annotations

import pytest

from soren.models.service import KeyboardShortcut, Service
from soren.services.service_update import ServiceUpdate


def _shortcut(keys: str, action: str, when: str | None = None) -> KeyboardShortcut:
    return KeyboardShortcut.model_validate({"keys": keys, "action": action, "when": when})


class TestStaging:
    def test_empty_by_default(self) -> None:
        update = ServiceUpdate()
        assert update.is_empty
        assert update.describe() == []

    def test_same_keys_replace_staged_shortcut(self) -> None:
        update = ServiceUpdate()
        update.add_shortcut(_shortcut("w", "Week"))
        update.add_shortcut(_shortcut("W", "Week view"))
        assert [(s.keys, s.action) for s in update.shortcuts] == [(["w"], "Week view")]

    def test_duplicate_instructions_dropped(self) -> None:
        update = ServiceUpdate()
        update.add_instruction("Use the sidebar")
        update.add_instruction("  Use the sidebar ")
        assert update.instructions == ["Use the sidebar"]

    @pytest.mark.parametrize("text", ["", "   "])
    def test_blank_text_rejected(self, text: str) -> None:
        update = ServiceUpdate()
        with pytest.raises(ValueError):
            update.add_instruction(text)
        with pytest.raises(ValueError):
            update.add_note(text)

    @pytest.mark.parametrize("brk", ["\r\n", "\r", "\x85", "\u2028", "\u2029"])
    def test_yaml_line_breaks_normalized(self, brk: str) -> None:
        """Pasted text can carry line breaks YAML rewrites on load; normalize so writes round-trip."""
        update = ServiceUpdate()
        update.add_note(f"one{brk}two")
        update.add_instruction(f"a{brk}b")
        assert update.notes == ["one\ntwo"]
        assert update.instructions == ["a\nb"]

    def test_negative_chrome_height_rejected(self) -> None:
        with pytest.raises(ValueError):
            ServiceUpdate().set_chrome_height(-1)

    def test_describe_lists_every_change_with_origin(self) -> None:
        update = ServiceUpdate()
        update.set_chrome_height(90)
        update.set_url("https://calendar.example", origin="auto: training navigation URL")
        update.add_shortcut(_shortcut("w", "Week view", "no text field is focused"))
        update.add_instruction("Use the sidebar")
        update.add_note("Events open in a side panel.")
        assert update.describe() == [
            "url: https://calendar.example (auto: training navigation URL)",
            "config.chrome_height_px: 90 (/crop)",
            "config.keyboard_shortcuts += hotkey(key='w') — Week view (only when no text field is focused)",
            "config.instructions += Use the sidebar",
            "notes += Events open in a side panel.",
        ]

    def test_discard(self) -> None:
        update = ServiceUpdate()
        update.set_chrome_height(90)
        update.add_note("x")
        update.discard()
        assert update.is_empty

    def test_changed_fields(self) -> None:
        update = ServiceUpdate()
        update.set_chrome_height(90)
        update.add_instruction("x")
        assert update.changed_fields() == {"chrome_height_px", "instructions"}


class TestApplyTo:
    def test_new_service_from_seed(self) -> None:
        update = ServiceUpdate()
        update.set_chrome_height(100)
        svc = update.apply_to(None, name="gcal", seed={"type": "generic_form", "url": "https://c.example"})
        assert svc == Service(
            name="gcal", type="generic_form", url="https://c.example", config={"chrome_height_px": 100}
        )

    def test_existing_values_preserved_and_only_staged_keys_changed(self) -> None:
        existing = Service.model_validate(
            {
                "name": "gcal",
                "url": "https://old.example",
                "discovery_hint": "hint",
                "config": {"step_budget": 30, "chrome_height_px": 80, "instructions": ["Old tip"]},
            }
        )
        update = ServiceUpdate()
        update.set_chrome_height(100)
        update.add_instruction("New tip")
        svc = update.apply_to(existing, name="gcal", seed={"url": "https://ignored.example"})
        assert svc.url == "https://old.example"  # seed only fills a new file
        assert svc.discovery_hint == "hint"
        assert svc.config.step_budget == 30
        assert svc.config.chrome_height_px == 100
        assert svc.config.instructions == ["Old tip", "New tip"]
        assert existing.config.chrome_height_px == 80  # input not mutated

    def test_shortcut_with_same_keys_replaces_file_entry(self) -> None:
        existing = Service.model_validate(
            {
                "name": "gcal",
                "config": {"keyboard_shortcuts": [{"keys": "w", "action": "Old"}, {"keys": "m", "action": "Month"}]},
            }
        )
        update = ServiceUpdate()
        update.add_shortcut(_shortcut("w", "Week view"))
        update.add_shortcut(_shortcut("d", "Day view"))
        svc = update.apply_to(existing, name="gcal")
        assert [(s.keys, s.action) for s in svc.config.keyboard_shortcuts or []] == [
            (["w"], "Week view"),
            (["m"], "Month"),
            (["d"], "Day view"),
        ]

    def test_instruction_already_in_file_not_duplicated(self) -> None:
        existing = Service.model_validate({"name": "gcal", "config": {"instructions": ["Tip"]}})
        update = ServiceUpdate()
        update.add_instruction("Tip")
        assert update.apply_to(existing, name="gcal").config.instructions == ["Tip"]

    def test_notes_appended_as_paragraphs(self) -> None:
        existing = Service.model_validate({"name": "gcal", "notes": "First paragraph.\n"})
        update = ServiceUpdate()
        update.add_note("Second.")
        update.add_note("Third.")
        assert update.apply_to(existing, name="gcal").notes == "First paragraph.\n\nSecond.\n\nThird.\n"

    def test_notes_on_service_without_notes(self) -> None:
        update = ServiceUpdate()
        update.add_note("Only.")
        assert update.apply_to(None, name="gcal").notes == "Only.\n"

    def test_url_set_explicitly_overrides_existing(self) -> None:
        existing = Service.model_validate({"name": "gcal", "url": "https://old.example"})
        update = ServiceUpdate()
        update.set_url("https://new.example")
        assert update.apply_to(existing, name="gcal").url == "https://new.example"
