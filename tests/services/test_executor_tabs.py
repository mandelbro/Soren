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


class TestHandleTabTransition:
    @pytest.mark.asyncio
    async def test_detects_new_tab_and_logs_transition(self):
        """When a new tab appears, handle_tab_transition logs the switch with host domain."""
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(return_value=True)
        vm.get_active_tab_url = AsyncMock(return_value="https://qualtrics.com/survey/123")

        ps = MagicMock()
        ps.resolve.return_value = "Host instructions for qualtrics"

        executor = _make_executor(vm=vm, playbook_service=ps)
        task = _make_task()
        step_log: list = []

        await executor._handle_tab_transition(
            task=task,
            step_log=step_log,
            steps_taken=5,
            pre_tab_count=1,
            platform_domain="prolific.com",
        )

        assert len(step_log) == 1
        assert step_log[0]["action_type"] == "tab_switch"
        assert step_log[0]["action_data"]["host_domain"] == "qualtrics.com"
        ps.resolve.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_transition_when_no_new_tab(self):
        """When no new tab appears, nothing is logged."""
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(return_value=False)

        executor = _make_executor(vm=vm)
        step_log: list = []

        await executor._handle_tab_transition(
            task=_make_task(), step_log=step_log, steps_taken=5, pre_tab_count=1, platform_domain="prolific.com"
        )

        assert len(step_log) == 0

    @pytest.mark.asyncio
    async def test_ignores_same_domain_tab(self):
        """If the new tab is on the same platform domain, skip DELEGATE_TO_HOST."""
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(return_value=True)
        vm.get_active_tab_url = AsyncMock(return_value="https://prolific.com/other-page")

        executor = _make_executor(vm=vm)
        step_log: list = []

        await executor._handle_tab_transition(
            task=_make_task(), step_log=step_log, steps_taken=5, pre_tab_count=1, platform_domain="prolific.com"
        )

        assert len(step_log) == 0

    @pytest.mark.asyncio
    async def test_no_crash_when_pre_tab_count_is_none(self):
        """When pre_tab_count is None (no computer), gracefully skip."""
        executor = _make_executor()
        step_log: list = []

        await executor._handle_tab_transition(
            task=_make_task(), step_log=step_log, steps_taken=5, pre_tab_count=None, platform_domain="prolific.com"
        )

        assert len(step_log) == 0

    @pytest.mark.asyncio
    async def test_handles_vm_error_gracefully(self):
        """If VMManager raises during tab detection, no crash."""
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(side_effect=Exception("VM unreachable"))

        executor = _make_executor(vm=vm)
        step_log: list = []

        await executor._handle_tab_transition(
            task=_make_task(), step_log=step_log, steps_taken=5, pre_tab_count=1, platform_domain="prolific.com"
        )

        assert len(step_log) == 0


class TestDelegateToHost:
    @pytest.mark.asyncio
    async def test_delegate_resolves_host_playbook(self):
        """DELEGATE_TO_HOST resolves via PlaybookService with host_domain."""
        ps = MagicMock()
        ps.resolve.return_value = "Host-specific instructions"

        executor = _make_executor(playbook_service=ps)
        task = _make_task()

        content = executor._load_playbook_content(task, host_domain="qualtrics.com")

        assert content == "Host-specific instructions"
        ps.resolve.assert_called_once_with(
            source_name="prolific",
            host_domain="qualtrics.com",
            task_type="survey",
            flow_type="execution",
        )

    @pytest.mark.asyncio
    async def test_standard_path_uses_no_host_domain(self):
        """Without host_domain, _load_playbook_content passes None."""
        ps = MagicMock()
        ps.resolve.return_value = "Standard content"
        ps.load_site.return_value = None

        executor = _make_executor(playbook_service=ps)
        task = _make_task()

        executor._load_playbook_content(task)

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
        executor = _make_executor()
        executor._step_budget = 50
        remaining = executor._remaining_budget(steps_taken=15)
        assert remaining == 35

    def test_remaining_budget_minimum(self):
        executor = _make_executor()
        executor._step_budget = 50
        remaining = executor._remaining_budget(steps_taken=55)
        assert remaining >= 1
