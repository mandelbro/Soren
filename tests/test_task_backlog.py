"""Tests for TaskBacklog service (WI 4.1)."""

import pytest

from soren.models.session import DiscoveredTask
from soren.services.database import DatabaseManager
from soren.services.task_backlog import (
    TaskBacklog,
    build_dedup_key,
    normalize_snippet,
    normalize_title,
)


@pytest.fixture
async def db(tmp_path):
    """Create and initialize a temporary database."""
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(db_path)
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
def backlog(db):
    """Create TaskBacklog with db fixture (async fixture)."""
    return TaskBacklog(db)


@pytest.mark.asyncio
async def test_insert_single_task_with_provider_id(backlog):
    """Insert single task with provider_task_id → dedup key uses native ID."""
    tasks = [
        DiscoveredTask(
            title="Survey A",
            source_name="betapanel",
            source_url="https://betapanel.example",
            provider_task_id="PID-123",
            persona_id="researcher",
        )
    ]
    result = await backlog.insert_discovered(tasks)
    assert result.inserted == 1
    assert result.skipped == 0
    assert result.errors == 0


@pytest.mark.asyncio
async def test_insert_single_task_without_provider_id(backlog):
    """Insert single task without provider_task_id → dedup key uses content fingerprint."""
    tasks = [
        DiscoveredTask(
            title="Survey B",
            source_name="betapanel",
            source_url="https://betapanel.example",
            persona_id="researcher",
            snippet="Short description",
        )
    ]
    result = await backlog.insert_discovered(tasks)
    assert result.inserted == 1
    assert result.skipped == 0


@pytest.mark.asyncio
async def test_insert_duplicate_silently_skipped(backlog):
    """Insert duplicate task (same dedup_key) → silently skipped, InsertResult.skipped == 1."""
    tasks = [
        DiscoveredTask(
            title="Dup Survey",
            source_name="betapanel",
            source_url="https://betapanel.example",
            provider_task_id="DUP-1",
            persona_id="researcher",
        )
    ]
    r1 = await backlog.insert_discovered(tasks)
    assert r1.inserted == 1
    r2 = await backlog.insert_discovered(tasks)
    assert r2.skipped == 1
    assert r2.inserted == 0


@pytest.mark.asyncio
async def test_batch_insert_with_duplicates(backlog):
    """Batch insert of 5 tasks with 2 duplicates → 3 inserted, 2 skipped."""
    base = DiscoveredTask(
        source_name="betapanel",
        source_url="https://betapanel.example",
        persona_id="researcher",
    )
    tasks = [
        DiscoveredTask(**{**base.model_dump(), "title": "A", "provider_task_id": "1"}),
        DiscoveredTask(**{**base.model_dump(), "title": "B", "provider_task_id": "2"}),
        DiscoveredTask(**{**base.model_dump(), "title": "C", "provider_task_id": "3"}),
        DiscoveredTask(**{**base.model_dump(), "title": "D", "provider_task_id": "1"}),
        DiscoveredTask(**{**base.model_dump(), "title": "E", "provider_task_id": "2"}),
    ]
    result = await backlog.insert_discovered(tasks)
    assert result.inserted == 3
    assert result.skipped == 2


@pytest.mark.asyncio
async def test_get_eligible_excludes_dead_letter(backlog):
    """get_eligible_tasks() excludes dead_letter status."""
    tasks = [
        DiscoveredTask(
            title="Queued",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="q1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    assert len(eligible) >= 1
    task = eligible[0]
    await backlog.mark_in_progress(task.id)
    await backlog.mark_failed(task.id, "error", [5, 30, 120])
    await backlog.mark_failed(task.id, "error", [5, 30, 120])
    await backlog.mark_failed(task.id, "error", [5, 30, 120])
    eligible2 = await backlog.get_eligible_tasks()
    assert not any(t.id == task.id for t in eligible2)


@pytest.mark.asyncio
async def test_get_eligible_excludes_future_next_eligible_at(backlog):
    """get_eligible_tasks() excludes tasks with future next_eligible_at."""
    tasks = [
        DiscoveredTask(
            title="Future",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="f1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    task = next(t for t in eligible if t.provider_task_id == "f1")
    await backlog.mark_in_progress(task.id)
    await backlog.mark_failed(task.id, "err", [1440])
    eligible2 = await backlog.get_eligible_tasks()
    assert not any(t.id == task.id for t in eligible2)


@pytest.mark.asyncio
async def test_get_eligible_includes_past_or_null_next_eligible_at(backlog):
    """get_eligible_tasks() includes tasks with past or null next_eligible_at."""
    tasks = [
        DiscoveredTask(
            title="Fresh",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="fresh1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    assert len(eligible) >= 1
    assert any(t.provider_task_id == "fresh1" for t in eligible)


@pytest.mark.asyncio
async def test_get_eligible_filters_by_persona(backlog):
    """get_eligible_tasks() filters by persona_id when provided."""
    tasks = [
        DiscoveredTask(
            title="P1 Task",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="p1t1",
            persona_id="persona1",
        ),
        DiscoveredTask(
            title="P2 Task",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="p2t1",
            persona_id="persona2",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks(persona_id="persona1")
    assert all(t.persona_id == "persona1" for t in eligible)
    assert len(eligible) >= 1


@pytest.mark.asyncio
async def test_mark_in_progress_sets_status_and_started_at(backlog):
    """mark_in_progress() sets status and started_at."""
    tasks = [
        DiscoveredTask(
            title="Progress",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="prog1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    task = eligible[0]
    await backlog.mark_in_progress(task.id)
    listed = await backlog.list_tasks(status="in_progress")
    assert len(listed) >= 1
    t = next(x for x in listed if x.id == task.id)
    assert t.status == "in_progress"
    assert t.started_at is not None


@pytest.mark.asyncio
async def test_mark_completed_sets_status_and_result_data(backlog):
    """mark_completed() sets status, completed_at, and result_data."""
    tasks = [
        DiscoveredTask(
            title="Complete",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="comp1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    task = eligible[0]
    await backlog.mark_in_progress(task.id)
    await backlog.mark_completed(task.id, result_data={"reward": 2.5})
    listed = await backlog.list_tasks(status="completed")
    t = next(x for x in listed if x.id == task.id)
    assert t.status == "completed"
    assert t.completed_at is not None
    assert t.result_data == {"reward": 2.5}


@pytest.mark.asyncio
async def test_mark_failed_increments_attempt_and_sets_backoff(backlog):
    """mark_failed() increments attempt_count and sets backoff."""
    tasks = [
        DiscoveredTask(
            title="Fail",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="fail1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    task = eligible[0]
    await backlog.mark_in_progress(task.id)
    await backlog.mark_failed(task.id, "first fail", [5, 30, 120])
    listed = await backlog.list_tasks(status="queued")
    t = next(x for x in listed if x.id == task.id)
    assert t.attempt_count == 1
    assert t.next_eligible_at is not None


@pytest.mark.asyncio
async def test_mark_failed_promotes_to_dead_letter_at_max_retries(backlog):
    """mark_failed() promotes to dead_letter when attempt_count >= max_retries."""
    tasks = [
        DiscoveredTask(
            title="Dead",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="dead1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    task = eligible[0]
    for _ in range(3):
        await backlog.mark_in_progress(task.id)
        await backlog.mark_failed(task.id, "fail", [5, 30, 120])
    listed = await backlog.list_tasks(status="dead_letter")
    assert any(t.id == task.id for t in listed)


@pytest.mark.asyncio
async def test_requeue_task_resets_dead_letter(backlog):
    """requeue_task() resets dead-letter task to queued."""
    tasks = [
        DiscoveredTask(
            title="Requeue",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="req1",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    eligible = await backlog.get_eligible_tasks()
    task = eligible[0]
    for _ in range(3):
        await backlog.mark_in_progress(task.id)
        await backlog.mark_failed(task.id, "fail", [5, 30, 120])
    await backlog.requeue_task(task.id)
    eligible2 = await backlog.get_eligible_tasks()
    assert any(t.id == task.id for t in eligible2)


def test_normalize_title_strips_dynamic_fragments():
    """normalize_title() strips dynamic fragments and collapses whitespace."""
    assert normalize_title("Survey 5 left") == "survey"
    assert normalize_title("  Multiple   Spaces  ") == "multiple spaces"
    assert normalize_title("Item NEW") == "item"


def test_normalize_snippet_truncates_at_160():
    """normalize_snippet() truncates at 160 chars."""
    long_text = "a" * 200
    assert len(normalize_snippet(long_text)) == 160


@pytest.mark.asyncio
async def test_get_summary_returns_correct_counts(backlog):
    """get_summary() returns correct counts per status."""
    tasks = [
        DiscoveredTask(
            title="S1",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="s1",
            persona_id="p1",
        ),
        DiscoveredTask(
            title="S2",
            source_name="s",
            source_url="https://x.co",
            provider_task_id="s2",
            persona_id="p1",
        ),
    ]
    await backlog.insert_discovered(tasks)
    summary = await backlog.get_summary()
    assert summary.queued >= 2
    assert summary.total >= 2


def test_build_dedup_key_primary_uses_provider_id():
    """Primary dedup key uses provider_task_id when present."""
    key = build_dedup_key(
        source_name="betapanel",
        persona_id="r1",
        task_type="survey",
        source_url="https://x.co",
        normalized_title="t",
        normalized_snippet="s",
        stable_metadata="m",
        provider_task_id="PID-99",
    )
    assert key == "betapanel:PID-99:r1"


def test_build_dedup_key_fallback_deterministic():
    """Content fingerprint is deterministic: same inputs produce same dedup key."""
    key1 = build_dedup_key(
        source_name="s",
        persona_id="p",
        task_type="survey",
        source_url="https://x.co",
        normalized_title="title",
        normalized_snippet="snippet",
        stable_metadata="meta",
        provider_task_id=None,
    )
    key2 = build_dedup_key(
        source_name="s",
        persona_id="p",
        task_type="survey",
        source_url="https://x.co",
        normalized_title="title",
        normalized_snippet="snippet",
        stable_metadata="meta",
        provider_task_id=None,
    )
    assert key1 == key2
