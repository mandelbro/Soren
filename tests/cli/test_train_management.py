"""Tests for `soren train list/show/delete` CLI commands (Task 017)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from soren.cli import cli
from soren.models.site_playbook import (
    ExecutionFlow,
    LoginFlow,
    PlaybookStep,
    SiteFlows,
    SitePlaybook,
)


@pytest.fixture
def runner():
    return CliRunner()


def _mock_settings():
    settings = MagicMock()
    settings.playbooks.base_dir = "data/playbooks"
    settings.playbooks.site_playbooks_dir = "data/playbooks/site"
    settings.playbooks.host_playbooks_dir = "data/playbooks/host"
    settings.playbooks.base_playbooks_dir = "data/playbooks/base"
    settings.vm = MagicMock()
    settings.llm = MagicMock()
    return settings


def _sample_site_playbook() -> SitePlaybook:
    return SitePlaybook(
        source_name="prolific",
        source_url="https://prolific.com",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        flows=SiteFlows(
            login=LoginFlow(
                steps=[PlaybookStep(seq=1, instruction="Click login", expected_after="Login page")],
                confidence=0.8,
                verified_at=datetime(2026, 3, 15, tzinfo=UTC),
                verified_by="human",
            ),
            execution={
                "survey": ExecutionFlow(
                    steps=[PlaybookStep(seq=1, instruction="Start survey", expected_after="Survey started")],
                    confidence=0.6,
                    verified_at=datetime(2026, 3, 10, tzinfo=UTC),
                    verified_by="auto",
                ),
            },
        ),
    )


class TestTrainList:
    def test_list_with_playbooks(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.list_site_playbooks.return_value = [_sample_site_playbook()]
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "list"])

        assert result.exit_code == 0
        assert "prolific" in result.output

    def test_list_no_playbooks(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.list_site_playbooks.return_value = []
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "list"])

        assert result.exit_code == 0
        assert "No playbooks" in result.output or "no playbooks" in result.output.lower()


class TestTrainShow:
    def test_show_displays_playbook(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.load_site.return_value = _sample_site_playbook()
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "show", "prolific"])

        assert result.exit_code == 0
        assert "prolific" in result.output
        assert "login" in result.output.lower()

    def test_show_not_found(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.load_site.return_value = None
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "show", "nonexistent"])

        assert result.exit_code == 1 or "not found" in result.output.lower()

    def test_show_cascade_flag(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.load_site.return_value = _sample_site_playbook()
            mock_svc.resolve.return_value = "Resolved cascade content"
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "show", "prolific", "--cascade"])

        assert result.exit_code == 0
        assert "cascade" in result.output.lower() or "Resolved" in result.output


class TestTrainDelete:
    def test_delete_calls_delete_site(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.delete_site.return_value = True
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "delete", "prolific"], input="y\n")

        assert result.exit_code == 0
        mock_svc.delete_site.assert_called_once_with("prolific")
        assert "Deleted" in result.output or "deleted" in result.output

    def test_delete_not_found(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.delete_site.return_value = False
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "delete", "nonexistent"], input="y\n")

        assert result.exit_code == 0
        assert "not found" in result.output.lower() or "No playbook" in result.output

    def test_delete_with_flow_option(self, runner):
        """--flow option deletes a specific flow from the playbook."""
        site = _sample_site_playbook()
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc.load_site.return_value = site
            mock_svc.save_site.return_value = None
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "delete", "prolific", "--flow", "login"], input="y\n")

        assert result.exit_code == 0
        assert "login" in result.output.lower()

    def test_delete_cancelled(self, runner):
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.PlaybookService") as mock_svc_cls,
        ):
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(cli, ["train", "delete", "prolific"], input="n\n")

        assert result.exit_code == 0
        mock_svc.delete_site.assert_not_called()
