"""Service config errors are classified as `config_error` and dead-lettered without retries."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from soren.models.service import ServiceConfigError
from soren.models.session import DiscoveredTask, TaskResult
from soren.models.task import Task
from soren.services.database import DatabaseManager
from soren.services.retry_policy import FAILURE_CATEGORIES, NON_RETRYABLE_FAILURES, RetryPolicy, classify_failure
from soren.services.task_backlog import TaskBacklog
from soren.services.task_executor import CONFIG_ERROR_PREFIX
from tests.services.conftest import build_meta_agent

CONFIG_MESSAGE = f"{CONFIG_ERROR_PREFIX} config/services/gcal.yaml: invalid service definition"


class TestClassification:
    def test_prefixed_message_is_config_error(self) -> None:
        assert classify_failure(CONFIG_MESSAGE) == "config_error"

    def test_config_error_wins_over_other_keywords(self) -> None:
        """Pydantic/file text can contain words like 'model' or 'timeout'; the prefix must win."""
        msg = f"{CONFIG_ERROR_PREFIX} vm.yaml: model timeout login auth step_budget exceeded"
        assert classify_failure(msg) == "config_error"

    def test_exception_instance_is_config_error(self) -> None:
        assert classify_failure(ServiceConfigError("broken")) == "config_error"

    def test_registered_and_non_retryable(self) -> None:
        assert "config_error" in FAILURE_CATEGORIES
        assert "config_error" in NON_RETRYABLE_FAILURES

    def test_existing_categories_unchanged(self) -> None:
        assert classify_failure("Request timed out") == "timeout"
        assert classify_failure("Something went wrong") == "unknown"


class TestRetryPolicy:
    def test_config_error_dead_letters_on_first_failure(self) -> None:
        settings = MagicMock()
        settings.scheduler.retry_backoff_minutes = [5, 30, 120]
        settings.scheduler.max_task_retries = 3
        task = Task(id="t", dedup_key="k", attempt_count=0, max_retries=3)
        assert RetryPolicy(settings).compute_next_action(task, "config_error").action == "dead_letter"
        assert RetryPolicy(settings).compute_next_action(task, "timeout").action == "retry"


class TestBacklogDeadLetter:
    @pytest.fixture
    async def backlog(self, tmp_path: Path):
        db = DatabaseManager(tmp_path / "t.db")
        await db.initialize()
        yield TaskBacklog(db)
        await db.close()

    async def _insert(self, backlog: TaskBacklog) -> str:
        await backlog.insert_discovered(
            [
                DiscoveredTask(
                    title="T", source_name="s", source_url="https://x.co", provider_task_id="1", persona_id="p"
                )
            ]
        )
        [task] = await backlog.get_eligible_tasks()
        return task.id

    async def test_config_error_dead_letters_immediately(self, backlog: TaskBacklog) -> None:
        task_id = await self._insert(backlog)
        await backlog.mark_failed(task_id, "config_error", [5, 30, 120])
        [dead] = await backlog.list_tasks(status="dead_letter")
        assert dead.id == task_id
        assert dead.last_failure_reason == "config_error"
        assert dead.attempt_count == 1

    async def test_other_failures_still_retry(self, backlog: TaskBacklog) -> None:
        task_id = await self._insert(backlog)
        await backlog.mark_failed(task_id, "timeout", [5, 30, 120])
        assert await backlog.list_tasks(status="dead_letter") == []
        [queued] = await backlog.list_tasks(status="queued")
        assert queued.id == task_id


class TestMetaAgentConfigError:
    async def test_marked_config_error_and_playbook_confidence_untouched(self) -> None:
        agent = build_meta_agent(task_result=TaskResult(success=False, error=CONFIG_MESSAGE))
        await agent.run_cycle()
        args = agent._backlog.mark_failed.call_args
        assert args[0][1] == "config_error"
        agent._playbook_service.update_confidence.assert_not_called()

    async def test_ordinary_failure_still_decrements_confidence(self) -> None:
        agent = build_meta_agent(task_result=TaskResult(success=False, error="timeout"))
        await agent.run_cycle()
        agent._playbook_service.update_confidence.assert_called_once()
