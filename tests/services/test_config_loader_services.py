"""Tests for settings.yaml parsing of the legacy `services:` block and top-level `enter_key_behavior`.

These keys were previously ignored by load_settings() and read ad hoc by
soren.patches.enter_key_config. Parsing must stay lenient: anything the old
reader tolerated (unknown keys, invalid values) must not start raising.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from soren.services.config_loader import load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGGER = "soren.services.config_loader"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestShippedSettings:
    def test_shipped_settings_load_with_empty_services(self) -> None:
        # The shipped file has `services:` with only commented-out children -> YAML null.
        settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
        assert settings.services == {}
        # Phase 5: the shipped default moved from the deprecated top-level key to service_defaults.
        assert settings.enter_key_behavior is None
        assert settings.service_defaults.enter_key_behavior == "enter_only"

    def test_shipped_settings_effective_enter_key_unchanged(self, tmp_path: Path) -> None:
        from soren.services.service_registry import ServiceRegistry
        from soren.services.service_resolution import resolve_service

        settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
        resolved = resolve_service("any", registry=ServiceRegistry(tmp_path), settings=settings, environ={})
        assert resolved.config.enter_key_behavior == "enter_only"

    def test_existing_sections_unaffected(self) -> None:
        settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
        assert settings.vm.name == "soren-vm"
        assert settings.playbooks.base_dir == "data/playbooks"


class TestServicesBlock:
    def test_per_service_enter_key_parsed(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "services:\n  slack:\n    enter_key_behavior: escape_enter\n")
        settings = load_settings(path)
        assert settings.services["slack"].enter_key_behavior == "escape_enter"
        assert settings.services["slack"].set_values() == {"enter_key_behavior": "escape_enter"}

    def test_new_service_config_fields_accepted(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "services:\n  gcal:\n    chrome_height_px: 90\n    keyboard_shortcuts:\n      - {keys: w, action: Week view}\n",
        )
        cfg = load_settings(path).services["gcal"]
        assert cfg.chrome_height_px == 90
        assert [s.keys for s in cfg.keyboard_shortcuts or []] == [["w"]]

    def test_unknown_key_ignored_with_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _write(tmp_path, "services:\n  slack:\n    enter_key_behavior: escape_enter\n    bogus: 1\n")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            settings = load_settings(path)
        assert settings.services["slack"].enter_key_behavior == "escape_enter"
        assert any("bogus" in r.getMessage() and "slack" in r.getMessage() for r in caplog.records)

    def test_invalid_value_ignored_with_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _write(tmp_path, "services:\n  slack:\n    enter_key_behavior: shift_enter\n    chrome_height_px: 80\n")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            settings = load_settings(path)
        cfg = settings.services["slack"]
        assert cfg.enter_key_behavior is None  # treated as unset, as the legacy reader did
        assert cfg.chrome_height_px == 80  # valid siblings still applied
        assert any("enter_key_behavior" in r.getMessage() for r in caplog.records)

    def test_non_mapping_entry_ignored_with_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _write(tmp_path, "services:\n  slack: true\n  gcal:\n    chrome_height_px: 90\n")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            settings = load_settings(path)
        assert "slack" not in settings.services
        assert settings.services["gcal"].chrome_height_px == 90
        assert any("slack" in r.getMessage() for r in caplog.records)

    def test_non_mapping_services_block_ignored(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _write(tmp_path, "services: [slack]\n")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            settings = load_settings(path)
        assert settings.services == {}
        assert caplog.records

    def test_null_service_entry_treated_as_empty(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "services:\n  slack:\n")
        assert load_settings(path).services["slack"].set_values() == {}


class TestTopLevelEnterKey:
    def test_absent_keys_default_to_unset(self, tmp_path: Path) -> None:
        settings = load_settings(_write(tmp_path, "vm:\n  name: x\n"))
        assert settings.services == {}
        assert settings.enter_key_behavior is None

    def test_valid_top_level_value(self, tmp_path: Path) -> None:
        assert (
            load_settings(_write(tmp_path, "enter_key_behavior: escape_enter\n")).enter_key_behavior == "escape_enter"
        )

    def test_invalid_top_level_value_ignored_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            settings = load_settings(_write(tmp_path, "enter_key_behavior: sideways\n"))
        assert settings.enter_key_behavior is None
        assert any("enter_key_behavior" in r.getMessage() for r in caplog.records)

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        settings = load_settings(tmp_path / "nope.yaml")
        assert settings.services == {}
        assert settings.enter_key_behavior is None


class TestServiceDefaultsBlock:
    """settings.yaml `service_defaults:` -- global defaults for every service (ServiceConfig shape)."""

    def test_absent_means_empty(self, tmp_path: Path) -> None:
        assert load_settings(_write(tmp_path, "vm:\n  name: x\n")).service_defaults.set_values() == {}

    def test_parsed(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "service_defaults:\n  include_notes_in_prompt: true\n  step_budget: 30\n")
        defaults = load_settings(path).service_defaults
        assert defaults.set_values() == {"include_notes_in_prompt": True, "step_budget": 30}

    def test_lenient_like_services_block(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _write(tmp_path, "service_defaults:\n  step_budget: 0\n  bogus: 1\n  chrome_height_px: 90\n")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            defaults = load_settings(path).service_defaults
        assert defaults.set_values() == {"chrome_height_px": 90}
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "bogus" in messages and "step_budget" in messages

    def test_non_mapping_ignored(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            defaults = load_settings(_write(tmp_path, "service_defaults: true\n")).service_defaults
        assert defaults.set_values() == {}
        assert caplog.records


class TestSectionParsing:
    """Characterization of load_settings() section handling (pinned before refactoring it)."""

    def test_every_section_parsed(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "vm:\n  name: vm-x\nllm:\n  primary_model: m1\nvlm:\n  model: v1\n"
            "scheduler:\n  max_task_retries: 5\nverification:\n  model: q1\nplaybooks:\n  base_dir: pb\n",
        )
        s = load_settings(path)
        assert (s.vm.name, s.llm.primary_model, s.vlm.model) == ("vm-x", "m1", "v1")
        assert (s.scheduler.max_task_retries, s.verification.model, s.playbooks.base_dir) == (5, "q1", "pb")

    @pytest.mark.parametrize("text", ["vm: {}\n", "vm:\n", "llm: null\n", ""])
    def test_empty_or_null_sections_use_defaults(self, tmp_path: Path, text: str) -> None:
        from soren.services.config_loader import SorenSettings

        assert load_settings(_write(tmp_path, text)) == SorenSettings()

    def test_invalid_section_value_raises(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            load_settings(_write(tmp_path, "vm:\n  memory_gb: 1\n"))
