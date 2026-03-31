"""Tests for MemoryService (WI 6.1)."""

from datetime import UTC

import pytest

from soren.services.database import DatabaseManager
from soren.services.memory_service import MemoryEntry, MemoryService


@pytest.fixture
async def db(tmp_path):
    """Create and initialize a temporary database."""
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(db_path)
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
def memory_service(db):
    """Create MemoryService with db fixture."""
    return MemoryService(db)


@pytest.mark.asyncio
async def test_store_creates_new_memory_entry(memory_service):
    """store() creates a new memory entry in the database."""
    entry_id = await memory_service.store(
        key="prolific:survey:login",
        value={"summary": "Login button is top-right"},
        entry_type="cycle",
        importance=0.7,
    )
    assert entry_id
    recalled = await memory_service.recall(key="prolific:survey:login")
    assert len(recalled) == 1
    assert recalled[0].key == "prolific:survey:login"
    assert recalled[0].value.get("summary") == "Login button is top-right"
    assert recalled[0].entry_type == "cycle"
    assert recalled[0].importance == 0.7


@pytest.mark.asyncio
async def test_store_with_existing_key_updates_entry(memory_service):
    """store() with existing key updates the entry."""
    await memory_service.store(
        key="prolific:survey:login",
        value={"summary": "Original"},
        entry_type="cycle",
    )
    entry_id = await memory_service.store(
        key="prolific:survey:login",
        value={"summary": "Updated"},
        entry_type="cycle",
    )
    recalled = await memory_service.recall(key="prolific:survey:login")
    assert len(recalled) == 1
    assert recalled[0].value.get("summary") == "Updated"
    assert recalled[0].id == entry_id


@pytest.mark.asyncio
async def test_recall_by_key_returns_matching_entries(memory_service):
    """recall() by key returns matching entries."""
    await memory_service.store("key-a", {"x": 1}, entry_type="cycle")
    await memory_service.store("key-b", {"x": 2}, entry_type="cycle")
    result = await memory_service.recall(key="key-a")
    assert len(result) == 1
    assert result[0].key == "key-a"
    assert result[0].value == {"x": 1}


@pytest.mark.asyncio
async def test_recall_by_entry_type_filters_correctly(memory_service):
    """recall() by entry_type filters correctly."""
    await memory_service.store("k1", {"x": 1}, entry_type="cycle")
    await memory_service.store("k2", {"x": 2}, entry_type="long_term")
    cycle_only = await memory_service.recall(entry_type="cycle")
    long_only = await memory_service.recall(entry_type="long_term")
    assert all(e.entry_type == "cycle" for e in cycle_only)
    assert all(e.entry_type == "long_term" for e in long_only)
    assert len(cycle_only) >= 1
    assert len(long_only) >= 1


@pytest.mark.asyncio
async def test_recall_respects_min_importance_threshold(memory_service):
    """recall() respects min_importance threshold."""
    await memory_service.store("low", {"x": 1}, entry_type="cycle", importance=0.2)
    await memory_service.store("high", {"x": 2}, entry_type="cycle", importance=0.8)
    result = await memory_service.recall(min_importance=0.5)
    keys = [e.key for e in result]
    assert "high" in keys
    assert "low" not in keys


@pytest.mark.asyncio
async def test_recall_orders_by_importance_and_recency(memory_service):
    """recall() orders by importance × recency (most relevant first)."""
    await memory_service.store("a", {"x": 1}, entry_type="cycle", importance=0.5)
    await memory_service.store("b", {"x": 2}, entry_type="cycle", importance=0.9)
    await memory_service.store("c", {"x": 3}, entry_type="cycle", importance=0.7)
    result = await memory_service.recall(limit=5)
    assert len(result) <= 5
    # Higher importance should rank higher (all same recency)
    scores = [(e.importance, e.key) for e in result]
    assert scores[0][0] >= scores[1][0] if len(scores) > 1 else True


@pytest.mark.asyncio
async def test_recall_respects_limit_parameter(memory_service):
    """recall() respects limit parameter."""
    for i in range(10):
        await memory_service.store(f"key-{i}", {"i": i}, entry_type="cycle")
    result = await memory_service.recall(limit=3)
    assert len(result) <= 3


@pytest.mark.asyncio
async def test_prune_removes_entries_below_importance_threshold(memory_service):
    """prune() removes entries below importance threshold."""
    await memory_service.store("low", {"x": 1}, entry_type="cycle", importance=0.2)
    await memory_service.store("high", {"x": 2}, entry_type="cycle", importance=0.8)
    deleted = await memory_service.prune(min_importance=0.3)
    assert deleted >= 1
    recalled = await memory_service.recall()
    keys = [e.key for e in recalled]
    assert "high" in keys or "low" not in keys


@pytest.mark.asyncio
async def test_prune_removes_expired_entries(memory_service):
    """prune() removes expired entries (past expires_at)."""
    from datetime import datetime, timedelta

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await memory_service.store(
        "expired",
        {"x": 1},
        entry_type="cycle",
        importance=0.7,
        expires_at=past,
    )
    deleted = await memory_service.prune(min_importance=0.3)
    assert deleted >= 1
    recalled = await memory_service.recall(key="expired")
    assert len(recalled) == 0


@pytest.mark.asyncio
async def test_prune_preserves_entries_above_threshold_and_not_expired(memory_service):
    """prune() preserves entries above threshold and not expired."""
    await memory_service.store("keep", {"x": 1}, entry_type="cycle", importance=0.8)
    await memory_service.prune(min_importance=0.3)
    recalled = await memory_service.recall(key="keep")
    assert len(recalled) == 1
    assert recalled[0].key == "keep"


@pytest.mark.asyncio
async def test_prune_preserves_long_term_entries_with_null_expires(memory_service):
    """prune() preserves long-term entries with expires_at = NULL."""
    await memory_service.store(
        "long-term",
        {"x": 1},
        entry_type="long_term",
        importance=0.1,
    )
    await memory_service.prune(min_importance=0.3)
    recalled = await memory_service.recall(key="long-term")
    assert len(recalled) == 1
    assert recalled[0].expires_at is None


@pytest.mark.asyncio
async def test_increment_access_updates_access_count_and_last_accessed(memory_service):
    """increment_access() updates access_count and last_accessed_at."""
    entry_id = await memory_service.store("key", {"x": 1}, entry_type="cycle")
    await memory_service.increment_access(entry_id)
    recalled = await memory_service.recall(key="key")
    assert len(recalled) == 1
    assert recalled[0].access_count == 1


@pytest.mark.asyncio
async def test_session_memory_stored_in_memory_not_persisted(memory_service):
    """Session memory: stored in-memory, not persisted to DB."""
    await memory_service.store(
        "session-key",
        {"x": 1},
        entry_type="session",
    )
    recalled = await memory_service.recall(key="session-key", entry_type="session")
    assert len(recalled) == 1
    assert recalled[0].entry_type == "session"
    # Persisted recall should not find it (session is in-memory only)
    cursor = await memory_service._db.execute(
        "SELECT COUNT(*) as n FROM memory_entries WHERE key = ?", ("session-key",)
    )
    row = await cursor.fetchone()
    assert row["n"] == 0


@pytest.mark.asyncio
async def test_cycle_memory_stored_with_30_day_expiration(memory_service):
    """Cycle memory: stored with 30-day expiration."""
    await memory_service.store("cycle-key", {"x": 1}, entry_type="cycle")
    recalled = await memory_service.recall(key="cycle-key")
    assert len(recalled) == 1
    assert recalled[0].expires_at is not None
    # expires_at should be ~30 days in future
    from datetime import datetime

    exp = datetime.fromisoformat(recalled[0].expires_at.replace("Z", "+00:00"))
    now = datetime.now(exp.tzinfo)
    days = (exp - now).days
    assert 20 <= days <= 31


def test_memory_entry_to_prompt_line_format():
    """MemoryEntry.to_prompt_line() produces 'From previous experience: {key}: {value}'."""
    entry = MemoryEntry(
        id="id",
        key="prolific:survey:login",
        value={"summary": "Login button is top-right"},
        entry_type="cycle",
        importance=0.5,
        access_count=0,
        created_at="",
        last_accessed_at="",
        expires_at=None,
    )
    line = entry.to_prompt_line()
    assert "From previous experience: prolific:survey:login: Login button is top-right" in line
