"""Session and cycle report models."""

from typing import Any

from pydantic import BaseModel, Field


class InsertResult(BaseModel):
    """Result of batch insert into task backlog."""

    inserted: int = 0
    skipped: int = 0
    errors: int = 0

    model_config = {"from_attributes": True, "extra": "ignore"}


class DiscoverySummary(BaseModel):
    """Summary of discovery-only run (refresh_tasks)."""

    total_inserted: int = 0
    total_skipped: int = 0
    personas_processed: int = 0
    source_count: int = 0

    model_config = {"from_attributes": True, "extra": "ignore"}


class DiscoveredTask(BaseModel):
    """Task discovered from an external source (pre-insert)."""

    title: str = ""
    description: str = ""
    task_type: str = "survey"
    source_name: str = ""
    source_type: str = ""
    source_url: str = ""
    provider_task_id: str | None = None
    persona_id: str = ""
    priority: int = Field(default=5, ge=0, le=10)
    estimated_duration_minutes: int | None = None
    estimated_reward: float | None = None
    snippet: str = ""
    stable_metadata: str = ""

    model_config = {"from_attributes": True, "extra": "ignore"}


class TaskResult(BaseModel):
    """Result of a single task execution."""

    success: bool
    steps_taken: int = 0
    step_log: list[dict[str, Any]] = Field(default_factory=list)
    screenshot_paths: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None
    verification_passed: bool | None = None
    correction_applied: str | None = None

    model_config = {"from_attributes": True, "extra": "ignore"}


class BacklogSummary(BaseModel):
    """Snapshot of backlog state."""

    total: int = 0
    queued: int = 0
    in_progress: int = 0
    completed: int = 0
    dead_letter: int = 0
    by_persona: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)

    model_config = {"from_attributes": True, "extra": "ignore"}


class CycleReport(BaseModel):
    """Report for a single Soren cycle."""

    cycle_id: str
    cycle_type: str = "execute"
    started_at: str = ""
    completed_at: str = ""
    total_duration_seconds: float = 0.0
    personas_used: list[str] = Field(default_factory=list)
    discovery_ran: bool = False
    discovery_summary: dict[str, Any] | None = None
    task_executed_id: str | None = None
    task_result: TaskResult | None = None
    backlog_snapshot: BacklogSummary | None = None
    memory_entries_created: int = 0
    memory_entries_pruned: int = 0
    vm_name: str = ""
    vm_boot_seconds: float = 0.0
    total_agent_steps: int = 0
    total_screenshots_captured: int = 0
    model_used: str | None = None
    fallback_triggered: bool = False
    memory_metrics: dict[str, Any] | None = None
    screenshots: list[str] = Field(default_factory=list)
    playbook_preview: str | None = None

    model_config = {"from_attributes": True, "extra": "ignore"}
