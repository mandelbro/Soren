"""MemoryService — store, recall, and prune memory entries across cycles."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from soren.services.database import DatabaseManager

CYCLE_RETENTION_DAYS = 30


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _iso_future(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


class MemoryEntry:
    """A single memory entry with key, value, and metadata."""

    def __init__(
        self,
        id: str,
        key: str,
        value: dict,
        entry_type: str,
        importance: float,
        access_count: int,
        created_at: str,
        last_accessed_at: str,
        expires_at: str | None,
    ) -> None:
        self.id = id
        self.key = key
        self.value = value
        self.entry_type = entry_type
        self.importance = importance
        self.access_count = access_count
        self.created_at = created_at
        self.last_accessed_at = last_accessed_at
        self.expires_at = expires_at

    def to_prompt_line(self) -> str:
        """Format for PersonaContext: 'From previous experience: {key}: {value}'."""
        summary = self.value.get("summary", json.dumps(self.value))
        return f"From previous experience: {self.key}: {summary}"


class MemoryService:
    """Manages memory_entries table with three tiers: session, cycle, long-term."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        self._session: dict[str, MemoryEntry] = {}

    async def store(
        self,
        key: str,
        value: dict,
        entry_type: str = "cycle",
        importance: float = 0.5,
        expires_at: str | None = None,
    ) -> str:
        """Insert or update a memory entry. Returns entry ID.

        Tiers:
        - session: in-memory only, never persisted
        - cycle: persisted with expires_at = now + 30 days
        - long_term: persisted with expires_at = NULL
        """
        now = _iso_now()
        if entry_type == "session":
            entry_id = str(uuid.uuid4())
            self._session[key] = MemoryEntry(
                id=entry_id,
                key=key,
                value=value,
                entry_type=entry_type,
                importance=importance,
                access_count=0,
                created_at=now,
                last_accessed_at=now,
                expires_at=None,
            )
            return entry_id

        if entry_type == "cycle" and expires_at is None:
            expires_at = _iso_future(CYCLE_RETENTION_DAYS)
        elif entry_type == "long_term":
            expires_at = None

        value_json = json.dumps(value)

        # Upsert: update if key+entry_type exists, else insert
        cursor = await self._db.execute(
            "SELECT id FROM memory_entries WHERE key = ? AND entry_type = ? LIMIT 1",
            (key, entry_type),
        )
        row = await cursor.fetchone()
        if row:
            entry_id = row["id"]
            await self._db.execute(
                """
                UPDATE memory_entries
                SET value = ?, importance = ?, last_accessed_at = ?, expires_at = ?
                WHERE id = ?
                """,
                (value_json, importance, now, expires_at, entry_id),
            )
        else:
            entry_id = str(uuid.uuid4())
            await self._db.execute(
                """
                INSERT INTO memory_entries (
                    id, key, value, entry_type, importance, access_count,
                    created_at, last_accessed_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (entry_id, key, value_json, entry_type, importance, now, now, expires_at),
            )
        await self._db.commit()
        return entry_id

    async def recall(
        self,
        key: str | None = None,
        entry_type: str | None = None,
        limit: int = 10,
        min_importance: float = 0.0,
    ) -> list[MemoryEntry]:
        """Query memories matching criteria, ordered by importance × recency."""
        entries: list[MemoryEntry] = []

        # Session memories (in-memory)
        if entry_type is None or entry_type == "session":
            for e in self._session.values():
                if key is None or e.key == key:
                    if e.importance >= min_importance:
                        entries.append(e)

        # Persisted memories
        conditions = ["1=1"]
        params: list[object] = []
        if key is not None:
            conditions.append("key = ?")
            params.append(key)
        if entry_type is not None:
            conditions.append("entry_type = ?")
            params.append(entry_type)
        conditions.append("importance >= ?")
        params.append(min_importance)
        conditions.append("(expires_at IS NULL OR expires_at > ?)")
        params.append(_iso_now())

        where = " AND ".join(conditions)
        cursor = await self._db.execute(
            f"""
            SELECT id, key, value, entry_type, importance, access_count, created_at, last_accessed_at, expires_at
            FROM memory_entries
            WHERE {where}
            ORDER BY last_accessed_at DESC
            LIMIT ?
            """,
            (*params, limit * 2),
        )
        rows = await cursor.fetchall()

        for row in rows:
            try:
                val = json.loads(row["value"]) if row["value"] else {}
            except (json.JSONDecodeError, TypeError):
                val = {}
            entries.append(
                MemoryEntry(
                    id=row["id"],
                    key=row["key"] or "",
                    value=val,
                    entry_type=row["entry_type"] or "cycle",
                    importance=float(row["importance"] or 0.5),
                    access_count=int(row["access_count"] or 0),
                    created_at=row["created_at"] or "",
                    last_accessed_at=row["last_accessed_at"] or "",
                    expires_at=row["expires_at"],
                )
            )

        # Apply importance decay and reorder by effective_importance × recency
        now = datetime.now(UTC)
        scored: list[tuple[float, MemoryEntry]] = []
        for e in entries:
            try:
                last = datetime.fromisoformat(e.last_accessed_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                last = now
            days_since = (now - last).days
            age_factor = min(0.9, days_since / 365.0)
            effective = e.importance * (1.0 - age_factor)
            scored.append((effective, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    async def prune(self, min_importance: float = 0.3) -> int:
        """Delete expired entries and entries below importance threshold. Returns count deleted.

        Preserves long-term entries (expires_at = NULL). Only deletes cycle entries that are
        expired or below min_importance.
        """
        now = _iso_now()
        cursor = await self._db.execute(
            """
            DELETE FROM memory_entries
            WHERE expires_at IS NOT NULL
              AND (expires_at <= ? OR importance < ?)
            """,
            (now, min_importance),
        )
        await self._db.commit()
        return cursor.rowcount

    async def increment_access(self, entry_id: str) -> None:
        """Bump access_count and last_accessed_at for recall tracking."""
        now = _iso_now()
        await self._db.execute(
            """
            UPDATE memory_entries
            SET access_count = access_count + 1, last_accessed_at = ?
            WHERE id = ?
            """,
            (now, entry_id),
        )
        await self._db.commit()

    def clear_session(self) -> None:
        """Clear in-memory session memories (call after task completion)."""
        self._session.clear()
