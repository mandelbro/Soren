-- Soren initial schema (Phase 1)
-- tasks.dedup_key UNIQUE for INSERT ... ON CONFLICT(dedup_key) DO NOTHING

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    dedup_key TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    task_type TEXT NOT NULL DEFAULT 'survey',
    source_name TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    provider_task_id TEXT,
    persona_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 5,
    estimated_duration_minutes INTEGER,
    estimated_reward REAL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    next_eligible_at TEXT,
    last_failure_reason TEXT,
    result_data TEXT,
    discovered_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_persona ON tasks(persona_id);
CREATE INDEX IF NOT EXISTS idx_tasks_dedup ON tasks(dedup_key);

CREATE TABLE IF NOT EXISTS task_steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    description TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL DEFAULT 'click',
    action_data TEXT,
    vlm_reasoning TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    screenshot_before TEXT,
    screenshot_after TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL DEFAULT '',
    value TEXT,
    entry_type TEXT NOT NULL DEFAULT 'session',
    importance REAL NOT NULL DEFAULT 0.5,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    last_accessed_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(entry_type);

CREATE TABLE IF NOT EXISTS cycle_reports (
    cycle_id TEXT PRIMARY KEY,
    cycle_type TEXT NOT NULL DEFAULT 'execute',
    started_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    total_duration_seconds REAL NOT NULL DEFAULT 0,
    personas_used TEXT,
    discovery_ran INTEGER NOT NULL DEFAULT 0,
    discovery_summary TEXT,
    task_executed_id TEXT REFERENCES tasks(id),
    task_result TEXT,
    backlog_snapshot TEXT,
    memory_entries_created INTEGER NOT NULL DEFAULT 0,
    memory_entries_pruned INTEGER NOT NULL DEFAULT 0,
    vm_name TEXT NOT NULL DEFAULT '',
    vm_boot_seconds REAL NOT NULL DEFAULT 0,
    total_agent_steps INTEGER NOT NULL DEFAULT 0,
    total_screenshots_captured INTEGER NOT NULL DEFAULT 0
);
