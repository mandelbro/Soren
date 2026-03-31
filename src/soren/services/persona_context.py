"""PersonaContext builder for ComputerAgent instructions."""

from soren.models.persona import Persona
from soren.models.task import Task

DEFAULT_STEP_BUDGET = 50

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


class PersonaContext:
    """Builds ComputerAgent instructions from persona and task."""

    @staticmethod
    def build(
        persona: Persona,
        task: Task,
        playbook_content: str | None = None,
        memories: list[str] | None = None,
        step_budget: int = DEFAULT_STEP_BUDGET,
    ) -> str:
        """Assemble the full instructions string for ComputerAgent.

        Args:
            persona: Agent identity with traits and preferences.
            task: Task to execute.
            playbook_content: Optional service playbook instructions.
            memories: Optional relevant memory recall content.
            step_budget: Maximum steps for task completion.

        Returns:
            Coherent instruction string suitable for ComputerAgent(instructions=...).
        """
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

        # Playbook integration
        if playbook_content and playbook_content.strip():
            sections.append("Playbook instructions (follow in order of specificity):\n" + playbook_content.strip())

        # Memory integration
        if memories and any(m.strip() for m in memories):
            mem_text = "\n".join(m.strip() for m in memories if m.strip())
            sections.append("Relevant memories:\n" + mem_text)

        # Step budget
        sections.append(f"You have a maximum of {step_budget} steps to complete this task.")

        return "\n\n".join(s for s in sections if s)
