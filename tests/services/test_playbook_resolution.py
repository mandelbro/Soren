"""Tests for PlaybookService cascading resolution, prompt formatting, and confidence tracking (Task 007)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from soren.models.host_playbook import HostPlaybook
from soren.models.site_playbook import (
    ExecutionFlow,
    FlowPlaybook,
    LoginFlow,
    PlaybookStep,
    SiteFlows,
    SitePlaybook,
)
from soren.services.playbook_service import PlaybookService


def _make_settings(tmp_path: Path, *, cascade_enabled: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.playbooks = MagicMock()
    settings.playbooks.base_dir = str(tmp_path / "playbooks")
    settings.playbooks.site_playbooks_dir = str(tmp_path / "playbooks" / "site")
    settings.playbooks.host_playbooks_dir = str(tmp_path / "playbooks" / "host")
    settings.playbooks.base_playbooks_dir = str(tmp_path / "playbooks" / "base")
    settings.playbooks.cascade_enabled = cascade_enabled
    settings.llm = MagicMock()
    settings.llm.primary_model = "gemma3:12b"
    return settings


def _make_llm() -> MagicMock:
    return MagicMock()


def _write_base_playbook(base_dir: Path, task_type: str, patterns: dict) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    data = {"task_type": task_type, "version": 1, "patterns": patterns}
    (base_dir / f"{task_type}.yaml").write_text(yaml.dump(data, sort_keys=False))


def _make_svc(tmp_path: Path, *, cascade_enabled: bool = True) -> PlaybookService:
    settings = _make_settings(tmp_path, cascade_enabled=cascade_enabled)
    return PlaybookService(settings, _make_llm())


def _make_site_with_execution(source_name: str = "prolific") -> SitePlaybook:
    return SitePlaybook(
        source_name=source_name,
        source_url="https://prolific.com",
        flows=SiteFlows(
            login=LoginFlow(
                steps=[
                    PlaybookStep(seq=1, instruction="Click login", expected_after="Login modal visible"),
                    PlaybookStep(
                        seq=2, instruction="Enter email", expected_after="Email filled", field="credential:username"
                    ),
                ],
            ),
            execution={
                "survey": ExecutionFlow(
                    completion_indicator="Thank you",
                    steps=[
                        PlaybookStep(seq=1, instruction="Click start survey", expected_after="Survey page loaded"),
                        PlaybookStep(seq=2, instruction="Answer questions", expected_after="All answered"),
                        PlaybookStep(seq=3, instruction="Click submit", expected_after="Confirmation shown"),
                    ],
                ),
            },
        ),
    )


class TestResolve:
    def test_resolve_all_three_tiers(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        svc.save_site(_make_site_with_execution())
        svc.save_host(
            HostPlaybook(
                host_domain="qualtrics.com",
                navigation={"quirk": "Double-click submit"},
            )
        )
        _write_base_playbook(tmp_path / "playbooks" / "base", "survey", {"nav": "click next"})

        result = svc.resolve("prolific", "qualtrics.com", "survey", "execution")
        assert "Site-Specific Instructions" in result
        assert "Host Instructions" in result
        assert "General Guidelines" in result

    def test_resolve_site_only(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        svc.save_site(_make_site_with_execution())
        result = svc.resolve("prolific", None, "survey", "execution")
        assert "Site-Specific Instructions" in result
        assert "Host Instructions" not in result
        assert "General Guidelines" not in result

    def test_resolve_base_only(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        _write_base_playbook(tmp_path / "playbooks" / "base", "survey", {"nav": "click next"})
        result = svc.resolve("nonexistent", None, "survey", "execution")
        assert "General Guidelines" in result
        assert "Site-Specific Instructions" not in result

    def test_resolve_returns_empty_when_nothing_found(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        result = svc.resolve("nonexistent", None, "unknown", "execution")
        assert result == ""

    def test_resolve_cascade_disabled(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path, cascade_enabled=False)
        svc.save_site(_make_site_with_execution())
        _write_base_playbook(tmp_path / "playbooks" / "base", "survey", {"nav": "click next"})
        result = svc.resolve("prolific", None, "survey", "execution")
        # Should only contain site content, no base/host
        assert "General Guidelines" not in result
        assert "Host Instructions" not in result


class TestToPromptInstructions:
    def test_produces_numbered_steps(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        flow = FlowPlaybook(
            steps=[
                PlaybookStep(seq=1, instruction="Click login", expected_after="Login modal visible"),
                PlaybookStep(seq=2, instruction="Enter credentials", expected_after="Fields filled"),
            ],
        )
        result = svc.to_prompt_instructions(flow, "login")
        assert "1." in result
        assert "2." in result
        assert "Click login" in result

    def test_handles_credential_placeholders(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        flow = FlowPlaybook(
            steps=[
                PlaybookStep(seq=1, instruction="Enter username", expected_after="Filled", field="credential:username"),
            ],
        )
        result = svc.to_prompt_instructions(flow, "login")
        assert "credential" in result.lower()

    def test_annotates_tab_transitions(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        flow = FlowPlaybook(
            steps=[
                PlaybookStep(
                    seq=1,
                    instruction="Click external link",
                    expected_after="New tab opened",
                    triggers_new_tab=True,
                    host_domain="qualtrics.com",
                ),
            ],
        )
        result = svc.to_prompt_instructions(flow, "execution")
        assert "TAB" in result
        assert "qualtrics.com" in result

    def test_annotates_delegate_to_host(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        flow = FlowPlaybook(
            steps=[
                PlaybookStep(
                    seq=1,
                    instruction="DELEGATE_TO_HOST",
                    expected_after="Host survey loaded",
                    host_domain="qualtrics.com",
                ),
            ],
        )
        result = svc.to_prompt_instructions(flow, "execution")
        assert "DELEGATE" in result

    def test_annotates_return_to_platform(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        flow = FlowPlaybook(
            steps=[
                PlaybookStep(
                    seq=1,
                    instruction="Submit completion code",
                    expected_after="Code accepted",
                    returns_to_platform=True,
                ),
            ],
        )
        result = svc.to_prompt_instructions(flow, "execution")
        assert "RETURN" in result


class TestUpdateConfidence:
    def test_increments_on_success(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        site = _make_site_with_execution()
        site.flows.execution["survey"].confidence = 0.5
        site.flows.execution["survey"].consecutive_successes = 2
        svc.save_site(site)

        svc.update_confidence("prolific", "execution", success=True, task_type="survey")
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.flows.execution["survey"].confidence == pytest.approx(0.65)
        assert loaded.flows.execution["survey"].consecutive_successes == 3

    def test_decrements_on_failure(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        site = _make_site_with_execution()
        site.flows.execution["survey"].confidence = 0.5
        site.flows.execution["survey"].consecutive_successes = 3
        svc.save_site(site)

        svc.update_confidence("prolific", "execution", success=False, task_type="survey")
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.flows.execution["survey"].confidence == pytest.approx(0.3)
        assert loaded.flows.execution["survey"].consecutive_successes == 0

    def test_confidence_floor_at_zero(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        site = _make_site_with_execution()
        site.flows.execution["survey"].confidence = 0.1
        svc.save_site(site)

        svc.update_confidence("prolific", "execution", success=False, task_type="survey")
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.flows.execution["survey"].confidence == 0.0

    def test_confidence_ceiling_at_one(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        site = _make_site_with_execution()
        site.flows.execution["survey"].confidence = 0.95
        svc.save_site(site)

        svc.update_confidence("prolific", "execution", success=True, task_type="survey")
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.flows.execution["survey"].confidence == 1.0

    def test_updates_login_flow_confidence(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        site = _make_site_with_execution()
        site.flows.login.confidence = 0.3
        svc.save_site(site)

        svc.update_confidence("prolific", "login", success=True)
        loaded = svc.load_site("prolific")
        assert loaded is not None
        assert loaded.flows.login.confidence == pytest.approx(0.45)
