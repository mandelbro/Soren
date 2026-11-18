"""MarkdownPlaybookStore — legacy per-service markdown playbooks (deprecated API).

Holds the original markdown playbook behavior (``data/playbooks/<service>.md``):
read, section extraction, and LLM write-back with ``<!-- soren-generated -->``
markers that preserve human edits. ``PlaybookService`` is the facade callers
use; it delegates here and emits the deprecation warnings for ``load()`` and
``get_relevant_sections()``. New code should use YAML site playbooks
(``PlaybookService.load_site`` / ``resolve``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from soren.services.naming import safe_service_name

if TYPE_CHECKING:
    from soren.models.task import Task
    from soren.services.config_loader import SorenSettings
    from soren.services.llm_service import LLMService

SOREN_MARKER = re.compile(r"<!--\s*soren-generated:\s*([^,]+),\s*task:\s*([^>]+)\s*-->", re.IGNORECASE)
SECTION_HEADER = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# Task type -> section headers to include (case-insensitive match)
TASK_SECTION_MAP: dict[str, list[str]] = {
    "survey": ["login", "survey completion", "survey"],
    "email_reply": ["login", "email", "email reply"],
    "form_submission": ["login", "form", "form submission"],
    "proposal": ["login", "proposal"],
}


@dataclass
class PlaybookInfo:
    """Metadata for a playbook file."""

    service_name: str
    file_path: Path
    file_size: int
    last_modified: str
    section_count: int


@dataclass
class PlaybookUpdate:
    """Result of a write-back operation."""

    service_name: str
    sections_added: int
    sections_updated: int
    dry_run: bool
    preview: str | None = None


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class MarkdownPlaybookStore:
    """Markdown playbook files in one directory, with LLM-summarized write-back."""

    def __init__(self, settings: SorenSettings, llm: LLMService, directory: Path) -> None:
        self._settings = settings
        self._llm = llm
        self._dir = directory

    def load(self, service_name: str) -> str | None:
        """Read playbook content for a service. Returns None if file does not exist."""
        safe = safe_service_name(service_name)
        path = self._dir / f"{safe}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_relevant_sections(self, content: str, task_type: str) -> str:
        """Extract sections relevant to the task type (e.g., Login, Survey Completion)."""
        if not content or not content.strip():
            return ""
        headers_to_find = TASK_SECTION_MAP.get(task_type, ["login"])
        sections: list[str] = []
        current_header: str | None = None
        current_body: list[str] = []
        lines = content.split("\n")

        for line in lines:
            m = SECTION_HEADER.match(line)
            if m:
                if current_header:
                    joined = "\n".join(current_body).strip()
                    if joined:
                        sections.append(f"## {current_header}\n{joined}")
                header_lower = m.group(1).strip().lower()
                current_header = m.group(1).strip()
                current_body = []
                if any(h in header_lower for h in headers_to_find):
                    pass
                else:
                    current_header = None
            elif current_header is not None:
                current_body.append(line)

        if current_header and current_body:
            joined = "\n".join(current_body).strip()
            if joined:
                sections.append(f"## {current_header}\n{joined}")

        if sections:
            return "\n\n".join(sections)
        return content.strip()

    async def write_back(
        self,
        service_name: str,
        task: Task,
        step_log: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> PlaybookUpdate:
        """Summarize execution steps via LLM and append/update playbook sections.

        Human-edited content outside <!-- soren-generated --> markers is preserved.
        """
        safe = safe_service_name(service_name)
        path = self._dir / f"{safe}.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""

        steps_text = self._format_steps_for_llm(step_log)
        prompt = self._build_summarization_prompt(task, steps_text)
        model = self._settings.llm.primary_model
        summary = await self._llm.generate(prompt=prompt, model=model)

        timestamp = _iso_now()
        task_id = task.id or "unknown"
        marker = f"<!-- soren-generated: {timestamp}, task: {task_id} -->"
        section_title = task.task_type or "Task"
        new_section = f"\n\n## {section_title} (auto-generated)\n\n{marker}\n{summary.strip()}\n"

        sections_added = 0
        sections_updated = 0

        if not existing.strip():
            new_content = f"# {service_name} Playbook\n{new_section}"
            sections_added = 1
        else:
            updated = self._find_and_replace_generated(existing, section_title, new_section)
            if updated is not None:
                new_content = updated
                sections_updated = 1
            else:
                new_content = existing.rstrip() + new_section
                sections_added = 1

        if not dry_run:
            path.write_text(new_content, encoding="utf-8")

        return PlaybookUpdate(
            service_name=service_name,
            sections_added=sections_added,
            sections_updated=sections_updated,
            dry_run=dry_run,
            preview=new_content if dry_run else None,
        )

    def _find_and_replace_generated(self, content: str, section_title: str, new_section: str) -> str | None:
        """Find a generated section for this title and replace it. Returns None if not found."""
        title_lower = section_title.lower()
        for m in SOREN_MARKER.finditer(content):
            section_start = content.rfind("\n## ", 0, m.start())
            if section_start >= 0:
                header = content[section_start : m.start()]
                if title_lower in header.lower():
                    end = content.find("\n\n## ", m.end())
                    if end == -1:
                        end = len(content)
                    before = content[:section_start].rstrip()
                    after = content[end:].lstrip()
                    replacement = new_section.strip()
                    sep_before = "\n\n" if before else ""
                    sep_after = "\n\n" if after else ""
                    return before + sep_before + replacement + sep_after + after
        return None

    def _format_steps_for_llm(self, step_log: list[dict[str, Any]]) -> str:
        """Format step log for LLM summarization."""
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

    def _build_summarization_prompt(self, task: Task, steps_text: str) -> str:
        """Build prompt for LLM to extract reusable instructions from step log."""
        return f"""You are summarizing a successful task execution into reusable playbook instructions.

Task: {task.title or task.description or "Unknown"}
Task type: {task.task_type or "task"}
Service: {task.source_name or "unknown"}

Execution steps:
{steps_text}

Produce concise, step-by-step instructions that a future agent can follow
to complete similar tasks on this service. Use imperative mood.
Focus on what worked. Do not include timestamps or step numbers.
Output plain markdown only."""

    def list_playbooks(self) -> list[PlaybookInfo]:
        """Enumerate all playbook files with metadata."""
        infos: list[PlaybookInfo] = []
        if not self._dir.exists():
            return infos
        for path in sorted(self._dir.glob("*.md")):
            stat = path.stat()
            content = path.read_text(encoding="utf-8")
            section_count = len(SECTION_HEADER.findall(content))
            infos.append(
                PlaybookInfo(
                    service_name=path.stem,
                    file_path=path,
                    file_size=stat.st_size,
                    last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    section_count=section_count,
                )
            )
        return infos
