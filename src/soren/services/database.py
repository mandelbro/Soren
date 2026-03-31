"""SQLite database layer with version-tracked migrations."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite


class DatabaseManager:
    """Manages SQLite database initialization and migrations."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create DB directory, enable WAL and foreign keys, run pending migrations."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=wal")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.commit()
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        """Apply pending migrations in order."""
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        await self._conn.commit()

        migrations_dir = Path(__file__).parent / "migrations"
        if not migrations_dir.exists():
            return

        files = sorted(migrations_dir.glob("*.sql"))
        for f in files:
            version = int(f.stem.split("_")[0])
            cursor = await self._conn.execute("SELECT 1 FROM schema_version WHERE version = ?", (version,))
            if await cursor.fetchone():
                continue

            sql = f.read_text()
            await self._conn.executescript(sql)
            await self._conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
            await self._conn.commit()

    async def get_connection(self) -> aiosqlite.Connection:
        """Return the database connection. Call initialize() first."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, parameters: tuple = ()) -> aiosqlite.Cursor:
        """Execute a single statement. Convenience for TaskBacklog."""
        conn = await self.get_connection()
        return await conn.execute(sql, parameters)

    async def executemany(self, sql: str, parameters: list[tuple]) -> None:
        """Execute many statements."""
        conn = await self.get_connection()
        await conn.executemany(sql, parameters)
        await conn.commit()

    async def commit(self) -> None:
        """Commit pending changes."""
        if self._conn:
            await self._conn.commit()


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(obj: dict | list | None) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj)


def _json_loads(s: str | None) -> dict | list | None:
    if s is None or s == "":
        return None
    return json.loads(s)


def _row_to_task(row: aiosqlite.Row) -> dict:
    """Convert a database row to a Task-compatible dict."""
    return {
        "id": row["id"],
        "dedup_key": row["dedup_key"],
        "title": row["title"] or "",
        "description": row["description"] or "",
        "task_type": row["task_type"] or "survey",
        "source_name": row["source_name"] or "",
        "source_type": row["source_type"] or "",
        "source_url": row["source_url"] or "",
        "provider_task_id": row["provider_task_id"],
        "persona_id": row["persona_id"] or "",
        "status": row["status"] or "queued",
        "priority": row["priority"] or 5,
        "estimated_duration_minutes": row["estimated_duration_minutes"],
        "estimated_reward": row["estimated_reward"],
        "attempt_count": row["attempt_count"] or 0,
        "max_retries": row["max_retries"] or 3,
        "next_eligible_at": row["next_eligible_at"],
        "last_failure_reason": row["last_failure_reason"],
        "result_data": _json_loads(row["result_data"]),
        "discovered_at": row["discovered_at"] or "",
        "created_at": row["created_at"] or "",
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }
