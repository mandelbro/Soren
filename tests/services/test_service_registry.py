"""Tests for ServiceRegistry: loading config/services/*.yaml."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from soren.models.service import ServiceConfigError
from soren.services.naming import safe_service_name
from soren.services.persona_loader import load_persona
from soren.services.service_registry import ServiceRegistry, get_services_dir, load_service_file

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write(directory: Path, filename: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


class TestEmptyOrMissingDirectory:
    def test_missing_directory_is_empty_and_not_created(self, tmp_path: Path) -> None:
        services_dir = tmp_path / "services"
        registry = ServiceRegistry(services_dir)
        assert registry.list() == []
        assert registry.get("acmepanel") is None
        assert registry.validate_all() == []
        assert not services_dir.exists()

    def test_empty_directory(self, tmp_path: Path) -> None:
        (tmp_path / "services").mkdir()
        assert ServiceRegistry(tmp_path / "services").list() == []


class TestGet:
    def test_loads_service_by_name(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "acmepanel.yaml",
            "name: acmepanel\nurl: https://acmepanel.example\nconfig:\n  chrome_height_px: 100\n",
        )
        svc = ServiceRegistry(tmp_path).get("acmepanel")
        assert svc is not None
        assert svc.url == "https://acmepanel.example"
        assert svc.config.chrome_height_px == 100

    def test_name_defaults_to_file_stem(self, tmp_path: Path) -> None:
        _write(tmp_path, "gcal.yaml", "url: https://calendar.example\n")
        svc = ServiceRegistry(tmp_path).get("gcal")
        assert svc is not None and svc.name == "gcal"

    def test_name_mismatch_raises_with_path(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "gcal.yaml", "name: outlook\n")
        with pytest.raises(ServiceConfigError) as exc:
            ServiceRegistry(tmp_path).get("gcal")
        assert str(path) in str(exc.value)
        assert "outlook" in str(exc.value)

    def test_lookup_uses_playbook_filename_sanitization(self, tmp_path: Path) -> None:
        _write(tmp_path, "Acme_Panel.yaml", "name: Acme Panel\n")
        registry = ServiceRegistry(tmp_path)
        assert registry.path_for("Acme Panel") == tmp_path / "Acme_Panel.yaml"
        svc = registry.get("Acme Panel")
        assert svc is not None and svc.name == "Acme Panel"

    def test_missing_service_returns_none(self, tmp_path: Path) -> None:
        _write(tmp_path, "other.yaml", "name: other\n")
        assert ServiceRegistry(tmp_path).get("acmepanel") is None

    @pytest.mark.parametrize(
        "text",
        [
            "name: [unclosed\n",  # YAML syntax error
            "- just\n- a list\n",  # not a mapping
            "",  # empty file
            "name: x\nconfig:\n  chrome_crop: 1\n",  # schema violation (unknown key)
        ],
    )
    def test_invalid_file_raises_with_path(self, tmp_path: Path, text: str) -> None:
        path = _write(tmp_path, "x.yaml", text)
        with pytest.raises(ServiceConfigError) as exc:
            ServiceRegistry(tmp_path).get("x")
        assert str(path) in str(exc.value)

    def test_reads_fresh_on_each_get(self, tmp_path: Path) -> None:
        """No caching: edits (e.g. by training write-back) are visible to the next task."""
        path = _write(tmp_path, "x.yaml", "config:\n  step_budget: 10\n")
        registry = ServiceRegistry(tmp_path)
        assert registry.get("x").config.step_budget == 10  # type: ignore[union-attr]
        path.write_text("config:\n  step_budget: 20\n", encoding="utf-8")
        assert registry.get("x").config.step_budget == 20  # type: ignore[union-attr]


class TestListAndValidate:
    def test_list_skips_invalid_files_and_logs(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        _write(tmp_path, "good.yaml", "name: good\n")
        _write(tmp_path, "bad.yaml", "config:\n  step_budget: 0\n")
        with caplog.at_level(logging.WARNING, logger="soren.services.service_registry"):
            services = ServiceRegistry(tmp_path).list()
        assert [s.name for s in services] == ["good"]
        assert any("bad.yaml" in r.getMessage() for r in caplog.records)

    def test_validate_all_reports_every_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "a_bad.yaml", "config:\n  step_budget: 0\n")
        _write(tmp_path, "b_good.yaml", "name: b_good\n")
        _write(tmp_path, "c_bad.yaml", "name: other\n")
        results = ServiceRegistry(tmp_path).validate_all()
        assert [r.path.name for r in results] == ["a_bad.yaml", "b_good.yaml", "c_bad.yaml"]
        assert [r.ok for r in results] == [False, True, False]
        assert results[1].service is not None and results[1].error is None
        assert results[0].service is None and results[0].error

    def test_only_yaml_extension_is_read(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.yaml", "name: a\n")
        _write(tmp_path, "b.yml", "name: b\n")
        _write(tmp_path, "notes.md", "# notes\n")
        assert [s.name for s in ServiceRegistry(tmp_path).list()] == ["a"]


class TestHelpers:
    def test_load_service_file_direct(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "gcal.yaml", "url: https://calendar.example\n")
        assert load_service_file(path).name == "gcal"

    def test_default_services_dir(self) -> None:
        assert get_services_dir() == REPO_ROOT / "config" / "services"

    def test_safe_service_name_matches_legacy_playbook_rule(self) -> None:
        assert safe_service_name("Acme Panel") == "Acme_Panel"
        assert safe_service_name("a/b:c") == "a_b_c"
        assert safe_service_name("") == "default"
        assert safe_service_name("__") == "default"


class TestShippedExample:
    def test_shipped_example_service_validates(self) -> None:
        svc = ServiceRegistry(REPO_ROOT / "config" / "services").get("acmepanel")
        assert svc is not None
        assert svc.config.chrome_height_px is not None

    def test_shipped_example_matches_example_persona(self) -> None:
        """The example service and example persona describe the same service -- keep them in sync."""
        svc = ServiceRegistry(REPO_ROOT / "config" / "services").get("acmepanel")
        persona = load_persona(REPO_ROOT / "config" / "personas" / "example.yaml")
        [source] = persona.task_sources
        assert svc is not None
        assert svc.url == source.url
        assert svc.type == source.type
