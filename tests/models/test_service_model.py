"""Tests for the Service / ServiceConfig / KeyboardShortcut models (config/services/*.yaml schema)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from soren.models.service import (
    SERVICE_SCHEMA_VERSION,
    KeyboardShortcut,
    Service,
    ServiceConfig,
)

FULL_SERVICE = {
    "version": 1,
    "name": "acmepanel",
    "type": "survey_platform",
    "url": "https://acmepanel.example/home",
    "discovery_hint": "Look for available surveys",
    "notes": "Login opens a modal.\nEmail first, then password.\n",
    "config": {
        "chrome_height_px": 100,
        "enter_key_behavior": "escape_enter",
        "step_budget": 40,
        "typing_delay_ms": 60,
        "action_delay_ms": 400,
        "cooldown_between_tasks_s": 20,
        "required_apps": ["Safari"],
        "keyboard_shortcuts": [
            {"keys": "w", "action": "Switch to week view", "when": "no text field is focused"},
            {"keys": ["cmd", "k"], "action": "Open quick search"},
        ],
        "instructions": ["The Log in button is in the top-right nav bar."],
    },
}


class TestServiceMinimal:
    def test_name_only_is_valid(self) -> None:
        svc = Service.model_validate({"name": "acmepanel"})
        assert svc.name == "acmepanel"
        assert svc.version == SERVICE_SCHEMA_VERSION
        assert svc.type is None
        assert svc.url is None
        assert svc.notes is None

    def test_all_config_fields_unset_by_default(self) -> None:
        """Unset fields must stay None so they never mask a lower-precedence layer."""
        cfg = Service.model_validate({"name": "x"}).config
        for field_name in ServiceConfig.model_fields:
            assert getattr(cfg, field_name) is None, field_name
        assert cfg.set_values() == {}

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Service.model_validate({"name": "  "})


class TestServiceFull:
    def test_full_example_validates(self) -> None:
        svc = Service.model_validate(FULL_SERVICE)
        assert svc.config.chrome_height_px == 100
        assert svc.config.enter_key_behavior == "escape_enter"
        assert svc.config.step_budget == 40
        assert svc.config.required_apps == ["Safari"]
        assert [s.keys for s in svc.config.keyboard_shortcuts or []] == [["w"], ["cmd", "k"]]
        assert svc.notes is not None and "Email first" in svc.notes

    def test_round_trip(self) -> None:
        svc = Service.model_validate(FULL_SERVICE)
        again = Service.model_validate(svc.model_dump(exclude_none=True))
        assert again == svc

    def test_set_values_reports_only_explicit_non_null_fields(self) -> None:
        cfg = ServiceConfig.model_validate({"chrome_height_px": 0, "step_budget": None})
        # 0 is an explicit value (disable crop); explicit null means "unset".
        assert cfg.set_values() == {"chrome_height_px": 0}


class TestServiceConfigValidation:
    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("chrome_height_px", -1),
            ("step_budget", 0),
            ("typing_delay_ms", -5),
            ("action_delay_ms", -1),
            ("cooldown_between_tasks_s", -1),
            ("enter_key_behavior", "shift_enter"),
        ],
    )
    def test_invalid_values_rejected(self, field_name: str, value: object) -> None:
        with pytest.raises(ValidationError):
            ServiceConfig.model_validate({field_name: value})

    def test_unknown_config_key_rejected_and_named(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Service.model_validate({"name": "x", "config": {"chrome_crop": 100}})
        assert "chrome_crop" in str(exc.value)

    def test_unknown_top_level_key_rejected_and_named(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Service.model_validate({"name": "x", "chrome_height_px": 100})
        assert "chrome_height_px" in str(exc.value)

    def test_blank_required_app_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ServiceConfig.model_validate({"required_apps": ["Safari", " "]})

    def test_blank_instruction_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ServiceConfig.model_validate({"instructions": [""]})


class TestServiceVersion:
    def test_missing_version_defaults_to_current(self) -> None:
        assert Service.model_validate({"name": "x"}).version == SERVICE_SCHEMA_VERSION

    def test_newer_version_rejected_with_upgrade_hint(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Service.model_validate({"name": "x", "version": SERVICE_SCHEMA_VERSION + 1})
        assert "Upgrade Soren" in str(exc.value)

    def test_version_below_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Service.model_validate({"name": "x", "version": 0})


class TestKeyboardShortcut:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("cmd+k", ["cmd", "k"]),
            (" Cmd + K ", ["cmd", "k"]),
            (["cmd", "k"], ["cmd", "k"]),
            (["CMD", " K "], ["cmd", "k"]),
            ("w", ["w"]),
            ("shift+/", ["shift", "/"]),
            # The "+" key itself can only be expressed in list form.
            (["cmd", "+"], ["cmd", "+"]),
        ],
    )
    def test_keys_normalized(self, raw: object, expected: list[str]) -> None:
        assert KeyboardShortcut.model_validate({"keys": raw, "action": "do it"}).keys == expected

    @pytest.mark.parametrize(
        "raw", ["", "   ", "cmd++k", "cmd+", "+k", [], ["cmd", ""], ["cmd+k"], 5, ["cmd", 1], {"cmd": "k"}]
    )
    def test_malformed_keys_rejected(self, raw: object) -> None:
        with pytest.raises(ValidationError):
            KeyboardShortcut.model_validate({"keys": raw, "action": "do it"})

    @pytest.mark.parametrize("action", ["", "   "])
    def test_action_required(self, action: str) -> None:
        with pytest.raises(ValidationError):
            KeyboardShortcut.model_validate({"keys": "w", "action": action})

    def test_missing_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeyboardShortcut.model_validate({"keys": "w"})

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeyboardShortcut.model_validate({"keys": "w", "action": "week", "desc": "typo"})


class TestChromeHeightBounds:
    @pytest.mark.parametrize("value", [0, 100, 500])
    def test_accepts_values_up_to_the_cap(self, value: int) -> None:
        assert ServiceConfig(chrome_height_px=value).chrome_height_px == value

    @pytest.mark.parametrize("value", [-1, 501, 10_000])
    def test_rejects_negative_or_implausibly_tall_chrome(self, value: int) -> None:
        """Over 500px is a typo on every supported resolution (Safari chrome is ~100px at 1024x768)."""
        with pytest.raises(ValidationError):
            ServiceConfig(chrome_height_px=value)
