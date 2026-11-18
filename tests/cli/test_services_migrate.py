"""`soren services migrate` and deprecation notices in `soren services validate` (Phase 5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from soren.cli import cli
from soren.models.persona import Persona
from soren.models.task_source import TaskSource
from soren.services import service_deprecations
from soren.services.service_registry import load_service_file
from tests.service_helpers import write_service

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(env={"COLUMNS": "250", "SOREN_CHROME_CROP_Y": None})


@pytest.fixture(autouse=True)
def _fresh_warn_once():
    service_deprecations.reset_warn_once()


def _invoke(runner: CliRunner, args: list[str], personas: list[Persona]):
    with patch("soren.cli.services.load_all", return_value=personas):
        return runner.invoke(cli, ["services", *args], catch_exceptions=False)


ACME = TaskSource(name="acmepanel", url="https://acme.example", type="survey_platform")


class TestMigrate:
    def test_dry_run_writes_nothing(self, runner: CliRunner, services_dir: Path) -> None:
        result = _invoke(runner, ["migrate", "--dry-run"], [Persona(name="A", id="a", task_sources=[ACME])])
        assert result.exit_code == 0, result.output
        assert "Would create" in result.output and "acmepanel.yaml" in result.output
        assert "a:acmepanel" in result.output
        assert not (services_dir / "acmepanel.yaml").exists()

    def test_creates_files(self, runner: CliRunner, services_dir: Path) -> None:
        result = _invoke(runner, ["migrate"], [Persona(name="A", id="a", task_sources=[ACME])])
        assert result.exit_code == 0, result.output
        assert "Created" in result.output
        assert load_service_file(services_dir / "acmepanel.yaml").url == "https://acme.example"
        assert "still take precedence" in result.output  # persona values are left in place

    def test_reports_existing_conflicts_and_inherits(self, runner: CliRunner, services_dir: Path) -> None:
        write_service(services_dir, "done", url="https://done.example")
        personas = [
            Persona(
                name="A",
                id="a",
                task_sources=[
                    TaskSource(name="done", url="https://done.example"),
                    TaskSource(name="clash", url="https://one.example"),
                    ACME,
                ],
            ),
            Persona(
                name="B",
                id="b",
                task_sources=[TaskSource(name="clash", url="https://two.example"), TaskSource(name="acmepanel")],
            ),
        ]
        result = _invoke(runner, ["migrate"], personas)
        out = result.output
        assert result.exit_code == 0, out
        assert "Already defined" in out and "done" in out
        assert "Conflict" in out and "clash" in out and "https://one.example" in out and "https://two.example" in out
        assert "will now inherit" in out and "b:acmepanel" in out
        assert not (services_dir / "clash.yaml").exists()

    def test_nothing_to_migrate(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["migrate"], [Persona(name="A", id="a", task_sources=[TaskSource(name="x")])])
        assert result.exit_code == 0
        assert "Nothing to migrate" in result.output

    def test_unknown_persona_exits_1(self, runner: CliRunner) -> None:
        result = _invoke(runner, ["migrate", "--persona", "nobody"], [])
        assert result.exit_code == 1
        assert "nobody" in result.output


class TestValidateNotices:
    def test_notices_shown_without_failing(self, services_dir: Path) -> None:
        write_service(services_dir, "acmepanel", url="https://acme.example")
        runner = CliRunner(env={"COLUMNS": "250", "SOREN_CHROME_CROP_Y": "100"})
        personas = [Persona(name="A", id="a", task_sources=[TaskSource(name="acmepanel", url="https://acme.example")])]
        result = _invoke(runner, ["validate"], personas)
        assert result.exit_code == 0, result.output
        assert "DEPRECATED" in result.output and "SOREN_CHROME_CROP_Y=100" in result.output
        assert "INFO" in result.output and "persona 'a'" in result.output


class TestJustRecipe:
    def test_migrate_recipe_defined(self) -> None:
        justfile = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
        assert "\nmigrate-services *ARGS:\n    uv run soren services migrate {{ARGS}}" in justfile
