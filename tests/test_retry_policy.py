"""Tests for RetryPolicy."""

from unittest.mock import MagicMock

from soren.models.task import Task
from soren.services.retry_policy import RetryPolicy, classify_failure


def _make_task(attempt_count: int = 0, max_retries: int = 3) -> Task:
    return Task(
        id="t1",
        dedup_key="k1",
        title="Test",
        attempt_count=attempt_count,
        max_retries=max_retries,
    )


def _make_settings(max_retries: int = 3, backoff: list[int] | None = None) -> MagicMock:
    settings = MagicMock()
    settings.scheduler = MagicMock()
    settings.scheduler.max_task_retries = max_retries
    settings.scheduler.retry_backoff_minutes = backoff or [5, 30, 120]
    return settings


def test_first_failure_retry_5min():
    """First failure (attempt 0) -> retry with 5-minute backoff."""
    policy = RetryPolicy(_make_settings())
    task = _make_task(attempt_count=0)
    action = policy.compute_next_action(task, "timeout")
    assert action.action == "retry"
    assert action.backoff_minutes == 5
    assert action.next_eligible_at is not None


def test_second_failure_retry_30min():
    """Second failure (attempt 1) -> retry with 30-minute backoff."""
    policy = RetryPolicy(_make_settings())
    task = _make_task(attempt_count=1)
    action = policy.compute_next_action(task, "vlm_error")
    assert action.action == "retry"
    assert action.backoff_minutes == 30


def test_third_failure_retry_120min():
    """Third failure (attempt 2) -> retry with 120-minute backoff when max_retries > 3."""
    policy = RetryPolicy(_make_settings(max_retries=4))
    task = _make_task(attempt_count=2, max_retries=4)
    action = policy.compute_next_action(task, "unknown")
    assert action.action == "retry"
    assert action.backoff_minutes == 120


def test_fourth_failure_dead_letter():
    """Third failure (attempt_count=2, max_retries=3) -> dead-letter after increment."""
    policy = RetryPolicy(_make_settings(max_retries=3))
    task = _make_task(attempt_count=2, max_retries=3)
    action = policy.compute_next_action(task, "timeout")
    assert action.action == "dead_letter"
    assert action.next_eligible_at is None
    assert action.backoff_minutes is None


def test_custom_max_retries():
    """Custom max_retries setting honored."""
    policy = RetryPolicy(_make_settings(max_retries=5))
    task = _make_task(attempt_count=4, max_retries=5)
    action = policy.compute_next_action(task, "timeout")
    assert action.action == "dead_letter"


def test_custom_backoff_schedule():
    """Custom retry_backoff_minutes schedule honored."""
    policy = RetryPolicy(_make_settings(backoff=[1, 2, 3]))
    task = _make_task(attempt_count=0)
    action = policy.compute_next_action(task, "timeout")
    assert action.action == "retry"
    assert action.backoff_minutes == 1

    task2 = _make_task(attempt_count=1)
    action2 = policy.compute_next_action(task2, "timeout")
    assert action2.backoff_minutes == 2


def test_backoff_overflow_uses_last_value():
    """Attempt count beyond schedule length uses last value."""
    policy = RetryPolicy(_make_settings(backoff=[5, 30]))
    task = _make_task(attempt_count=5, max_retries=10)
    action = policy.compute_next_action(task, "timeout")
    assert action.action == "retry"
    assert action.backoff_minutes == 30


def test_classify_timeout():
    """Error classification maps timeout correctly."""
    assert classify_failure(Exception("Request timed out")) == "timeout"
    assert classify_failure("Connection timeout") == "timeout"


def test_classify_vlm_error():
    """Error classification maps VLM error correctly."""
    assert classify_failure("Ollama model not found") == "vlm_error"
    assert classify_failure("VLM inference failed") == "vlm_error"


def test_classify_unknown():
    """Error classification defaults to unknown for unrecognized errors."""
    assert classify_failure("Something went wrong") == "unknown"
    assert classify_failure(Exception("Random error")) == "unknown"
