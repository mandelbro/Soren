"""Site playbook Pydantic models and schema versioning.

Defines the per-source playbook data structure with three flow types
(login, discovery, execution) and a migration registry for schema evolution.
See discovery document Sections 6.3 and 17.2.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


# --- Exceptions ---


class PlaybookVersionError(Exception):
    """Raised when a playbook file version is unsupported or missing migration path."""


class PlaybookValidationError(Exception):
    """Raised on content validation failure (credential leaks, sequence gaps)."""


# --- Models ---


class PlaybookStep(BaseModel):
    """A single step in a flow playbook."""

    seq: int
    instruction: str
    expected_after: str
    field: str | None = None
    screenshot_ref: str | None = None
    triggers_new_tab: bool = False
    host_domain: str | None = None
    returns_to_platform: bool = False

    model_config = {"from_attributes": True}


class FlowPlaybook(BaseModel):
    """Base class for flow-specific playbooks."""

    verified_at: datetime | None = None
    verified_by: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    consecutive_successes: int = 0
    max_steps: int = Field(default=20, ge=1)
    steps: list[PlaybookStep]

    model_config = {"from_attributes": True}


class LoginFlow(FlowPlaybook):
    """Login flow with auth indicators and failure fallback."""

    auth_complete_indicator: str = ""
    fallback_on_failure: Literal["stop_and_report", "generic"] = "stop_and_report"


class DiscoveryFlow(FlowPlaybook):
    """Discovery flow with readiness indicator."""

    discovery_ready_indicator: str = ""


class ExecutionFlow(FlowPlaybook):
    """Task execution flow with completion indicator."""

    completion_indicator: str = ""


class SiteFlows(BaseModel):
    """Container for all flow types within a site playbook."""

    login: LoginFlow | None = None
    discovery: DiscoveryFlow | None = None
    execution: dict[str, ExecutionFlow] | None = None

    model_config = {"from_attributes": True}


class SitePlaybook(BaseModel):
    """Per-source site playbook with three flow types."""

    source_name: str
    source_url: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    version: int = SCHEMA_VERSION
    flows: SiteFlows

    model_config = {"from_attributes": True}


# --- Migration Registry (Section 17.2) ---

_MIGRATIONS: dict[tuple[int, int], Callable[[dict], dict]] = {}

_CREDENTIAL_PATTERN = re.compile(r"credential:\w+")


def _register_migration(from_v: int, to_v: int) -> Callable:
    """Decorator to register a migration function."""

    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        _MIGRATIONS[(from_v, to_v)] = fn
        return fn

    return decorator


def _migrate(raw: dict, from_version: int, to_version: int) -> dict:
    """Apply sequential migrations from from_version to to_version."""
    current = from_version
    while current < to_version:
        next_v = current + 1
        migration = _MIGRATIONS.get((current, next_v))
        if migration is None:
            msg = f"No migration path from v{current} to v{next_v}."
            raise PlaybookVersionError(msg)
        raw = migration(raw)
        raw["version"] = next_v
        current = next_v
    return raw


# --- Content Validation ---


def _iter_flow_steps(raw: dict) -> list[tuple[str, dict]]:
    """Yield (flow_name, step_dict) for every step in the raw playbook."""
    flows = raw.get("flows", {})
    results: list[tuple[str, dict]] = []
    for flow_name in ("login", "discovery"):
        flow_data = flows.get(flow_name)
        if flow_data and "steps" in flow_data:
            for step in flow_data["steps"]:
                results.append((flow_name, step))
    execution = flows.get("execution")
    if isinstance(execution, dict):
        for task_type, flow_data in execution.items():
            if isinstance(flow_data, dict) and "steps" in flow_data:
                for step in flow_data["steps"]:
                    results.append((f"execution.{task_type}", step))
    return results


def validate_credential_placeholders(raw: dict) -> list[str]:
    """Check that credential placeholders only appear in 'field', not in 'instruction' or 'expected_after'."""
    violations: list[str] = []
    for flow_name, step in _iter_flow_steps(raw):
        seq = step.get("seq", "?")
        for forbidden_field in ("instruction", "expected_after"):
            value = step.get(forbidden_field, "")
            if isinstance(value, str) and _CREDENTIAL_PATTERN.search(value):
                violations.append(f"{flow_name} step {seq}: credential placeholder in '{forbidden_field}'")
    return violations


def validate_step_sequences(raw: dict) -> list[str]:
    """Check that seq values within each flow's steps are contiguous (no gaps)."""
    issues: list[str] = []
    flows = raw.get("flows", {})

    def _check_flow(flow_name: str, flow_data: dict) -> None:
        steps = flow_data.get("steps", [])
        if not steps:
            return
        seqs = sorted(s.get("seq", 0) for s in steps)
        for i in range(1, len(seqs)):
            if seqs[i] != seqs[i - 1] + 1:
                issues.append(f"{flow_name}: non-contiguous seq values {seqs}")
                return

    for name in ("login", "discovery"):
        flow_data = flows.get(name)
        if isinstance(flow_data, dict):
            _check_flow(name, flow_data)

    execution = flows.get("execution")
    if isinstance(execution, dict):
        for task_type, flow_data in execution.items():
            if isinstance(flow_data, dict):
                _check_flow(f"execution.{task_type}", flow_data)

    return issues


# --- Load & Validate ---


def load_and_validate(path: Path) -> SitePlaybook:
    """Load YAML, check version, migrate if needed, validate content, return model."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a YAML mapping, got {type(raw).__name__}"
        raise PlaybookValidationError(msg)

    file_version = raw.get("version", 1)

    if file_version > SCHEMA_VERSION:
        msg = (
            f"{path}: file version {file_version} is newer than "
            f"SCHEMA_VERSION {SCHEMA_VERSION}. Upgrade Soren to read this file."
        )
        raise PlaybookVersionError(msg)

    if file_version < SCHEMA_VERSION:
        raw = _migrate(raw, file_version, SCHEMA_VERSION)
        path.write_text(
            yaml.dump(raw, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    # Content validation
    cred_issues = validate_credential_placeholders(raw)
    if cred_issues:
        msg = f"{path}: credential placeholder violations: {'; '.join(cred_issues)}"
        raise PlaybookValidationError(msg)

    seq_issues = validate_step_sequences(raw)
    if seq_issues:
        msg = f"{path}: step sequence issues: {'; '.join(seq_issues)}"
        raise PlaybookValidationError(msg)

    return SitePlaybook.model_validate(raw)
