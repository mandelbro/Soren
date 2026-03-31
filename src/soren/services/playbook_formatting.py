"""Playbook prompt formatting helpers for CUA agent instruction generation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import yaml

from soren.models.site_playbook import FlowPlaybook


def serialize_for_yaml(obj: Any) -> Any:
    """Convert datetime objects to ISO strings for YAML serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialize_for_yaml(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_for_yaml(v) for v in obj]
    return obj


def flow_to_prompt_instructions(
    flow: FlowPlaybook,
    flow_type: str,
    credentials: Any | None = None,
) -> str:
    """Convert FlowPlaybook steps to numbered imperative instructions for CUA agent.

    Handles credential placeholders, tab transitions, host delegation, and platform returns.
    """
    lines: list[str] = []
    for step in flow.steps:
        instruction = step.instruction
        parts: list[str] = []

        # Main instruction with expected outcome
        line = f"{step.seq}. {instruction}"
        if step.expected_after:
            line += f" (expect: {step.expected_after})"
        parts.append(line)

        # Credential placeholder
        if step.field and step.field.startswith("credential:"):
            cred_key = step.field.split(":", 1)[1]
            parts.append(f"   [CREDENTIAL: use {cred_key} from credential handoff]")

        # Tab transition
        if step.triggers_new_tab:
            domain = step.host_domain or "unknown"
            parts.append(f"   [TAB: new tab expected at {domain}]")

        # Delegate to host
        if instruction == "DELEGATE_TO_HOST":
            domain = step.host_domain or "unknown"
            parts.append(f"   [DELEGATE: switch to host playbook for {domain}]")

        # Return to platform
        if step.returns_to_platform:
            parts.append("   [RETURN: switch back to platform tab]")

        lines.append("\n".join(parts))

    return "\n".join(lines)


def format_host_content(navigation: dict, quirks: list[str]) -> str | None:
    """Format host playbook content for cascading resolution output."""
    parts: list[str] = []
    if navigation:
        parts.append(yaml.dump(navigation, default_flow_style=False).strip())
    if quirks:
        parts.append("Quirks:\n" + "\n".join(f"- {q}" for q in quirks))
    return "\n".join(parts) if parts else None


def format_base_content(patterns: dict) -> str | None:
    """Format base playbook patterns for cascading resolution output."""
    if not patterns:
        return None
    return yaml.dump(patterns, default_flow_style=False).strip()
