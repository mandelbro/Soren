"""Tests for PlaybookService YAML CRUD methods (Task 006)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from soren.models.host_playbook import HostPlaybook
from soren.models.site_playbook import (
    FlowPlaybook,
    LoginFlow,
    PlaybookStep,
    SiteFlows,
    SitePlaybook,
)
from soren.services.playbook_service import PlaybookService


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.playbooks = MagicMock()
    settings.playbooks.base_dir = str(tmp_path / "playbooks")
    settings.playbooks.site_playbooks_dir = str(tmp_path / "playbooks" / "site")
    settings.playbooks.host_playbooks_dir = str(tmp_path / "playbooks" / "host")
    settings.playbooks.base_playbooks_dir = str(tmp_path / "playbooks" / "base")
    settings.playbooks.cascade_enabled = True
    settings.llm = MagicMock()
    settings.llm.primary_model = "gemma3:12b"
    return settings


def _make_llm() -> MagicMock:
    llm = MagicMock()
    return llm


def _make_site_playbook(source_name: str = "prolific") -> SitePlaybook:
    return SitePlaybook(
        source_name=source_name,
        source_url="https://prolific.com",
        flows=SiteFlows(
            login=LoginFlow(
                steps=[
                    PlaybookStep(seq=1, instruction="Click login", expected_after="Login modal visible"),
                ],
            ),
        ),
    )


@pytest.fixture
def svc(tmp_path: Path) -> PlaybookService:
    settings = _make_settings(tmp_path)
    return PlaybookService(settings, _make_llm())


class TestLoadSite:
    def test_load_site_returns_playbook_from_valid_yaml(self, svc: PlaybookService, tmp_path: Path) -> None:
        playbook = _make_site_playbook()
        svc.save_site(playbook)
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.source_name == "prolific"
        assert loaded.flows.login is not None

    def test_load_site_returns_none_for_nonexistent(self, svc: PlaybookService) -> None:
        assert svc.load_site("nonexistent") is None


class TestSaveSite:
    def test_save_site_writes_valid_yaml(self, svc: PlaybookService) -> None:
        playbook = _make_site_playbook()
        path = svc.save_site(playbook)
        assert path.exists()
        raw = yaml.safe_load(path.read_text())
        assert raw["source_name"] == "prolific"

    def test_save_site_round_trip(self, svc: PlaybookService) -> None:
        original = _make_site_playbook()
        svc.save_site(original)
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.source_name == original.source_name
        assert loaded.source_url == original.source_url
        assert loaded.flows.login is not None
        assert len(loaded.flows.login.steps) == 1


class TestSaveFlow:
    def test_save_flow_updates_existing_playbook(self, svc: PlaybookService) -> None:
        svc.save_site(_make_site_playbook())
        new_flow = FlowPlaybook(
            steps=[PlaybookStep(seq=1, instruction="Open tasks", expected_after="Task list visible")]
        )
        svc.save_flow("prolific", "discovery", new_flow)
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.flows.discovery is not None
        assert loaded.flows.discovery.steps[0].instruction == "Open tasks"

    def test_save_flow_creates_new_playbook_when_none_exists(self, svc: PlaybookService) -> None:
        flow = FlowPlaybook(steps=[PlaybookStep(seq=1, instruction="Do thing", expected_after="Thing done")])
        path = svc.save_flow("new_source", "discovery", flow)
        assert path.exists()
        loaded = svc.load_site("new_source")
        assert loaded is not None
        assert loaded.flows.discovery is not None

    def test_save_flow_execution_with_task_type(self, svc: PlaybookService) -> None:
        svc.save_site(_make_site_playbook())
        exec_flow = FlowPlaybook(steps=[PlaybookStep(seq=1, instruction="Fill form", expected_after="Form submitted")])
        svc.save_flow("prolific", "execution", exec_flow, task_type="survey")
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.flows.execution is not None
        assert "survey" in loaded.flows.execution


class TestListSitePlaybooks:
    def test_list_enumerates_all_yaml_files(self, svc: PlaybookService) -> None:
        svc.save_site(_make_site_playbook("prolific"))
        svc.save_site(_make_site_playbook("mturk"))
        playbooks = svc.list_site_playbooks()
        names = {p.source_name for p in playbooks}
        assert "prolific" in names
        assert "mturk" in names

    def test_list_returns_empty_when_no_playbooks(self, svc: PlaybookService) -> None:
        assert svc.list_site_playbooks() == []


class TestDeleteSite:
    def test_delete_removes_file_and_returns_true(self, svc: PlaybookService) -> None:
        svc.save_site(_make_site_playbook())
        assert svc.delete_site("prolific") is True
        assert svc.load_site("prolific") is None

    def test_delete_returns_false_for_nonexistent(self, svc: PlaybookService) -> None:
        assert svc.delete_site("nonexistent") is False


class TestHostPlaybookRoundTrip:
    def test_save_and_load_host(self, svc: PlaybookService) -> None:
        host = HostPlaybook(
            host_domain="qualtrics.com",
            url_patterns=["*.qualtrics.com/*"],
            quirks=["Session expires after 30 min"],
        )
        path = svc.save_host(host)
        assert path.exists()
        loaded = svc.load_host("qualtrics.com")
        assert loaded is not None
        assert loaded.host_domain == "qualtrics.com"
        assert loaded.quirks == ["Session expires after 30 min"]


class TestLoadBase:
    def test_load_base_returns_playbook(self, svc: PlaybookService, tmp_path: Path) -> None:
        base_dir = tmp_path / "playbooks" / "base"
        base_dir.mkdir(parents=True, exist_ok=True)
        data = {"task_type": "survey", "version": 1, "patterns": {"nav": "click next"}}
        (base_dir / "survey.yaml").write_text(yaml.dump(data))
        loaded = svc.load_base("survey")
        assert loaded is not None
        assert loaded.task_type == "survey"


class TestLegacyDeprecation:
    def test_load_emits_deprecation_warning(self, svc: PlaybookService) -> None:
        with pytest.warns(DeprecationWarning, match="deprecated"):
            svc.load("test_service")
