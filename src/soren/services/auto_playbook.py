"""Auto-playbook generation from successful execution step logs (Task 012).

Converts step logs into structured FlowPlaybook and HostPlaybook objects via
LLM summarization. Extends the existing PlaybookService.write_back() pattern
described in discovery document Section 5.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

from soren.models.host_playbook import HostPlaybook
from soren.models.site_playbook import FlowPlaybook, PlaybookStep

if TYPE_CHECKING:
    from soren.services.llm_service import LLMService

logger = logging.getLogger(__name__)

_CREDENTIAL_KEYWORDS = re.compile(
    r"\b(login|password|username|email|sign.?in|credential|auth)\b",
    re.IGNORECASE,
)

_NUMBERED_STEP = re.compile(r"^\d+\.\s+(.+)$", re.MULTILINE)


def _format_steps_for_llm(step_log: list[dict[str, Any]]) -> str:
    """Format step log into readable text for LLM consumption.

    Mirrors PlaybookService._format_steps_for_llm() pattern.
    """
    lines: list[str] = []
    for i, s in enumerate(step_log, 1):
        action = s.get("action_type", "unknown")
        data = s.get("action_data", {})
        reasoning = s.get("vlm_reasoning", "")
        lines.append(f"Step {i}: {action}")
        if data:
            lines.append(f"  Data: {data}")
        if reasoning:
            lines.append(f"  Reasoning: {reasoning[:300]}")
    return "\n".join(lines) if lines else "No steps recorded."


def _detect_credential_field(instruction: str) -> str | None:
    """Return a credential placeholder marker if the instruction mentions credential-related actions."""
    lower = instruction.lower()
    if _CREDENTIAL_KEYWORDS.search(lower):
        if "password" in lower:
            return "credential:password"
        if "username" in lower or "user" in lower:
            return "credential:username"
        if "email" in lower:
            return "credential:email"
        if "login" in lower or "sign" in lower:
            return "credential:login"
    return None


def _parse_steps_from_llm(response: str) -> list[PlaybookStep]:
    """Parse LLM output into PlaybookStep objects.

    Tries YAML parsing first, then falls back to numbered list parsing.
    Returns empty list on parse failure.
    """
    # Try YAML first
    try:
        parsed = yaml.safe_load(response)
        if isinstance(parsed, list):
            steps: list[PlaybookStep] = []
            for i, item in enumerate(parsed, 1):
                if isinstance(item, dict):
                    instruction = str(item.get("instruction", item.get("step", "")))
                    expected_after = str(item.get("expected_after", ""))
                    field = item.get("field")
                    triggers_new_tab = bool(item.get("triggers_new_tab", False))
                    if not field:
                        field = _detect_credential_field(instruction)
                    steps.append(
                        PlaybookStep(
                            seq=item.get("seq", i),
                            instruction=instruction,
                            expected_after=expected_after,
                            field=field,
                            triggers_new_tab=triggers_new_tab,
                        )
                    )
            if steps:
                return steps
    except (yaml.YAMLError, TypeError, AttributeError):
        pass

    # Fallback: numbered list parsing
    matches = _NUMBERED_STEP.findall(response)
    if matches:
        steps = []
        for i, text in enumerate(matches, 1):
            field = _detect_credential_field(text)
            steps.append(
                PlaybookStep(
                    seq=i,
                    instruction=text.strip(),
                    expected_after="",
                    field=field,
                )
            )
        return steps

    return []


_SITE_PROMPT_TEMPLATE = """\
You are converting a successful task execution log into a reusable playbook.

Source: {source_name} ({source_url})
Flow type: {flow_type}
{task_type_line}

Execution steps:
{steps_text}

Produce a YAML list of playbook steps. Each step must have:
- seq: (integer, sequential starting from 1)
- instruction: (imperative text describing the action)
- expected_after: (what the screen should show after this step)
- field: (credential placeholder like "credential:password" if this step involves entering credentials, otherwise null)
- triggers_new_tab: (true if this action opens a new browser tab, otherwise false)

Output ONLY the YAML list, no extra commentary."""


async def auto_generate_site_playbook(
    llm: LLMService,
    step_log: list[dict[str, Any]],
    source_name: str,
    source_url: str,
    flow_type: str,
    task_type: str | None = None,
    model: str | None = None,
) -> FlowPlaybook:
    """Generate a site-level FlowPlaybook from execution step log via LLM."""
    steps_text = _format_steps_for_llm(step_log)
    task_type_line = f"Task type: {task_type}" if task_type else ""

    prompt = _SITE_PROMPT_TEMPLATE.format(
        source_name=source_name,
        source_url=source_url,
        flow_type=flow_type,
        task_type_line=task_type_line,
        steps_text=steps_text,
    )

    try:
        response = await llm.generate(prompt=prompt, model=model)
        steps = _parse_steps_from_llm(response)
    except Exception:
        logger.exception("LLM generation failed during site playbook auto-generation")
        steps = []

    return FlowPlaybook(
        verified_by="auto",
        confidence=0.0,
        steps=steps,
        max_steps=len(steps) + 5 if steps else 20,
    )


_HOST_PROMPT_TEMPLATE = """\
You are analyzing a successful task execution log to extract host-specific UI patterns.

Host domain: {host_domain}

Execution steps:
{steps_text}

Identify what is specific to the host UI (navigation quirks, layout patterns, \
platform-specific behaviors) vs. the particular task being performed.

Produce a YAML mapping with two keys:
- navigation: a mapping of navigation element names to descriptions
- quirks: a list of strings describing host-specific UI behaviors

Output ONLY the YAML mapping, no extra commentary."""


async def auto_generate_host_playbook(
    llm: LLMService,
    step_log: list[dict[str, Any]],
    host_domain: str,
    model: str | None = None,
) -> HostPlaybook:
    """Generate a host-level playbook from execution step log via LLM."""
    steps_text = _format_steps_for_llm(step_log)

    prompt = _HOST_PROMPT_TEMPLATE.format(
        host_domain=host_domain,
        steps_text=steps_text,
    )

    navigation: dict[str, Any] = {}
    quirks: list[str] = []

    try:
        response = await llm.generate(prompt=prompt, model=model)
        parsed = yaml.safe_load(response)
        if isinstance(parsed, dict):
            navigation = parsed.get("navigation", {}) or {}
            raw_quirks = parsed.get("quirks", []) or []
            quirks = [str(q) for q in raw_quirks if q]
    except Exception:
        logger.exception("LLM generation failed during host playbook auto-generation")

    return HostPlaybook(
        host_domain=host_domain,
        learned_from="auto",
        confidence=0.0,
        navigation=navigation,
        quirks=quirks,
    )
