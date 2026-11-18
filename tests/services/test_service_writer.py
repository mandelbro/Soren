"""ServiceWriter: safe PyYAML writes of config/services/<name>.yaml with rotated backups."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from soren.models.service import Service, ServiceConfigError
from soren.services.service_registry import ServiceRegistry, load_service_file
from soren.services.service_writer import MANAGED_HEADER, ServiceWriter


def _service(**kwargs) -> Service:
    return Service.model_validate({"name": "gcal", **kwargs})


@pytest.fixture
def registry(tmp_path: Path) -> ServiceRegistry:
    return ServiceRegistry(tmp_path / "services")


class TestWrite:
    def test_creates_new_file_and_directory(self, registry: ServiceRegistry) -> None:
        result = ServiceWriter(registry).write(_service(url="https://c.example", config={"chrome_height_px": 90}))
        assert result.created is True
        assert result.backup_path is None
        assert result.path == registry.path_for("gcal")
        assert load_service_file(result.path) == _service(url="https://c.example", config={"chrome_height_px": 90})

    def test_key_order_and_no_nulls(self, registry: ServiceRegistry) -> None:
        svc = _service(url="https://c.example", type="generic_form", notes="n", config={"step_budget": 5})
        path = ServiceWriter(registry).write(svc).path
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert list(data) == ["version", "name", "type", "url", "notes", "config"]
        assert data["config"] == {"step_budget": 5}

    def test_empty_config_omitted(self, registry: ServiceRegistry) -> None:
        path = ServiceWriter(registry).write(_service(url="https://c.example")).path
        assert "config" not in yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_multiline_notes_use_literal_block_and_round_trip(self, registry: ServiceRegistry) -> None:
        notes = "Login opens a modal.\n\nEmail first — then password: “quoted”.\n"
        path = ServiceWriter(registry).write(_service(notes=notes)).path
        text = path.read_text(encoding="utf-8")
        assert "notes: |" in text
        assert "“quoted”" in text  # unicode written as-is, not escaped
        assert load_service_file(path).notes == notes

    def test_managed_header_written(self, registry: ServiceRegistry) -> None:
        path = ServiceWriter(registry).write(_service()).path
        assert path.read_text(encoding="utf-8").startswith(MANAGED_HEADER)

    def test_filename_uses_sanitized_name(self, registry: ServiceRegistry) -> None:
        path = ServiceWriter(registry).write(Service(name="Acme Panel")).path
        assert path.name == "Acme_Panel.yaml"
        assert load_service_file(path).name == "Acme Panel"

    def test_invalid_service_leaves_existing_file_untouched(self, registry: ServiceRegistry) -> None:
        writer = ServiceWriter(registry)
        path = writer.write(_service(url="https://c.example")).path
        before = path.read_text(encoding="utf-8")
        bad = _service().model_copy(update={"version": 99})  # bypasses validation, like a buggy caller
        with pytest.raises(ServiceConfigError):
            writer.write(bad)
        assert path.read_text(encoding="utf-8") == before
        assert sorted(p.name for p in path.parent.iterdir()) == ["gcal.yaml"]  # no temp files, no backup

    @pytest.mark.parametrize("text", ["a\x85b", "a\nb\x85c"])
    def test_refuses_values_that_do_not_round_trip(self, registry: ServiceRegistry, text: str) -> None:
        """Found by fuzzing: PyYAML writes NEL (\\x85) raw and reads it back changed.
        Refuse rather than silently alter the notes (ServiceUpdate normalizes user input first)."""
        with pytest.raises(ServiceConfigError, match="round trip"):
            ServiceWriter(registry).write(_service(notes=text))
        assert not registry.path_for("gcal").exists()

    def test_failed_replace_leaves_no_temp_file(self, registry: ServiceRegistry) -> None:
        writer = ServiceWriter(registry)
        with patch("soren.services.service_writer.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                writer.write(_service())
        services_dir = registry.services_dir
        assert [p.name for p in services_dir.iterdir() if p.is_file()] == []


class TestBackups:
    def test_backup_created_before_overwrite(self, registry: ServiceRegistry) -> None:
        writer = ServiceWriter(registry)
        writer.write(_service(url="https://v1.example"))
        result = writer.write(_service(url="https://v2.example"))
        assert result.created is False
        assert result.backup_path is not None
        assert result.backup_path.parent == registry.services_dir / ".backups"
        assert "https://v1.example" in result.backup_path.read_text(encoding="utf-8")
        assert load_service_file(result.path).url == "https://v2.example"

    def test_only_newest_backups_kept_per_service(self, registry: ServiceRegistry) -> None:
        writer = ServiceWriter(registry, keep_backups=3)
        for i in range(6):
            writer.write(_service(url=f"https://v{i}.example"))
        writer.write(Service(name="other"))
        writer.write(Service(name="other", url="https://o.example"))
        backups = sorted((registry.services_dir / ".backups").iterdir())
        gcal = [b for b in backups if b.name.startswith("gcal.")]
        assert len(gcal) == 3
        assert ["https://v2.example" in gcal[0].read_text(), "https://v4.example" in gcal[-1].read_text()] == [
            True,
            True,
        ]
        assert len([b for b in backups if b.name.startswith("other.")]) == 1  # other services unaffected

    def test_keep_zero_disables_backups(self, registry: ServiceRegistry) -> None:
        writer = ServiceWriter(registry, keep_backups=0)
        writer.write(_service())
        assert writer.write(_service(url="https://v2.example")).backup_path is None
        assert not (registry.services_dir / ".backups").exists()

    def test_backups_invisible_to_registry(self, registry: ServiceRegistry) -> None:
        writer = ServiceWriter(registry)
        writer.write(_service())
        writer.write(_service(url="https://v2.example"))
        assert [s.name for s in registry.list()] == ["gcal"]
        assert [r.path.name for r in registry.validate_all()] == ["gcal.yaml"]

    def test_negative_keep_rejected(self, registry: ServiceRegistry) -> None:
        with pytest.raises(ValueError):
            ServiceWriter(registry, keep_backups=-1)


class TestCommentDetection:
    def test_user_comments_detected(self, registry: ServiceRegistry) -> None:
        path = registry.path_for("gcal")
        path.parent.mkdir(parents=True)
        path.write_text("# my notes\nname: gcal\nurl: https://x.example  # trailing\n", encoding="utf-8")
        assert ServiceWriter(registry).has_user_comments(path) is True

    def test_managed_header_alone_is_not_a_user_comment(self, registry: ServiceRegistry) -> None:
        writer = ServiceWriter(registry)
        path = writer.write(_service(notes="a # not a comment inside a block\n")).path
        assert writer.has_user_comments(path) is False

    def test_hash_inside_quoted_value_is_not_a_comment(self, registry: ServiceRegistry) -> None:
        path = registry.path_for("gcal")
        path.parent.mkdir(parents=True)
        path.write_text('name: gcal\nurl: "https://x.example/#anchor"\n', encoding="utf-8")
        assert ServiceWriter(registry).has_user_comments(path) is False

    def test_missing_file_has_no_comments(self, registry: ServiceRegistry) -> None:
        assert ServiceWriter(registry).has_user_comments(registry.path_for("nope")) is False

    def test_shipped_example_has_comments(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        registry = ServiceRegistry(repo / "config" / "services")
        assert ServiceWriter(registry).has_user_comments(registry.path_for("acmepanel")) is True
