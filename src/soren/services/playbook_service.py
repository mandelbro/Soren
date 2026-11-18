"""PlaybookService — facade for playbook management.

Callers use this one class for everything playbook-related: YAML site/host/base
CRUD, cascading resolution (site > host > base), prompt formatting, confidence
tracking, and the deprecated markdown API. The markdown API is implemented by
``soren.services.markdown_playbooks.MarkdownPlaybookStore``, and its names are
re-exported here for backward compatibility.
"""

from __future__ import annotations

import warnings
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
from soren.services.markdown_playbooks import (
    SECTION_HEADER,
    SOREN_MARKER,
    TASK_SECTION_MAP,
    MarkdownPlaybookStore,
    PlaybookInfo,
    PlaybookUpdate,
)
from soren.services.naming import safe_service_name
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


__all__ = [
    "SECTION_HEADER",
    "SOREN_MARKER",
    "TASK_SECTION_MAP",
    "PlaybookInfo",
    "PlaybookService",
    "PlaybookUpdate",
]


class PlaybookService:
    """Facade: YAML site/host/base playbooks plus the deprecated markdown playbooks (delegated)."""

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
        self._markdown = MarkdownPlaybookStore(settings, llm, self._dir)

        # Tier directories for YAML playbooks
        self._site_dir = Path(settings.playbooks.site_playbooks_dir)
        self._host_dir = Path(settings.playbooks.host_playbooks_dir)
        self._base_dir_path = Path(settings.playbooks.base_playbooks_dir)
        self._site_dir.mkdir(parents=True, exist_ok=True)
        self._host_dir.mkdir(parents=True, exist_ok=True)
        self._base_dir_path.mkdir(parents=True, exist_ok=True)

    # --- YAML Site Playbook CRUD ---

    def load_site(self, source_name: str) -> SitePlaybook | None:
        """Load a site playbook from YAML. Returns None if not found."""
        path = self._site_dir / f"{safe_service_name(source_name)}.yaml"
        if not path.exists():
            return None
        return load_and_validate(path)

    def save_site(self, playbook: SitePlaybook) -> Path:
        """Serialize and write a site playbook to YAML."""
        path = self._site_dir / f"{safe_service_name(playbook.source_name)}.yaml"
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
        path = self._site_dir / f"{safe_service_name(source_name)}.yaml"
        if path.exists():
            path.unlink()
            return True
        return False

    # --- Host Playbook CRUD ---

    def load_host(self, host_domain: str) -> HostPlaybook | None:
        """Load a host playbook from YAML. Returns None if not found."""
        safe = safe_service_name(host_domain)
        path = self._host_dir / f"{safe}.yaml"
        if not path.exists():
            return None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return HostPlaybook.model_validate(raw)

    def save_host(self, playbook: HostPlaybook) -> Path:
        """Serialize and write a host playbook to YAML."""
        safe = safe_service_name(playbook.host_domain)
        path = self._host_dir / f"{safe}.yaml"
        data = serialize_for_yaml(playbook.model_dump(exclude_none=True))
        path.write_text(yaml.dump(data, sort_keys=False, default_flow_style=False), encoding="utf-8")
        return path

    # --- Base Playbook ---

    def load_base(self, task_type: str) -> BasePlaybook | None:
        """Load a base playbook from YAML. Returns None if not found."""
        path = self._base_dir_path / f"{safe_service_name(task_type)}.yaml"
        if not path.exists():
            return None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return BasePlaybook.model_validate(raw)

    # --- Cascading Resolution ---

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

    # --- Prompt Formatting ---

    def to_prompt_instructions(
        self,
        flow: FlowPlaybook,
        flow_type: str,
        credentials: Any | None = None,
    ) -> str:
        """Convert FlowPlaybook steps to numbered imperative instructions for CUA agent."""
        return flow_to_prompt_instructions(flow, flow_type, credentials)

    # --- Confidence Tracking ---

    def update_confidence(
        self,
        source_name: str,
        flow_type: str,
        success: bool,
        task_type: str | None = None,
        min_confidence: float = 0.0,
    ) -> None:
        """Update confidence and consecutive_successes for a flow after execution.

        Args:
            min_confidence: Floor for confidence decrement (e.g. 0.1 for unattended auto-skip).
        """
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
            flow.confidence = max(min_confidence, flow.confidence - 0.2)

        self.save_site(site)

    def update_host_confidence(self, host_domain: str, success: bool) -> None:
        """Update confidence for a host playbook after execution."""
        host = self.load_host(host_domain)
        if host is None:
            return

        if success:
            host.confidence = min(1.0, host.confidence + 0.15)
            host.verified_at = datetime.now(UTC)
        else:
            host.confidence = max(0.0, host.confidence - 0.2)

        self.save_host(host)

    # --- Legacy markdown playbooks (deprecated; delegated to MarkdownPlaybookStore) ---

    def load(self, service_name: str) -> str | None:
        """Read playbook content for a service. Returns None if file does not exist.

        .. deprecated:: Use :meth:`load_site` or :meth:`resolve` instead.
        """
        warnings.warn("load() is deprecated, use load_site() or resolve()", DeprecationWarning, stacklevel=2)
        return self._markdown.load(service_name)

    def get_relevant_sections(self, content: str, task_type: str) -> str:
        """Extract sections relevant to the task type (e.g., Login, Survey Completion).

        .. deprecated:: Use :meth:`resolve` instead.
        """
        warnings.warn("get_relevant_sections() is deprecated, use resolve()", DeprecationWarning, stacklevel=2)
        return self._markdown.get_relevant_sections(content, task_type)

    async def write_back(
        self,
        service_name: str,
        task: Task,
        step_log: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> PlaybookUpdate:
        """Summarize execution steps via LLM and append/update markdown playbook sections.

        Human-edited content outside <!-- soren-generated --> markers is preserved.
        """
        return await self._markdown.write_back(service_name, task, step_log, dry_run=dry_run)

    def list_playbooks(self) -> list[PlaybookInfo]:
        """Enumerate all markdown playbook files with metadata."""
        return self._markdown.list_playbooks()
