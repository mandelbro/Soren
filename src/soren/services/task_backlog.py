"""Task backlog service: CRUD, deduplication, and status transitions."""

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from soren.models.session import BacklogSummary, DiscoveredTask, InsertResult
from soren.models.task import Task
from soren.services.database import DatabaseManager, _iso_now, _json_dumps, _row_to_task
from soren.services.retry_policy import NON_RETRYABLE_FAILURES

DEFAULT_RETRY_BACKOFF_MINUTES = [5, 30, 120]


def normalize_title(title: str) -> str:
    """Lowercase, collapse whitespace, strip dynamic fragments (e.g. '5 left', 'NEW')."""
    if not title:
        return ""
    s = title.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*(?:\d+\s*left|new|!\s*)$", "", s, flags=re.IGNORECASE)
    return s.strip()


def normalize_snippet(text: str, max_chars: int = 160) -> str:
    """Lowercase, collapse whitespace, truncate."""
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s[:max_chars]


def build_dedup_key(
    source_name: str,
    persona_id: str,
    task_type: str,
    source_url: str,
    normalized_title: str,
    normalized_snippet: str,
    stable_metadata: str,
    provider_task_id: str | None = None,
) -> str:
    """Build tiered dedup key. Primary: provider_task_id. Fallback: content fingerprint."""
    if provider_task_id:
        return f"{source_name}:{provider_task_id}:{persona_id}"
    payload = f"{task_type}+{source_url}+{normalized_title}+{normalized_snippet}+{stable_metadata}"
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    return f"{source_name}:{fingerprint}:{persona_id}"


class TaskBacklog:
    """Manages the task queue with tiered deduplication and status transitions."""

    def __init__(
        self,
        db: DatabaseManager,
        retry_backoff_minutes: list[int] | None = None,
    ):
        self._db = db
        self._backoff = retry_backoff_minutes or DEFAULT_RETRY_BACKOFF_MINUTES

    async def insert_discovered(self, tasks: list[DiscoveredTask]) -> InsertResult:
        """Batch insert with dedup. Returns counts (inserted, skipped, errors)."""
        result = InsertResult()
        if not tasks:
            return result

        now = _iso_now()
        rows: list[tuple] = []

        for t in tasks:
            try:
                dt = t if isinstance(t, DiscoveredTask) else DiscoveredTask.model_validate(t)
            except Exception:
                result.errors += 1
                continue

            nt = normalize_title(dt.title)
            ns = normalize_snippet(dt.snippet or dt.description)
            dedup_key = build_dedup_key(
                source_name=dt.source_name,
                persona_id=dt.persona_id,
                task_type=dt.task_type,
                source_url=dt.source_url,
                normalized_title=nt,
                normalized_snippet=ns,
                stable_metadata=dt.stable_metadata,
                provider_task_id=dt.provider_task_id,
            )
            task_id = str(uuid.uuid4())
            rows.append(
                (
                    task_id,
                    dedup_key,
                    dt.title or "",
                    dt.description or "",
                    dt.task_type or "survey",
                    dt.source_name or "",
                    dt.source_type or "",
                    dt.source_url or "",
                    dt.provider_task_id,
                    dt.persona_id or "",
                    "queued",
                    dt.priority,
                    dt.estimated_duration_minutes,
                    dt.estimated_reward,
                    0,
                    3,
                    None,
                    None,
                    None,
                    now,
                    now,
                    None,
                    None,
                )
            )

        sql = """
            INSERT INTO tasks (
                id, dedup_key, title, description, task_type, source_name, source_type,
                source_url, provider_task_id, persona_id, status, priority,
                estimated_duration_minutes, estimated_reward, attempt_count, max_retries,
                next_eligible_at, last_failure_reason, result_data, discovered_at, created_at,
                started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedup_key) DO NOTHING
        """
        conn = await self._db.get_connection()
        for row in rows:
            cursor = await conn.execute(sql, row)
            if cursor.rowcount > 0:
                result.inserted += 1
            else:
                result.skipped += 1
        await conn.commit()
        return result

    async def get_eligible_tasks(self, persona_id: str | None = None) -> list[Task]:
        """Query queued tasks, excluding dead_letter and future next_eligible_at."""
        sql = """
            SELECT * FROM tasks
            WHERE status = 'queued'
            AND (next_eligible_at IS NULL OR next_eligible_at <= datetime('now'))
        """
        params: tuple = ()
        if persona_id:
            sql += " AND persona_id = ?"
            params = (persona_id,)
        sql += " ORDER BY priority DESC, created_at ASC"

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [Task.model_validate(_row_to_task(r)) for r in rows]

    async def mark_in_progress(self, task_id: str) -> None:
        """Set status = in_progress, started_at = now()."""
        now = _iso_now()
        await self._db.execute(
            "UPDATE tasks SET status = 'in_progress', started_at = ? WHERE id = ?",
            (now, task_id),
        )
        await self._db.commit()

    async def mark_completed(self, task_id: str, result_data: dict | None = None) -> None:
        """Set status = completed, completed_at = now(), result_data."""
        now = _iso_now()
        await self._db.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ?, result_data = ? WHERE id = ?",
            (now, _json_dumps(result_data), task_id),
        )
        await self._db.commit()

    async def mark_failed(self, task_id: str, reason: str, backoff_minutes: list[int] | None = None) -> None:
        """Increment attempt_count, set next_eligible_at from backoff, or promote to dead_letter.

        Non-retryable reasons (e.g. ``config_error``) are dead-lettered immediately.
        """
        backoff = backoff_minutes or self._backoff
        cursor = await self._db.execute("SELECT attempt_count, max_retries FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        if not row:
            return

        attempt = (row["attempt_count"] or 0) + 1
        max_retries = row["max_retries"] or 3

        if attempt >= max_retries or reason in NON_RETRYABLE_FAILURES:
            await self._db.execute(
                "UPDATE tasks SET status = 'dead_letter', attempt_count = ?, last_failure_reason = ? WHERE id = ?",
                (attempt, reason, task_id),
            )
        else:
            mins = backoff[min(attempt - 1, len(backoff) - 1)]
            next_at = (datetime.now(UTC) + timedelta(minutes=mins)).isoformat()
            await self._db.execute(
                "UPDATE tasks SET status = 'queued', attempt_count = ?, "
                "next_eligible_at = ?, last_failure_reason = ? WHERE id = ?",
                (attempt, next_at, reason, task_id),
            )
        await self._db.commit()

    async def requeue_task(self, task_id: str) -> None:
        """Reset dead-letter task: status = queued, attempt_count = 0, clear next_eligible_at."""
        await self._db.execute(
            "UPDATE tasks SET status = 'queued', attempt_count = 0, next_eligible_at = NULL WHERE id = ?",
            (task_id,),
        )
        await self._db.commit()

    async def get_summary(self) -> BacklogSummary:
        """Return counts by status and persona."""
        cursor = await self._db.execute(
            "SELECT status, persona_id, COUNT(*) as cnt FROM tasks GROUP BY status, persona_id"
        )
        rows = await cursor.fetchall()

        total = queued = in_progress = completed = dead_letter = 0
        by_persona: dict[str, int] = {}
        by_source: dict[str, int] = {}

        for r in rows:
            cnt = r["cnt"]
            status = r["status"] or ""
            persona = r["persona_id"] or ""
            total += cnt
            if status == "queued":
                queued += cnt
            elif status == "in_progress":
                in_progress += cnt
            elif status == "completed":
                completed += cnt
            elif status == "dead_letter":
                dead_letter += cnt
            if persona:
                by_persona[persona] = by_persona.get(persona, 0) + cnt

        return BacklogSummary(
            total=total,
            queued=queued,
            in_progress=in_progress,
            completed=completed,
            dead_letter=dead_letter,
            by_persona=by_persona,
            by_source=by_source,
        )

    async def clear_all(self) -> int:
        """Delete all tasks, return count."""
        cursor = await self._db.execute("DELETE FROM tasks")
        await self._db.commit()
        return cursor.rowcount

    async def list_tasks(
        self,
        persona_id: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        """List tasks with optional filters."""
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: list = []
        if persona_id:
            sql += " AND persona_id = ?"
            params.append(persona_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if task_type:
            sql += " AND task_type = ?"
            params.append(task_type)
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        cursor = await self._db.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [Task.model_validate(_row_to_task(r)) for r in rows]

    async def requeue_all_dead_letter(self) -> int:
        """Requeue all dead-letter tasks. Return count requeued."""
        cursor = await self._db.execute(
            "UPDATE tasks SET status = 'queued', attempt_count = 0, "
            "next_eligible_at = NULL WHERE status = 'dead_letter'"
        )
        await self._db.commit()
        return cursor.rowcount


def get_default_db_path() -> Path:
    """Return default database path (data/soren.db relative to project root)."""
    return Path(__file__).resolve().parents[3] / "data" / "soren.db"
