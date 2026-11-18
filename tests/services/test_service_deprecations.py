"""Deprecation notices for legacy service configuration (Phase 5)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from soren.models.persona import Persona
from soren.models.service import ServiceConfig
from soren.models.task_source import TaskSource
from soren.services import service_deprecations
from soren.services.config_loader import SorenSettings, load_settings
from soren.services.service_context import current_chrome_height_px, use_service
from soren.services.service_deprecations import collect_notices, warn_once
from soren.services.service_registry import ServiceRegistry
from tests.service_helpers import make_resolved_service, write_service

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_warn_once():
    service_deprecations.reset_warn_once()
    yield
    service_deprecations.reset_warn_once()


def _notices(tmp_path: Path, *, settings=None, personas=(), environ=None):
    return collect_notices(
        settings or SorenSettings(), list(personas), ServiceRegistry(tmp_path / "services"), environ or {}
    )


class TestCollectNotices:
    def test_clean_configuration_has_no_notices(self, tmp_path: Path) -> None:
        assert _notices(tmp_path) == []

    def test_env_crop_override(self, tmp_path: Path) -> None:
        [notice] = _notices(tmp_path, environ={"SOREN_CHROME_CROP_Y": "100"})
        assert notice.level == "deprecated"
        assert "SOREN_CHROME_CROP_Y=100" in notice.message and "chrome_height_px" in notice.message

    def test_malformed_env_ignored(self, tmp_path: Path) -> None:
        assert _notices(tmp_path, environ={"SOREN_CHROME_CROP_Y": "abc"}) == []

    def test_top_level_enter_key_behavior(self, tmp_path: Path) -> None:
        [notice] = _notices(tmp_path, settings=SorenSettings(enter_key_behavior="escape_enter"))
        assert notice.level == "deprecated"
        assert "service_defaults" in notice.message

    def test_persona_repeating_service_file_value(self, tmp_path: Path) -> None:
        write_service(tmp_path / "services", "acmepanel", url="https://acme.example", config={"typing_delay_ms": 80})
        persona = Persona(
            name="A",
            id="a",
            task_sources=[TaskSource(name="acmepanel", url="https://acme.example", typing_delay_ms=90)],
        )
        [notice] = _notices(tmp_path, personas=[persona])
        assert notice.level == "info"
        assert "persona 'a'" in notice.message and "url" in notice.message and "typing_delay_ms" not in notice.message

    def test_persona_without_service_file_gets_migration_hint(self, tmp_path: Path) -> None:
        persona = Persona(name="A", id="a", task_sources=[TaskSource(name="gcal", url="https://cal.example")])
        [notice] = _notices(tmp_path, personas=[persona])
        assert notice.level == "info"
        assert "soren services migrate" in notice.message

    def test_shipped_configuration_is_clean(self) -> None:
        settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
        notices = collect_notices(settings, [], ServiceRegistry(REPO_ROOT / "config" / "services"), {})
        assert notices == []
        assert settings.service_defaults.enter_key_behavior == "enter_only"


class TestWarnOnce:
    def test_logs_once_per_key(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="soren.services.service_deprecations"):
            warn_once("k", "first")
            warn_once("k", "second")
            warn_once("other", "third")
        assert [r.getMessage() for r in caplog.records] == ["first", "third"]


class TestRuntimeWarnings:
    LOGGER = "soren.services.service_deprecations"

    def test_top_level_enter_key_warns_once_when_loaded(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text("enter_key_behavior: escape_enter\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            load_settings(path)
            load_settings(path)
        assert len([r for r in caplog.records if "enter_key_behavior" in r.getMessage()]) == 1

    def test_legacy_env_crop_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "100")
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            assert current_chrome_height_px() == 100
            assert current_chrome_height_px() == 100
        assert len([r for r in caplog.records if "SOREN_CHROME_CROP_Y" in r.getMessage()]) == 1

    def test_no_env_warning_with_active_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "100")
        svc = make_resolved_service(tmp_path, config={"chrome_height_px": 100})
        with caplog.at_level(logging.WARNING, logger=self.LOGGER), use_service(svc):
            current_chrome_height_px()
        assert caplog.records == []

    def test_service_defaults_enter_key_does_not_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = SorenSettings(service_defaults=ServiceConfig(enter_key_behavior="escape_enter"))
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            assert collect_notices(settings, [], ServiceRegistry(Path("/nonexistent")), {}) == []
        assert caplog.records == []
