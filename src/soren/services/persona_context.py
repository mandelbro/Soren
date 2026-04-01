"""PersonaContext builder for ComputerAgent instructions."""

from __future__ import annotations

import logging
from collections.abc import Callable

from soren.models.persona import Persona
from soren.models.task import Task

DEFAULT_STEP_BUDGET = 50

logger = logging.getLogger(__name__)

# Big Five trait name -> (low, medium, high) behavioral directives
TRAIT_DIRECTIVES: dict[str, tuple[str, str, str]] = {
    "conscientiousness": (
        "Work efficiently; avoid over-analyzing. Move forward with reasonable choices.",
        "Balance thoroughness with pace. Review key steps before submitting.",
        "Be thorough and detail-oriented; double-check your work before submitting.",
    ),
    "openness": (
        "Stick to straightforward approaches. Follow the task description closely.",
        "Consider alternative approaches when the obvious path is blocked.",
        "Be creative and adaptable; explore novel solutions when standard approaches fail.",
    ),
    "agreeableness": (
        "Prioritize task completion. Be direct and efficient.",
        "Balance cooperation with task goals. Be polite but focused.",
        "Be cooperative and considerate; use polite, friendly language in responses.",
    ),
    "extraversion": (
        "Be reserved and thoughtful. Prefer concise, measured responses.",
        "Balance engagement with efficiency. Be clear but not verbose.",
        "Be engaging and expressive; provide warm, personable responses when appropriate.",
    ),
    "neuroticism": (
        "Stay calm and methodical. Proceed with confidence.",
        "Handle setbacks calmly. Retry or adjust when needed.",
        "Be cautious under uncertainty; verify before acting when the outcome is unclear.",
    ),
}

# Playbook section headers in truncation priority order (lowest priority first).
# Sections matching these prefixes are truncated first when exceeding token limit.
_TRUNCATION_PRIORITY = [
    "## General Guidelines",  # Base playbook (lowest priority)
    "Relevant memories",  # Memory entries
    "## Host Instructions",  # Host playbook
    "## Site-Specific",  # Site playbook (highest priority, truncated last)
]


def _default_token_count(text: str) -> int:
    """Approximate token count using len(text) // 4 heuristic."""
    return len(text) // 4


def _trait_level(value: float) -> str:
    """Map trait score to low/medium/high."""
    if value <= 0.33:
        return "low"
    if value <= 0.66:
        return "medium"
    return "high"


def _build_demographic_section(persona: Persona) -> str:
    """Build natural language identity statement from demographics."""
    parts: list[str] = []
    age = persona.get_demographic("age") or persona.get_demographic("age_range")
    occupation = persona.get_demographic("occupation")
    education = persona.get_demographic("education") or persona.get_demographic("education_level")
    location = persona.get_demographic("location")

    if age:
        parts.append(f"age {age}")
    if occupation:
        parts.append(f"occupation as {occupation}")
    if education:
        parts.append(f"education level {education}")
    if location:
        parts.append(f"based in {location}")

    if parts:
        return f"You are {persona.name}. Identity: {', '.join(parts)}."
    if persona.name:
        return f"You are {persona.name}."
    return ""


def _build_trait_directives(persona: Persona) -> list[str]:
    """Build behavioral directives from Big Five traits."""
    directives: list[str] = []
    for trait_name, (low, medium, high) in TRAIT_DIRECTIVES.items():
        value = persona.get_trait(trait_name)
        level = _trait_level(value)
        if level == "low":
            directives.append(low)
        elif level == "medium":
            directives.append(medium)
        else:
            directives.append(high)
    return directives


def _build_response_style(persona: Persona) -> str:
    """Build response style guidance from preferences."""
    formality = persona.get_preference("formality")
    response_style = persona.get_preference("response_style")
    decision_speed = persona.get_preference("decision_speed")

    parts: list[str] = []
    if formality:
        formality_map = {
            "formal": "Use formal, professional language.",
            "casual": "Use casual, conversational language.",
            "professional": "Use professional but approachable language.",
        }
        parts.append(formality_map.get(formality.lower(), f"Formality: {formality}."))
    if response_style:
        style_map = {
            "concise": "Keep responses brief and to the point.",
            "detailed": "Provide thorough, detailed responses when needed.",
            "balanced": "Balance brevity with completeness.",
        }
        parts.append(style_map.get(response_style.lower(), f"Style: {response_style}."))
    if decision_speed:
        speed_map = {
            "fast": "Make decisions quickly; avoid over-deliberation.",
            "deliberate": "Take time to consider options before acting.",
            "moderate": "Balance speed with careful consideration.",
        }
        parts.append(speed_map.get(decision_speed.lower(), f"Pace: {decision_speed}."))

    return " ".join(parts) if parts else ""


def _truncate_playbook_content(
    playbook_content: str,
    max_tokens: int,
    token_counter: Callable[[str], int],
) -> str:
    """Truncate playbook content by removing lowest-priority sections first.

    Priority order (lowest truncated first):
    1. Base playbook (General Guidelines)
    2. Memory entries
    3. Host playbook (Host Instructions)
    4. Site playbook (Site-Specific) — truncated last

    Returns the truncated content with a marker if truncation occurred.
    """
    current_tokens = token_counter(playbook_content)
    if current_tokens <= max_tokens:
        return playbook_content

    original_tokens = current_tokens
    lines = playbook_content.split("\n")

    # Try removing sections in priority order (lowest priority first)
    for prefix in _TRUNCATION_PRIORITY:
        if current_tokens <= max_tokens:
            break
        # Find and remove the section matching this prefix
        filtered: list[str] = []
        in_section = False
        for line in lines:
            if line.startswith(prefix):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                in_section = False
            if not in_section:
                filtered.append(line)
        if len(filtered) < len(lines):
            lines = filtered
            current_tokens = token_counter("\n".join(lines))

    result = "\n".join(lines).strip()
    if current_tokens > max_tokens:
        # Hard truncate as last resort (keep first max_tokens * 4 chars)
        char_limit = max_tokens * 4
        result = result[:char_limit].rsplit(" ", 1)[0]

    logger.warning(
        "Playbook instructions truncated from ~%d to ~%d tokens (limit: %d)",
        original_tokens,
        token_counter(result),
        max_tokens,
    )
    return result + "\n[Note: Some lower-priority instructions were truncated to fit context window]"


class PersonaContext:
    """Builds ComputerAgent instructions from persona and task."""

    @staticmethod
    def build(
        persona: Persona,
        task: Task,
        playbook_content: str | None = None,
        memories: list[str] | None = None,
        step_budget: int = DEFAULT_STEP_BUDGET,
        max_instruction_tokens: int | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> str:
        """Assemble the full instructions string for ComputerAgent.

        Args:
            persona: Agent identity with traits and preferences.
            task: Task to execute.
            playbook_content: Optional service playbook instructions.
            memories: Optional relevant memory recall content.
            step_budget: Maximum steps for task completion.
            max_instruction_tokens: Optional token limit for playbook instructions.
                When set, cascading sections are truncated by priority.
                None = no limit (backward compatible default).
            token_counter: Optional function to count tokens in text.
                Defaults to len(text) // 4 heuristic.

        Returns:
            Coherent instruction string suitable for ComputerAgent(instructions=...).
        """
        counter = token_counter or _default_token_count
        sections: list[str] = []

        # Demographic identity
        demo = _build_demographic_section(persona)
        if demo:
            sections.append(demo)

        # Trait-based behavioral directives
        trait_dirs = _build_trait_directives(persona)
        if trait_dirs:
            sections.append("Behavioral guidelines:\n- " + "\n- ".join(trait_dirs))

        # Response style
        style = _build_response_style(persona)
        if style:
            sections.append(style)

        # Task context
        task_parts: list[str] = []
        if task.description:
            task_parts.append(f"Task: {task.description}")
        if task.task_type:
            task_parts.append(f"Task type: {task.task_type}")
        if task.source_name:
            task_parts.append(f"Service: {task.source_name}")
        if task_parts:
            sections.append("Task context:\n" + "\n".join(task_parts))

        # Playbook integration (with optional token limit truncation)
        if playbook_content and playbook_content.strip():
            content = playbook_content.strip()
            if max_instruction_tokens is not None:
                content = _truncate_playbook_content(content, max_instruction_tokens, counter)
            sections.append("Playbook instructions (follow in order of specificity):\n" + content)

        # Memory integration
        if memories and any(m.strip() for m in memories):
            mem_text = "\n".join(m.strip() for m in memories if m.strip())
            if max_instruction_tokens is not None:
                # Memories are lower priority than site playbook but higher than base
                mem_tokens = counter(mem_text)
                playbook_tokens = counter(playbook_content or "") if playbook_content else 0
                total = mem_tokens + playbook_tokens
                if total > max_instruction_tokens and mem_tokens > 0:
                    # Truncate memories if combined total exceeds limit
                    available = max(0, max_instruction_tokens - playbook_tokens)
                    if available > 0:
                        char_limit = available * 4
                        mem_text = mem_text[:char_limit].rsplit("\n", 1)[0]
                    else:
                        mem_text = ""
            if mem_text.strip():
                sections.append("Relevant memories:\n" + mem_text)

        # Step budget
        sections.append(f"You have a maximum of {step_budget} steps to complete this task.")

        return "\n\n".join(s for s in sections if s)
