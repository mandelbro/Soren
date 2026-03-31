"""Tests for TaskDiscovery playbook integration (Task 009)."""

from __future__ import annotations

from unittest.mock import MagicMock

from soren.models.site_playbook import (
    DiscoveryFlow,
    LoginFlow,
    PlaybookStep,
    SiteFlows,
    SitePlaybook,
)
from soren.services.task_discovery import TaskDiscovery


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.playbooks = MagicMock()
    settings.playbooks.cascade_enabled = True
    vlm = MagicMock()
    vlm.model = "mlx-community/UI-TARS-1.5-7B-6bit"
    settings.vlm = vlm
    return settings


def _make_source(name: str = "prolific") -> MagicMock:
    source = MagicMock()
    source.name = name
    source.type = "survey_platform"
    source.url = "https://prolific.com"
    source.discovery_hint = ""
    return source


def _make_persona() -> MagicMock:
    persona = MagicMock()
    persona.id = "p1"
    persona.name = "Test"
    return persona


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.navigation_prompt.return_value = "Navigate to surveys page"
    return adapter


def _make_playbook_service_with_login() -> MagicMock:
    svc = MagicMock()
    site = SitePlaybook(
        source_name="prolific",
        source_url="https://prolific.com",
        flows=SiteFlows(
            login=LoginFlow(
                steps=[
                    PlaybookStep(seq=1, instruction="Click login", expected_after="Login modal"),
                ],
            ),
        ),
    )
    svc.load_site.return_value = site
    svc.to_prompt_instructions.return_value = "1. Click login (expect: Login modal)"
    return svc


def _make_playbook_service_with_discovery() -> MagicMock:
    svc = MagicMock()
    site = SitePlaybook(
        source_name="prolific",
        source_url="https://prolific.com",
        flows=SiteFlows(
            discovery=DiscoveryFlow(
                discovery_ready_indicator="Task list loaded",
                steps=[
                    PlaybookStep(seq=1, instruction="Click studies tab", expected_after="Studies visible"),
                ],
            ),
        ),
    )
    svc.load_site.return_value = site
    svc.to_prompt_instructions.return_value = "1. Click studies tab (expect: Studies visible)"
    return svc


class TestPhase1NavigateWithPlaybooks:
    def test_uses_login_playbook_when_available(self) -> None:
        settings = _make_settings()
        playbook_svc = _make_playbook_service_with_login()

        discovery = TaskDiscovery(
            settings, MagicMock(), MagicMock(), MagicMock(),
            playbook_service=playbook_svc,
        )
        source = _make_source()

        prompt = discovery._build_nav_prompt(MagicMock(), _make_persona(), source)
        assert "Click login" in prompt
        playbook_svc.load_site.assert_called_once_with("prolific")

    def test_falls_back_to_adapter_when_no_playbook(self) -> None:
        settings = _make_settings()
        playbook_svc = MagicMock()
        playbook_svc.load_site.return_value = None

        discovery = TaskDiscovery(
            settings, MagicMock(), MagicMock(), MagicMock(),
            playbook_service=playbook_svc,
        )

        adapter = _make_adapter()
        source = _make_source()
        prompt = discovery._build_nav_prompt(adapter, _make_persona(), source)
        assert "Navigate to surveys page" in prompt

    def test_combined_login_and_discovery_prompt(self) -> None:
        settings = _make_settings()
        playbook_svc = MagicMock()
        site = SitePlaybook(
            source_name="prolific",
            source_url="https://prolific.com",
            flows=SiteFlows(
                login=LoginFlow(
                    steps=[PlaybookStep(seq=1, instruction="Log in", expected_after="Logged in")],
                ),
                discovery=DiscoveryFlow(
                    steps=[PlaybookStep(seq=1, instruction="Go to studies", expected_after="Studies page")],
                ),
            ),
        )
        playbook_svc.load_site.return_value = site
        playbook_svc.to_prompt_instructions.side_effect = [
            "1. Log in (expect: Logged in)",
            "1. Go to studies (expect: Studies page)",
        ]

        discovery = TaskDiscovery(
            settings, MagicMock(), MagicMock(), MagicMock(),
            playbook_service=playbook_svc,
        )
        source = _make_source()
        prompt = discovery._build_nav_prompt(MagicMock(), _make_persona(), source)
        assert "Log in" in prompt
        assert "Go to studies" in prompt
        assert "After login" in prompt

    def test_no_playbook_service_uses_adapter(self) -> None:
        settings = _make_settings()
        discovery = TaskDiscovery(
            settings, MagicMock(), MagicMock(), MagicMock(),
        )
        adapter = _make_adapter()
        source = _make_source()
        prompt = discovery._build_nav_prompt(adapter, _make_persona(), source)
        assert "Navigate to surveys page" in prompt
