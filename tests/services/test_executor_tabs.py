"""Tests for tab-aware execution in TaskExecutor."""

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
    async def test_detects_new_tab_and_injects_content(self):
        """New tab detected -> logs transition, injects host content into messages, returns updated count."""
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(return_value=True)
        vm.get_active_tab_url = AsyncMock(return_value="https://qualtrics.com/survey/123")
        vm.get_tab_count = AsyncMock(return_value=2)

        ps = MagicMock()
        ps.resolve.return_value = "Host instructions for qualtrics"

        executor = _make_executor(vm=vm, playbook_service=ps)
        task = _make_task()
        messages: list = [{"role": "user", "content": "do task"}]
        step_log: list = []

        updated_count = await executor._handle_tab_transition(
            task=task,
            messages=messages,
            step_log=step_log,
            steps_taken=5,
            pre_tab_count=1,
            platform_domain="prolific.com",
        )

        assert len(step_log) == 1
        assert step_log[0]["action_type"] == "tab_switch"
        assert step_log[0]["action_data"]["host_domain"] == "qualtrics.com"
        # Host content injected into messages
        assert len(messages) == 2
        assert "DELEGATE_TO_HOST" in messages[1]["content"]
        assert "Host instructions for qualtrics" in messages[1]["content"]
        # Tab count updated
        assert updated_count == 2

    @pytest.mark.asyncio
    async def test_returns_updated_count_prevents_retrigger(self):
        """After first detection, updated count prevents repeated triggers."""
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(return_value=True)
        vm.get_active_tab_url = AsyncMock(return_value="https://qualtrics.com/survey/123")
        vm.get_tab_count = AsyncMock(return_value=2)

        ps = MagicMock()
        ps.resolve.return_value = "Host instructions"

        executor = _make_executor(vm=vm, playbook_service=ps)
        messages: list = []
        step_log: list = []

        count = await executor._handle_tab_transition(
            task=_make_task(),
            messages=messages,
            step_log=step_log,
            steps_taken=5,
            pre_tab_count=1,
            platform_domain="prolific.com",
        )

        # Second call with updated count — no new tab
        vm.wait_for_new_tab = AsyncMock(return_value=False)
        count2 = await executor._handle_tab_transition(
            task=_make_task(),
            messages=messages,
            step_log=step_log,
            steps_taken=6,
            pre_tab_count=count,
            platform_domain="prolific.com",
        )

        assert count2 == count  # No change
        assert len(step_log) == 1  # Only one tab_switch

    @pytest.mark.asyncio
    async def test_no_transition_when_no_new_tab(self):
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(return_value=False)

        executor = _make_executor(vm=vm)
        step_log: list = []

        result = await executor._handle_tab_transition(
            task=_make_task(),
            messages=[],
            step_log=step_log,
            steps_taken=5,
            pre_tab_count=1,
            platform_domain="prolific.com",
        )

        assert len(step_log) == 0
        assert result == 1  # Unchanged

    @pytest.mark.asyncio
    async def test_ignores_same_domain_tab(self):
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(return_value=True)
        vm.get_active_tab_url = AsyncMock(return_value="https://prolific.com/other-page")
        vm.get_tab_count = AsyncMock(return_value=2)

        executor = _make_executor(vm=vm)
        step_log: list = []

        result = await executor._handle_tab_transition(
            task=_make_task(),
            messages=[],
            step_log=step_log,
            steps_taken=5,
            pre_tab_count=1,
            platform_domain="prolific.com",
        )

        assert len(step_log) == 0
        assert result == 2  # Updated count but no delegation

    @pytest.mark.asyncio
    async def test_returns_none_when_pre_tab_count_is_none(self):
        executor = _make_executor()
        result = await executor._handle_tab_transition(
            task=_make_task(),
            messages=[],
            step_log=[],
            steps_taken=5,
            pre_tab_count=None,
            platform_domain="prolific.com",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_vm_error_gracefully(self):
        vm = MagicMock()
        vm.wait_for_new_tab = AsyncMock(side_effect=Exception("VM unreachable"))

        executor = _make_executor(vm=vm)
        step_log: list = []

        result = await executor._handle_tab_transition(
            task=_make_task(),
            messages=[],
            step_log=step_log,
            steps_taken=5,
            pre_tab_count=1,
            platform_domain="prolific.com",
        )

        assert len(step_log) == 0
        assert result == 1  # Returns original count on error


class TestPlatformReturn:
    @pytest.mark.asyncio
    async def test_returns_to_platform_tab(self):
        vm = MagicMock()
        vm.find_tab_by_domain = AsyncMock(return_value=1)
        vm.switch_to_tab = AsyncMock(return_value=True)

        executor = _make_executor(vm=vm)
        step_log: list = []

        await executor._handle_platform_return("prolific.com", step_log, steps_taken=10)

        vm.find_tab_by_domain.assert_called_once_with("prolific.com")
        vm.switch_to_tab.assert_called_once_with(1)
        assert len(step_log) == 1
        assert step_log[0]["action_type"] == "tab_switch"
        assert "returns_to_platform" in step_log[0]["vlm_reasoning"]

    @pytest.mark.asyncio
    async def test_platform_tab_not_found_logs_warning(self):
        vm = MagicMock()
        vm.find_tab_by_domain = AsyncMock(return_value=None)

        executor = _make_executor(vm=vm)
        step_log: list = []

        await executor._handle_platform_return("prolific.com", step_log, steps_taken=10)

        assert len(step_log) == 0  # No switch logged

    @pytest.mark.asyncio
    async def test_skips_when_no_platform_domain(self):
        executor = _make_executor()
        step_log: list = []

        await executor._handle_platform_return(None, step_log, steps_taken=10)

        assert len(step_log) == 0


class TestDelegateToHost:
    @pytest.mark.asyncio
    async def test_delegate_resolves_host_playbook(self):
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
    async def test_delegate_falls_back_to_base(self):
        ps = MagicMock()
        ps.resolve.return_value = "Base instructions only"

        executor = _make_executor(playbook_service=ps)
        content = executor._load_playbook_content(_make_task(), host_domain="unknown-host.com")
        assert content is not None

    @pytest.mark.asyncio
    async def test_standard_path_uses_no_host_domain(self):
        ps = MagicMock()
        ps.resolve.return_value = "Standard content"
        ps.load_site.return_value = None

        executor = _make_executor(playbook_service=ps)
        executor._load_playbook_content(_make_task())

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
        assert executor._remaining_budget(steps_taken=15) == 35

    def test_remaining_budget_minimum(self):
        executor = _make_executor()
        executor._step_budget = 50
        assert executor._remaining_budget(steps_taken=55) >= 1
