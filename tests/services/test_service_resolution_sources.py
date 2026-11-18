"""resolve_service(): service references, env override, purity/provenance, and the notes toggle."""

from __future__ import annotations

from pathlib import Path

import pytest

from soren.models.service import ServiceConfig, ServiceNotFoundError
from soren.models.task_source import TaskSource
from soren.services.config_loader import SorenSettings
from soren.services.service_resolution import resolve_service
from tests.services.service_resolution_helpers import ACME_FILE, NO_ENV, make_registry, make_settings


class TestServiceReference:
    def test_service_ref_loads_referenced_file(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate({"name": "acme-main", "service": "acmepanel"})
        r = resolve_service(
            source.service_key,
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(),
            source=source,
            environ=NO_ENV,
        )
        assert r.name == "acmepanel"
        assert r.config.chrome_height_px == 100

    def test_settings_services_keyed_by_service_name(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate({"name": "acme-main", "service": "acmepanel"})
        r = resolve_service(
            source.service_key,
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(services={"acmepanel": {"step_budget": 5}, "acme-main": {"step_budget": 7}}),
            source=source,
            environ=NO_ENV,
        )
        assert r.config.step_budget == 5

    def test_settings_services_falls_back_to_legacy_source_name(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate({"name": "acme-main", "service": "acmepanel"})
        r = resolve_service(
            source.service_key,
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(services={"acme-main": {"step_budget": 7}}),
            source=source,
            environ=NO_ENV,
        )
        assert r.config.step_budget == 7
        assert r.source_of("step_budget") == "settings.services.acme-main"

    def test_explicit_reference_to_missing_file_raises(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate({"name": "acme-main", "service": "acmepanel"})
        with pytest.raises(ServiceNotFoundError) as exc:
            resolve_service(
                source.service_key,
                registry=make_registry(tmp_path),
                settings=make_settings(),
                source=source,
                environ=NO_ENV,
            )
        assert "acmepanel" in str(exc.value)

    def test_legacy_source_without_file_is_fine(self, tmp_path: Path) -> None:
        source = TaskSource.model_validate({"name": "acmepanel", "url": "https://p.example"})
        r = resolve_service(
            "acmepanel", registry=make_registry(tmp_path), settings=make_settings(), source=source, environ=NO_ENV
        )
        assert r.url == "https://p.example"
        assert r.service_file is None


class TestEnvOverride:
    @pytest.mark.parametrize(("raw", "expected"), [("100", 100), ("0", 0), ("-5", 0), (" 42 ", 42)])
    def test_valid_env_values(self, tmp_path: Path, raw: str, expected: int) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(),
            environ={"SOREN_CHROME_CROP_Y": raw},
        )
        assert r.config.chrome_height_px == expected
        assert r.source_of("chrome_height_px") == "env:SOREN_CHROME_CROP_Y"

    @pytest.mark.parametrize("raw", ["", "abc", "1.5"])
    def test_malformed_env_treated_as_unset(self, tmp_path: Path, raw: str) -> None:
        r = resolve_service(
            "acmepanel",
            registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}),
            settings=make_settings(),
            environ={"SOREN_CHROME_CROP_Y": raw},
        )
        assert r.config.chrome_height_px == 100
        assert r.source_of("chrome_height_px").startswith("service_file:")

    def test_defaults_to_process_environment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "33")
        r = resolve_service("x", registry=make_registry(tmp_path), settings=make_settings())
        assert r.config.chrome_height_px == 33


class TestPurity:
    def test_does_not_mutate_environment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        import os

        before = dict(os.environ)
        resolve_service(
            "acmepanel", registry=make_registry(tmp_path, {"acmepanel.yaml": ACME_FILE}), settings=make_settings()
        )
        assert dict(os.environ) == before

    def test_does_not_mutate_settings_or_share_lists(self, tmp_path: Path) -> None:
        settings = make_settings(
            services={"svc": {"instructions": ["a"], "keyboard_shortcuts": [{"keys": "w", "action": "W"}]}}
        )
        snapshot = settings.model_dump()
        r = resolve_service("svc", registry=make_registry(tmp_path), settings=settings, environ=NO_ENV)
        r.config.instructions.append("mutated")
        r.config.keyboard_shortcuts[0].action = "mutated"
        r.config.keyboard_shortcuts.clear()
        assert settings.model_dump() == snapshot

    def test_provenance_covers_every_field(self, tmp_path: Path) -> None:
        r = resolve_service("x", registry=make_registry(tmp_path), settings=make_settings(), environ=NO_ENV)
        expected_fields = {"type", "url", "discovery_hint", "notes"} | set(ServiceConfig.model_fields)
        assert set(r.provenance) == expected_fields

    def test_source_of_unknown_field_raises(self, tmp_path: Path) -> None:
        r = resolve_service("x", registry=make_registry(tmp_path), settings=make_settings(), environ=NO_ENV)
        with pytest.raises(KeyError):
            r.source_of("nope")


class TestIncludeNotesToggle:
    def test_default_false(self, tmp_path: Path) -> None:
        r = resolve_service("x", registry=make_registry(tmp_path), settings=make_settings(), environ=NO_ENV)
        assert r.config.include_notes_in_prompt is False

    def test_global_default_then_per_service_override(self, tmp_path: Path) -> None:
        settings = SorenSettings.model_validate(
            {
                "service_defaults": ServiceConfig(include_notes_in_prompt=True),
                "services": {"quiet": ServiceConfig(include_notes_in_prompt=False)},
            }
        )
        registry = make_registry(tmp_path)
        assert resolve_service(
            "loud", registry=registry, settings=settings, environ=NO_ENV
        ).config.include_notes_in_prompt
        assert not resolve_service(
            "quiet", registry=registry, settings=settings, environ=NO_ENV
        ).config.include_notes_in_prompt
