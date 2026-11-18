"""resolve_service(): each precedence layer, legacy persona values, and the full stack.

Precedence (highest first), per ADR-0002:
  1. env SOREN_CHROME_CROP_Y (chrome_height_px only; debug override)
  2. settings.yaml services.<name>
  3. persona task_source fields that were explicitly set
  4. config/services/<name>.yaml
  5. settings.yaml top-level defaults (enter_key_behavior)
  6. built-in defaults
"""

from __future__ import annotations

from pathlib import Path

import pytest

from soren.models.service import ServiceConfig, ServiceConfigError
from soren.models.task_source import TaskSource
from soren.services.config_loader import SorenSettings
from soren.services.service_resolution import resolve_service
from tests.services.service_resolution_helpers import ACME_FILE, NO_ENV, make_registry, make_settings


class TestBuiltinDefaults:
    def test_nothing_configured_yields_defaults(self, tmp_path: Path) -> None:
        r = resolve_service("unknown", registry=make_registry(tmp_path), settings=make_settings(), environ=NO_ENV)
        assert r.name == "unknown"
        assert (r.type, r.url, r.discovery_hint, r.notes) == ("survey_platform", "", None, None)
        c = r.config
        assert (c.chrome_height_px, c.enter_key_behavior, c.step_budget) == (0, "enter_only", 50)
        assert (c.typing_delay_ms, c.action_delay_ms, c.cooldown_between_tasks_s) == (50, 500, 30)
        assert (c.required_apps, c.keyboard_shortcuts, c.instructions) == ([], [], [])
        assert set(r.provenance.values()) == {"default"}
        assert r.service_file is None


class TestLegacyPersonaOnly:
    def test_persona_values_used_without_service_file(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate(
            {
                "name": "acmepanel",
                "type": "generic_form",
                "url": "https://persona.example",
                "discovery_hint": "persona hint",
                "typing_delay_ms": 80,
            }
        )
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path),
            settings=make_settings(),
            source=source,
            persona_id="p1",
            environ=NO_ENV,
        )
        assert (r.type, r.url, r.discovery_hint, r.config.typing_delay_ms) == (
            "generic_form",
            "https://persona.example",
            "persona hint",
            80,
        )
        assert r.source_of("url") == "persona:p1"
        assert r.source_of("typing_delay_ms") == "persona:p1"
        assert r.source_of("action_delay_ms") == "default"

    def test_persona_defaults_do_not_override_service_file(self, tmp_path: Path) -> None:
        """TaskSource defaults (typing 50, type survey_platform) are not 'explicit' and must not win."""
        source = TaskSource.model_validate({"name": "acmepanel", "credential_ref": "acmepanel"})
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(),
            source=source,
            environ=NO_ENV,
        )
        assert r.config.typing_delay_ms == 70
        assert r.url == "https://file.example"
        assert r.source_of("typing_delay_ms").startswith("service_file:")

    def test_persona_explicit_empty_strings_are_unset(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate({"name": "acmepanel", "url": "", "discovery_hint": ""})
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(),
            source=source,
            environ=NO_ENV,
        )
        assert r.url == "https://file.example"
        assert r.discovery_hint == "from file"

    def test_persona_label_without_id(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate({"name": "x", "url": "https://p.example"})
        r = resolve_service(
            "x", registry=make_registry(tmp_path), settings=make_settings(), source=source, environ=NO_ENV
        )
        assert r.source_of("url") == "persona"


class TestServiceFile:
    def test_service_file_values_used(self, tmp_path: Path) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(),
            environ=NO_ENV,
        )
        assert r.url == "https://file.example"
        assert r.notes == "file notes"
        assert r.config.chrome_height_px == 100
        assert r.config.step_budget == 40
        assert [s.keys for s in r.config.keyboard_shortcuts] == [["w"], ["m"]]
        assert r.service_file == tmp_path / "services" / "acmepanel.yaml"
        assert r.source_of("chrome_height_px") == f"service_file:{r.service_file}"

    def test_service_file_beats_settings_top_level_default(self, tmp_path: Path) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(enter_key_behavior="enter_only"),
            environ=NO_ENV,
        )
        assert r.config.enter_key_behavior == "escape_enter"

    def test_settings_top_level_fills_gap(self, tmp_path: Path) -> None:
        r = resolve_service(
            "gcal",
            registry=make_registry(tmp_path, {"gcal.yaml": "config:\n  chrome_height_px: 90\n"}),
            settings=make_settings(enter_key_behavior="escape_enter"),
            environ=NO_ENV,
        )
        assert r.config.enter_key_behavior == "escape_enter"
        assert r.source_of("enter_key_behavior") == "settings"

    def test_invalid_service_file_fails_loudly(self, tmp_path: Path) -> None:
        with pytest.raises(ServiceConfigError):
            resolve_service(
                "bad",
                registry=make_registry(tmp_path, {"bad.yaml": "config:\n  step_budget: 0\n"}),
                settings=make_settings(),
                environ=NO_ENV,
            )


class TestSettingsServicesOverride:
    def test_settings_services_beats_service_file(self, tmp_path: Path) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(
                services={"acmepanel": {"enter_key_behavior": "enter_only", "chrome_height_px": 80}}
            ),
            environ=NO_ENV,
        )
        assert r.config.enter_key_behavior == "enter_only"
        assert r.config.chrome_height_px == 80
        assert r.source_of("chrome_height_px") == "settings.services.acmepanel"
        assert r.config.step_budget == 40  # untouched fields still come from the file

    def test_lists_are_replaced_not_merged(self, tmp_path: Path) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(
                services={"acmepanel": {"keyboard_shortcuts": [{"keys": "d", "action": "Day view"}]}}
            ),
            environ=NO_ENV,
        )
        assert [s.keys for s in r.config.keyboard_shortcuts] == [["d"]]
        assert r.config.instructions == ["file instruction"]

    def test_explicit_empty_list_clears_lower_layer(self, tmp_path: Path) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(services={"acmepanel": {"keyboard_shortcuts": []}}),
            environ=NO_ENV,
        )
        assert r.config.keyboard_shortcuts == []
        assert r.source_of("keyboard_shortcuts") == "settings.services.acmepanel"


class TestFullPrecedenceStack:
    @pytest.mark.parametrize(
        ("env", "settings_services", "file_value", "expected", "expected_source"),
        [
            ("15", 25, 45, 15, "env:SOREN_CHROME_CROP_Y"),
            (None, 25, 45, 25, "settings.services.svc"),
            (None, None, 45, 45, "service_file"),
            (None, None, None, 0, "default"),
        ],
    )
    def test_chrome_height_precedence(
        self,
        tmp_path: Path,
        env: str | None,
        settings_services: int | None,
        file_value: int | None,
        expected: int,
        expected_source: str,
    ) -> None:
        """chrome_height_px has no persona/TaskSource or top-level layer; the rest of the stack applies."""
        files = {"svc.yaml": f"config:\n  chrome_height_px: {file_value}\n"} if file_value is not None else {}
        services = {"svc": {"chrome_height_px": settings_services}} if settings_services is not None else {}
        environ = {"SOREN_CHROME_CROP_Y": env} if env is not None else {}
        r = resolve_service(
            "svc", registry=make_registry(tmp_path, files), settings=make_settings(services), environ=environ
        )
        assert r.config.chrome_height_px == expected
        assert r.source_of("chrome_height_px").startswith(expected_source)

    @pytest.mark.parametrize(
        ("settings_services", "persona", "file_value", "expected", "expected_source"),
        [
            (25, 35, 45, 25, "settings.services.svc"),
            (None, 35, 45, 35, "persona:p1"),
            (None, None, 45, 45, "service_file"),
            (None, None, None, 50, "default"),
        ],
    )
    def test_typing_delay_precedence(
        self,
        tmp_path: Path,
        settings_services: int | None,
        persona: int | None,
        file_value: int | None,
        expected: int,
        expected_source: str,
    ) -> None:
        files = {"svc.yaml": f"config:\n  typing_delay_ms: {file_value}\n"} if file_value is not None else {}
        services = {"svc": {"typing_delay_ms": settings_services}} if settings_services is not None else {}
        source_data: dict = {"name": "svc"}
        if persona is not None:
            source_data["typing_delay_ms"] = persona
        r = resolve_service(
            "svc",
            registry=make_registry(tmp_path, files),
            settings=make_settings(services),
            source=TaskSource.model_validate(source_data),
            persona_id="p1",
            environ=NO_ENV,
        )
        assert r.config.typing_delay_ms == expected
        assert r.source_of("typing_delay_ms").startswith(expected_source)

    @pytest.mark.parametrize(
        ("settings_services", "file_value", "top_level", "expected", "expected_source"),
        [
            ("enter_only", "escape_enter", "escape_enter", "enter_only", "settings.services.svc"),
            (None, "escape_enter", "enter_only", "escape_enter", "service_file"),
            (None, None, "escape_enter", "escape_enter", "settings"),
            (None, None, None, "enter_only", "default"),
        ],
    )
    def test_enter_key_precedence(
        self,
        tmp_path: Path,
        settings_services: str | None,
        file_value: str | None,
        top_level: str | None,
        expected: str,
        expected_source: str,
    ) -> None:
        files = {"svc.yaml": f"config:\n  enter_key_behavior: {file_value}\n"} if file_value else {}
        services = {"svc": {"enter_key_behavior": settings_services}} if settings_services else {}
        r = resolve_service(
            "svc",
            registry=make_registry(tmp_path, files),
            settings=make_settings(services, enter_key_behavior=top_level),
            environ=NO_ENV,
        )
        assert r.config.enter_key_behavior == expected
        assert r.source_of("enter_key_behavior").startswith(expected_source)


class TestServiceDefaultsLayer:
    """Layer 5 = settings.yaml `service_defaults` (then the legacy top-level enter_key_behavior)."""

    def _settings(self, service_defaults: dict, **kwargs) -> SorenSettings:
        return SorenSettings.model_validate(
            {"service_defaults": ServiceConfig.model_validate(service_defaults), **kwargs}
        )

    def test_fills_gaps_below_service_file(self, tmp_path: Path) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=self._settings({"step_budget": 10, "action_delay_ms": 250}),
            environ=NO_ENV,
        )
        assert r.config.step_budget == 40  # service file wins
        assert r.config.action_delay_ms == 250
        assert r.source_of("action_delay_ms") == "settings.service_defaults"

    def test_beats_legacy_top_level_enter_key(self, tmp_path: Path) -> None:
        r = resolve_service(
            "x",
            registry=make_registry(tmp_path),
            settings=self._settings({"enter_key_behavior": "escape_enter"}, enter_key_behavior="enter_only"),
            environ=NO_ENV,
        )
        assert r.config.enter_key_behavior == "escape_enter"
        assert r.source_of("enter_key_behavior") == "settings.service_defaults"
