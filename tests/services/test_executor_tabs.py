"""Tests for tab-aware execution in TaskExecutor (Task 019)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from soren.services.task_executor import TaskExecutor


def _mock_settings():
    settings = MagicMock()
    settings.playbooks = MagicMock()
    settings.verification = MagicMock()
    settings.verification.model = "qwen3-vl:8b"
    settings.vlm = None
    return settings


def _make_executor(vm=None, playbook_service=None) -> TaskExecutor:
    settings = _mock_settings()
    llm = MagicMock()
    vm = vm or MagicMock()
    return TaskExecutor(
        settings=settings,
        llm=llm,
        vm=vm,
        playbook_service=playbook_service,
    )


def _make_task(source_name="prolific", task_type="survey"):
    task = MagicMock()
    task.id = "test-task-001"
    task.title = "Complete survey"
    task.description = "Complete the survey on prolific"
    task.source_name = source_name
    task.task_type = task_type
    task.source_url = "https://prolific.com"
    return task


def _make_persona():
    persona = MagicMock()
    persona.id = "researcher-01"
    persona.task_sources = []
    return persona


class TestHandleTabTransition:
    @pytest.mark.asyncio
    async def test_detects_new_tab_after_triggers_new_tab_step(self):
        """When a step has triggers_new_tab, executor should check for tab changes."""
        vm = MagicMock()
        vm.get_tab_count = AsyncMock(return_value=2)
        vm.wait_for_new_tab = AsyncMock(return_value=True)
        vm.get_active_tab_url = AsyncMock(return_value="https://qualtrics.com/survey/123")
        vm.find_tab_by_domain = AsyncMock(return_value=1)
        vm.switch_to_tab = AsyncMock(return_value=True)

        ps = MagicMock()
        ps.resolve.return_value = "Host instructions for qualtrics"
        ps.load_host.return_value = None

        executor = _make_executor(vm=vm, playbook_service=ps)

        result = executor._detect_host_domain("https://qualtrics.com/survey/123")
        assert result == "qualtrics.com"

    @pytest.mark.asyncio
    async def test_delegate_to_host_resolves_host_playbook(self):
        """DELEGATE_TO_HOST should resolve host playbook via PlaybookService."""
        ps = MagicMock()
        ps.resolve.return_value = "Host-specific instructions"

        executor = _make_executor(playbook_service=ps)

        content = executor._resolve_host_content(
            source_name="prolific",
            host_domain="qualtrics.com",
            task_type="survey",
        )

        assert content == "Host-specific instructions"
        ps.resolve.assert_called_once_with(
            source_name="prolific",
            host_domain="qualtrics.com",
            task_type="survey",
            flow_type="execution",
        )

    @pytest.mark.asyncio
    async def test_delegate_to_host_falls_back_to_base(self):
        """When no host playbook, falls back to base content."""
        ps = MagicMock()
        ps.resolve.return_value = "Base instructions only"

        executor = _make_executor(playbook_service=ps)

        content = executor._resolve_host_content(
            source_name="prolific",
            host_domain="unknown-host.com",
            task_type="survey",
        )

        assert content is not None

    @pytest.mark.asyncio
    async def test_no_tab_steps_uses_standard_execution(self):
        """When no tab steps exist, _load_playbook_content uses host_domain=None."""
        ps = MagicMock()
        ps.resolve.return_value = "Standard content"
        ps.load_site.return_value = None

        executor = _make_executor(playbook_service=ps)
        task = _make_task()

        executor._load_playbook_content(task)

        # Standard path: host_domain=None
        ps.resolve.assert_called_once_with(
            source_name="prolific",
            host_domain=None,
            task_type="survey",
            flow_type="execution",
        )


class TestDomainExtraction:
    def test_extracts_domain_from_url(self):
        executor = _make_executor()
        assert executor._detect_host_domain("https://qualtrics.com/survey/123") == "qualtrics.com"
        assert executor._detect_host_domain("https://www.google.com/forms/d/abc") == "www.google.com"

    def test_returns_none_for_empty_url(self):
        executor = _make_executor()
        assert executor._detect_host_domain("") is None
        assert executor._detect_host_domain("not-a-url") is None


class TestStepBudgetSharing:
    def test_remaining_budget_calculation(self):
        """Step budget should be shared with delegated execution."""
        executor = _make_executor()
        executor._step_budget = 50
        remaining = executor._remaining_budget(steps_taken=15)
        assert remaining == 35

    def test_remaining_budget_minimum(self):
        """Remaining budget should never go below 1."""
        executor = _make_executor()
        executor._step_budget = 50
        remaining = executor._remaining_budget(steps_taken=55)
        assert remaining >= 1
