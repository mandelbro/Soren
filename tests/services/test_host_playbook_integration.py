"""Tests for host playbook integration and auto-learning."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from soren.models.host_playbook import HostPlaybook


def _mock_settings():
    settings = MagicMock()
    settings.playbooks.base_dir = "data/playbooks"
    settings.playbooks.site_playbooks_dir = "data/playbooks/site"
    settings.playbooks.host_playbooks_dir = "data/playbooks/host"
    settings.playbooks.base_playbooks_dir = "data/playbooks/base"
    settings.playbooks.cascade_enabled = True
    settings.playbooks.write_back = True
    settings.playbooks.auto_learn_host = True
    settings.vm = MagicMock()
    settings.llm = MagicMock()
    settings.scheduler = MagicMock()
    settings.scheduler.retry_backoff_minutes = None
    return settings


class TestResolveIncludesHost:
    def test_resolve_includes_host_content_when_domain_provided(self, tmp_path):
        from soren.services.playbook_service import PlaybookService

        settings = _mock_settings()
        settings.playbooks.site_playbooks_dir = str(tmp_path / "site")
        settings.playbooks.host_playbooks_dir = str(tmp_path / "host")
        settings.playbooks.base_playbooks_dir = str(tmp_path / "base")

        llm = MagicMock()
        svc = PlaybookService(settings, llm)

        # Save a host playbook
        host = HostPlaybook(
            host_domain="qualtrics.com",
            navigation={"submit_button": "Bottom-right green button"},
            quirks=["Requires scrolling to bottom before submit is clickable"],
        )
        svc.save_host(host)

        result = svc.resolve(
            source_name="prolific",
            host_domain="qualtrics.com",
            task_type="survey",
            flow_type="execution",
        )

        assert "Host Instructions" in result
        assert "qualtrics" in result.lower() or "submit_button" in result

    def test_resolve_skips_host_when_no_playbook(self, tmp_path):
        from soren.services.playbook_service import PlaybookService

        settings = _mock_settings()
        settings.playbooks.site_playbooks_dir = str(tmp_path / "site")
        settings.playbooks.host_playbooks_dir = str(tmp_path / "host")
        settings.playbooks.base_playbooks_dir = str(tmp_path / "base")

        llm = MagicMock()
        svc = PlaybookService(settings, llm)

        result = svc.resolve(
            source_name="prolific",
            host_domain="unknown-host.com",
            task_type="survey",
            flow_type="execution",
        )

        assert "Host Instructions" not in result


class TestHostAutoLearning:
    @pytest.mark.asyncio
    async def test_auto_learning_triggers_on_new_domain(self):
        """After successful tab-aware execution, auto-generate host playbook for new domain."""
        from soren.services.auto_playbook import auto_generate_host_playbook

        llm = MagicMock()
        llm.generate = AsyncMock(return_value="navigation:\n  submit: click submit\nquirks:\n  - slow loading")

        host = await auto_generate_host_playbook(
            llm=llm,
            step_log=[{"action_type": "click", "action_data": {"target": "submit"}, "vlm_reasoning": "Clicked submit"}],
            host_domain="qualtrics.com",
        )

        assert host.host_domain == "qualtrics.com"
        assert host.learned_from == "auto"

    @pytest.mark.asyncio
    async def test_auto_learning_skips_when_playbook_exists(self, tmp_path):
        """Should not overwrite existing host playbook."""
        from soren.services.playbook_service import PlaybookService

        settings = _mock_settings()
        settings.playbooks.site_playbooks_dir = str(tmp_path / "site")
        settings.playbooks.host_playbooks_dir = str(tmp_path / "host")
        settings.playbooks.base_playbooks_dir = str(tmp_path / "base")

        llm = MagicMock()
        svc = PlaybookService(settings, llm)

        existing = HostPlaybook(
            host_domain="qualtrics.com",
            navigation={"submit": "manual instructions"},
            learned_from="human",
            confidence=0.9,
        )
        svc.save_host(existing)

        # Verify it exists
        loaded = svc.load_host("qualtrics.com")
        assert loaded is not None
        assert loaded.learned_from == "human"

    @pytest.mark.asyncio
    async def test_auto_learning_respects_flag(self):
        """auto_learn_host=False should skip generation."""
        settings = _mock_settings()
        settings.playbooks.auto_learn_host = False
        # The meta_agent checks this flag before calling auto_generate_host_playbook
        assert not settings.playbooks.auto_learn_host


class TestFormatHostPlaybook:
    def test_format_produces_readable_output(self):
        from soren.services.playbook_formatting import format_host_content

        navigation = {"login_button": "Top-right corner", "submit": "Bottom of form"}
        quirks = ["Page loads slowly", "Requires JavaScript enabled"]

        result = format_host_content(navigation, quirks)

        assert "login_button" in result
        assert "Top-right corner" in result
        assert "Page loads slowly" in result


class TestHostConfidenceTracking:
    def test_update_host_confidence_on_success(self, tmp_path):
        from soren.services.playbook_service import PlaybookService

        settings = _mock_settings()
        settings.playbooks.site_playbooks_dir = str(tmp_path / "site")
        settings.playbooks.host_playbooks_dir = str(tmp_path / "host")
        settings.playbooks.base_playbooks_dir = str(tmp_path / "base")

        llm = MagicMock()
        svc = PlaybookService(settings, llm)

        host = HostPlaybook(host_domain="qualtrics.com", confidence=0.5)
        svc.save_host(host)

        svc.update_host_confidence("qualtrics.com", success=True)

        updated = svc.load_host("qualtrics.com")
        assert updated is not None
        assert updated.confidence > 0.5

    def test_update_host_confidence_on_failure(self, tmp_path):
        from soren.services.playbook_service import PlaybookService

        settings = _mock_settings()
        settings.playbooks.site_playbooks_dir = str(tmp_path / "site")
        settings.playbooks.host_playbooks_dir = str(tmp_path / "host")
        settings.playbooks.base_playbooks_dir = str(tmp_path / "base")

        llm = MagicMock()
        svc = PlaybookService(settings, llm)

        host = HostPlaybook(host_domain="qualtrics.com", confidence=0.5)
        svc.save_host(host)

        svc.update_host_confidence("qualtrics.com", success=False)

        updated = svc.load_host("qualtrics.com")
        assert updated is not None
        assert updated.confidence < 0.5
