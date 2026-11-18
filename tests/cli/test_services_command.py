"""Tests for `soren services list|show|validate` (ADR-0002, Phase 3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from soren.cli import cli
from soren.models.persona import Persona
from soren.models.task_source import TaskSource
from tests.service_helpers import write_service

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner() -> CliRunner:
    # Wide terminal so Rich tables don't wrap values we assert on.
    return CliRunner(env={"COLUMNS": "250", "SOREN_CHROME_CROP_Y": None})


@pytest.fixture
def settings_file(tmp_path: Path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text("vm:\n  name: test-vm\n", encoding="utf-8")
    return path


def _invoke(runner: CliRunner, args: list[str], personas: list[Persona] | None = None):
    with patch("soren.cli.services.load_all", return_value=personas or []):
        return runner.invoke(cli, ["services", *args], catch_exceptions=False)


class TestGroup:
    def test_registered(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["services", "--help"])
        assert result.exit_code == 0
        for sub in ("list", "show", "validate"):
            assert sub in result.output


class TestList:
    def test_empty_directory_message(self, runner: CliRunner, services_dir: Path) -> None:
        result = _invoke(runner, ["list"])
        assert result.exit_code == 0
        assert "No service definitions found" in result.output
        assert "acmepanel.yaml" in result.output  # points at the example

    def test_lists_valid_and_invalid(self, runner: CliRunner, services_dir: Path) -> None:
        write_service(services_dir, "gcal", type="generic_form", url="https://calendar.example")
        (services_dir / "broken.yaml").write_text("config:\n  step_budget: 0\n", encoding="utf-8")
        result = _invoke(runner, ["list"])
        assert result.exit_code == 0
        assert "gcal" in result.output and "https://calendar.example" in result.output
        assert "broken" in result.output and "INVALID" in result.output
        assert "soren services validate" in result.output  # hint when something is invalid

    def test_services_dir_option(self, runner: CliRunner, tmp_path: Path) -> None:
        other = tmp_path / "elsewhere"
        write_service(other, "slack")
        result = _invoke(runner, ["list", "--services-dir", str(other)])
        assert result.exit_code == 0
        assert "slack" in result.output


class TestShow:
    def test_values_and_provenance(self, runner: CliRunner, services_dir: Path, settings_file: Path) -> None:
        write_service(
            services_dir,
            "gcal",
            url="https://calendar.example",
            config={"chrome_height_px": 90, "keyboard_shortcuts": [{"keys": "w", "action": "Week view"}]},
        )
        result = _invoke(runner, ["show", "gcal", "--config", str(settings_file)])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "chrome_height_px" in out and "90" in out
        assert "service_file" in out
        assert "step_budget" in out and "default" in out
        assert "hotkey(key='w') — Week view" in out

    def test_settings_override_shown(self, runner: CliRunner, services_dir: Path, tmp_path: Path) -> None:
        write_service(services_dir, "gcal", config={"chrome_height_px": 90})
        settings = tmp_path / "s.yaml"
        settings.write_text("services:\n  gcal:\n    chrome_height_px: 70\n", encoding="utf-8")
        result = _invoke(runner, ["show", "gcal", "--config", str(settings)])
        assert "70" in result.output and "settings.services.gcal" in result.output

    def test_env_override_marked(self, services_dir: Path, settings_file: Path) -> None:
        write_service(services_dir, "gcal", config={"chrome_height_px": 90})
        runner = CliRunner(env={"COLUMNS": "250", "SOREN_CHROME_CROP_Y": "40"})
        result = _invoke(runner, ["show", "gcal", "--config", str(settings_file)])
        assert "40" in result.output and "env:SOREN_CHROME_CROP_Y" in result.output

    def test_persona_option_uses_persona_source(
        self, runner: CliRunner, services_dir: Path, settings_file: Path
    ) -> None:
        write_service(services_dir, "acmepanel", url="https://file.example")
        personas = [
            Persona(name="A", id="a", task_sources=[TaskSource(name="acmepanel")]),
            Persona(name="B", id="b", task_sources=[TaskSource(name="acmepanel", url="https://persona-b.example")]),
        ]
        result = _invoke(runner, ["show", "acmepanel", "--persona", "b", "--config", str(settings_file)], personas)
        assert result.exit_code == 0, result.output
        assert "https://persona-b.example" in result.output and "persona:b" in result.output

    def test_unknown_persona_exits_1(self, runner: CliRunner, settings_file: Path) -> None:
        result = _invoke(runner, ["show", "acmepanel", "--persona", "nobody", "--config", str(settings_file)])
        assert result.exit_code == 1
        assert "nobody" in result.output

    def test_persona_without_matching_source_exits_1(self, runner: CliRunner, settings_file: Path) -> None:
        personas = [Persona(name="A", id="a", task_sources=[TaskSource(name="other")])]
        result = _invoke(runner, ["show", "acmepanel", "--persona", "a", "--config", str(settings_file)], personas)
        assert result.exit_code == 1
        assert "no task source for 'acmepanel'" in result.output

    def test_no_service_file_shows_values_with_note(self, runner: CliRunner, settings_file: Path) -> None:
        personas = [Persona(name="A", id="a", task_sources=[TaskSource(name="legacy", url="https://legacy.example")])]
        result = _invoke(runner, ["show", "legacy", "--config", str(settings_file)], personas)
        assert result.exit_code == 0, result.output
        assert "No service file" in result.output
        assert "https://legacy.example" in result.output

    def test_invalid_file_exits_1_with_path(self, runner: CliRunner, services_dir: Path, settings_file: Path) -> None:
        (services_dir / "gcal.yaml").write_text("config:\n  step_budget: 0\n", encoding="utf-8")
        result = _invoke(runner, ["show", "gcal", "--config", str(settings_file)])
        assert result.exit_code == 1
        assert "gcal.yaml" in result.output

    def test_prompt_preview(self, runner: CliRunner, services_dir: Path, settings_file: Path) -> None:
        write_service(services_dir, "gcal", config={"instructions": ["Use the sidebar"]}, notes="Hidden notes")
        result = _invoke(runner, ["show", "gcal", "--prompt", "--config", str(settings_file)])
        assert "Service guidance for gcal:" in result.output
        assert "- Use the sidebar" in result.output
        assert "Hidden notes" in result.output  # shown in the table...
        assert result.output.count("Hidden notes") == 1  # ...but not in the prompt preview (toggle off)

    def test_prompt_preview_when_empty(self, runner: CliRunner, services_dir: Path, settings_file: Path) -> None:
        write_service(services_dir, "gcal")
        result = _invoke(runner, ["show", "gcal", "--prompt", "--config", str(settings_file)])
        assert "nothing would be added" in result.output


class TestValidate:
    def test_all_valid_exits_0(self, runner: CliRunner, services_dir: Path) -> None:
        write_service(services_dir, "gcal")
        result = _invoke(runner, ["validate"])
        assert result.exit_code == 0
        assert "gcal.yaml" in result.output and "OK" in result.output

    def test_reports_every_invalid_file(self, runner: CliRunner, services_dir: Path) -> None:
        (services_dir / "a.yaml").write_text("config:\n  step_budget: 0\n", encoding="utf-8")
        (services_dir / "b.yaml").write_text("name: other\n", encoding="utf-8")
        write_service(services_dir, "c")
        result = _invoke(runner, ["validate"])
        assert result.exit_code == 1
        assert "a.yaml" in result.output and "b.yaml" in result.output
        assert result.output.count("INVALID") == 2

    def test_flags_missing_persona_service_reference(self, runner: CliRunner, services_dir: Path) -> None:
        write_service(services_dir, "gcal")
        personas = [
            Persona(
                name="A",
                id="a",
                task_sources=[TaskSource(name="cal", service="gcal"), TaskSource(name="x", service="missing-svc")],
            )
        ]
        result = _invoke(runner, ["validate"], personas)
        assert result.exit_code == 1
        assert "persona 'a'" in result.output and "'missing-svc'" in result.output
        assert "'gcal'" not in result.output  # valid reference not reported

    def test_legacy_sources_without_files_are_fine(self, runner: CliRunner) -> None:
        personas = [Persona(name="A", id="a", task_sources=[TaskSource(name="legacy", url="https://x.example")])]
        result = _invoke(runner, ["validate"], personas)
        assert result.exit_code == 0

    def test_shipped_example_validates(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["validate", "--services-dir", str(REPO_ROOT / "config" / "services")])
        assert result.exit_code == 0, result.output
        assert "acmepanel.yaml" in result.output


class TestJustRecipes:
    @pytest.mark.parametrize(
        ("recipe", "command"),
        [
            ("list-services", "uv run soren services list"),
            ("show-service NAME *ARGS", "uv run soren services show {{NAME}} {{ARGS}}"),
            ("validate-services", "uv run soren services validate"),
        ],
    )
    def test_recipe_defined(self, recipe: str, command: str) -> None:
        justfile = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
        assert f"\n{recipe}:\n    {command}" in justfile
