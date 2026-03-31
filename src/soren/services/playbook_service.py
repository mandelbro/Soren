"""PlaybookService — unified playbook management with YAML site/host/base CRUD and legacy markdown support."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from soren.models.host_playbook import BasePlaybook, HostPlaybook
from soren.models.site_playbook import (
    DiscoveryFlow,
    ExecutionFlow,
    FlowPlaybook,
    LoginFlow,
    SiteFlows,
    SitePlaybook,
    load_and_validate,
)
from soren.services.playbook_formatting import (
    flow_to_prompt_instructions,
    format_base_content,
    format_host_content,
    serialize_for_yaml,
)

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


def _safe_service_name(name: str) -> str:
    """Sanitize service name for use as filename."""
    return re.sub(r"[^\w\-]", "_", name or "default").strip("_") or "default"


class PlaybookService:
    """Manages per-service playbook markdown files with LLM write-back and YAML CRUD."""

    def __init__(
        self,
        settings: SorenSettings,
        llm: LLMService,
        *,
        playbooks_dir: Path | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        base = settings.playbooks.base_dir
        self._dir = playbooks_dir or Path.cwd() / base
        self._dir.mkdir(parents=True, exist_ok=True)

        # Tier directories for YAML playbooks
        self._site_dir = Path(settings.playbooks.site_playbooks_dir)
        self._host_dir = Path(settings.playbooks.host_playbooks_dir)
        self._base_dir_path = Path(settings.playbooks.base_playbooks_dir)
        self._site_dir.mkdir(parents=True, exist_ok=True)
        self._host_dir.mkdir(parents=True, exist_ok=True)
        self._base_dir_path.mkdir(parents=True, exist_ok=True)

    # --- YAML Site Playbook CRUD (Task 006) ---

    def load_site(self, source_name: str) -> SitePlaybook | None:
        """Load a site playbook from YAML. Returns None if not found."""
        path = self._site_dir / f"{_safe_service_name(source_name)}.yaml"
        if not path.exists():
            return None
        return load_and_validate(path)

    def save_site(self, playbook: SitePlaybook) -> Path:
        """Serialize and write a site playbook to YAML."""
        path = self._site_dir / f"{_safe_service_name(playbook.source_name)}.yaml"
        data = serialize_for_yaml(playbook.model_dump(exclude_none=True))
        path.write_text(yaml.dump(data, sort_keys=False, default_flow_style=False), encoding="utf-8")
        return path

    def save_flow(
        self,
        source_name: str,
        flow_type: str,
        flow: FlowPlaybook,
        task_type: str | None = None,
    ) -> Path:
        """Update a specific flow in a site playbook (creating the playbook if needed)."""
        existing = self.load_site(source_name)
        if existing is None:
            existing = SitePlaybook(
                source_name=source_name,
                source_url="",
                flows=SiteFlows(),
            )

        flow_data = flow.model_dump(exclude_none=True)

        if flow_type == "login":
            existing.flows.login = LoginFlow.model_validate(flow_data)
        elif flow_type == "discovery":
            existing.flows.discovery = DiscoveryFlow.model_validate(flow_data)
        elif flow_type == "execution":
            if existing.flows.execution is None:
                existing.flows.execution = {}
            key = task_type or "default"
            existing.flows.execution[key] = ExecutionFlow.model_validate(flow_data)

        return self.save_site(existing)

    def list_site_playbooks(self) -> list[SitePlaybook]:
        """Enumerate and load all site playbook YAML files."""
        if not self._site_dir.exists():
            return []
        playbooks: list[SitePlaybook] = []
        for path in sorted(self._site_dir.glob("*.yaml")):
            try:
                playbooks.append(load_and_validate(path))
            except Exception:
                continue
        return playbooks

    def delete_site(self, source_name: str) -> bool:
        """Delete a site playbook file. Returns True if it existed."""
        path = self._site_dir / f"{_safe_service_name(source_name)}.yaml"
        if path.exists():
            path.unlink()
            return True
        return False

    # --- Host Playbook CRUD ---

    def load_host(self, host_domain: str) -> HostPlaybook | None:
        """Load a host playbook from YAML. Returns None if not found."""
        safe = _safe_service_name(host_domain)
        path = self._host_dir / f"{safe}.yaml"
        if not path.exists():
            return None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return HostPlaybook.model_validate(raw)

    def save_host(self, playbook: HostPlaybook) -> Path:
        """Serialize and write a host playbook to YAML."""
        safe = _safe_service_name(playbook.host_domain)
        path = self._host_dir / f"{safe}.yaml"
        data = serialize_for_yaml(playbook.model_dump(exclude_none=True))
        path.write_text(yaml.dump(data, sort_keys=False, default_flow_style=False), encoding="utf-8")
        return path

    # --- Base Playbook ---

    def load_base(self, task_type: str) -> BasePlaybook | None:
        """Load a base playbook from YAML. Returns None if not found."""
        path = self._base_dir_path / f"{_safe_service_name(task_type)}.yaml"
        if not path.exists():
            return None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return BasePlaybook.model_validate(raw)

    # --- Cascading Resolution (Task 007) ---

    def resolve(
        self,
        source_name: str,
        host_domain: str | None,
        task_type: str,
        flow_type: str,
    ) -> str:
        """Resolve cascading playbook instructions: site > host > base.

        Returns formatted prompt text with priority headers, or empty string if nothing found.
        """
        sections: list[str] = []

        # Site tier
        site_content = self._resolve_site_content(source_name, flow_type, task_type)
        if site_content:
            sections.append(
                "## Site-Specific Instructions (highest priority)\n"
                "Follow these instructions first. They override all other guidance.\n"
                f"{site_content}"
            )

        cascade_enabled = getattr(self._settings.playbooks, "cascade_enabled", True)

        if cascade_enabled:
            # Host tier
            if host_domain:
                host_content = self._resolve_host_content(host_domain)
                if host_content:
                    sections.append(f"## Host Instructions\nThese apply to the survey host platform.\n{host_content}")

            # Base tier
            base_content = self._resolve_base_content(task_type)
            if base_content:
                sections.append(
                    f"## General Guidelines\nFollow these when no specific instructions apply above.\n{base_content}"
                )

        if not sections:
            return ""
        return "\n\n".join(sections)

    def _resolve_site_content(self, source_name: str, flow_type: str, task_type: str) -> str | None:
        """Extract content from site playbook for the given flow type."""
        site = self.load_site(source_name)
        if site is None:
            return None

        flow: FlowPlaybook | None = None
        if flow_type == "login":
            flow = site.flows.login
        elif flow_type == "discovery":
            flow = site.flows.discovery
        elif flow_type == "execution" and site.flows.execution:
            flow = site.flows.execution.get(task_type) or site.flows.execution.get("default")

        if flow is None:
            return None

        return self.to_prompt_instructions(flow, flow_type)

    def _resolve_host_content(self, host_domain: str) -> str | None:
        """Extract content from host playbook."""
        host = self.load_host(host_domain)
        if host is None:
            return None
        return format_host_content(host.navigation, host.quirks)

    def _resolve_base_content(self, task_type: str) -> str | None:
        """Extract content from base playbook."""
        base = self.load_base(task_type)
        if base is None:
            return None
        return format_base_content(base.patterns)

    # --- Prompt Formatting (Task 007) ---

    def to_prompt_instructions(
        self,
        flow: FlowPlaybook,
        flow_type: str,
        credentials: Any | None = None,
    ) -> str:
        """Convert FlowPlaybook steps to numbered imperative instructions for CUA agent."""
        return flow_to_prompt_instructions(flow, flow_type, credentials)

    # --- Confidence Tracking (Task 007) ---

    def update_confidence(
        self,
        source_name: str,
        flow_type: str,
        success: bool,
        task_type: str | None = None,
    ) -> None:
        """Update confidence and consecutive_successes for a flow after execution."""
        site = self.load_site(source_name)
        if site is None:
            return

        flow: FlowPlaybook | None = None
        if flow_type == "login":
            flow = site.flows.login
        elif flow_type == "discovery":
            flow = site.flows.discovery
        elif flow_type == "execution" and site.flows.execution:
            flow = site.flows.execution.get(task_type or "default")

        if flow is None:
            return

        if success:
            flow.consecutive_successes += 1
            flow.confidence = min(1.0, flow.confidence + 0.15)
            flow.verified_at = datetime.now(UTC)
        else:
            flow.consecutive_successes = 0
            flow.confidence = max(0.0, flow.confidence - 0.2)

        self.save_site(site)

    def load(self, service_name: str) -> str | None:
        """Read playbook content for a service. Returns None if file does not exist.

        .. deprecated:: Use :meth:`load_site` or :meth:`resolve` instead.
        """
        warnings.warn(
            "load() is deprecated, use load_site() or resolve()",
            DeprecationWarning,
            stacklevel=2,
        )
        safe = _safe_service_name(service_name)
        path = self._dir / f"{safe}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_relevant_sections(self, content: str, task_type: str) -> str:
        """Extract sections relevant to the task type (e.g., Login, Survey Completion).

        .. deprecated:: Use :meth:`resolve` instead.
        """
        warnings.warn(
            "get_relevant_sections() is deprecated, use resolve()",
            DeprecationWarning,
            stacklevel=2,
        )
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
        safe = _safe_service_name(service_name)
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
