"""TaskDiscovery — three-phase pipeline: UI-TARS navigate → Qwen3-VL extract → Gemma3 structure."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from soren.adapters.base import DISCOVERY_READY, get_adapter
from soren.models.service import ServiceConfigError
from soren.models.session import DiscoveredTask
from soren.services.agent_output import contains_text, screenshot_from_item
from soren.services.service_context import use_service
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import ResolvedService, resolve_service
from soren.services.training_navigation import navigate_if_url_bar_hidden

if TYPE_CHECKING:
    from soren.models.persona import Persona
    from soren.models.task_source import TaskSource
    from soren.services.config_loader import SorenSettings
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.task_backlog import TaskBacklog
    from soren.services.vm_manager import VMManager

DISCOVERY_STEP_BUDGET = 30

logger = logging.getLogger(__name__)


class TaskDiscovery:
    """Orchestrates the three-phase discovery pipeline."""

    def __init__(
        self,
        settings: SorenSettings,
        llm: LLMService,
        vm: VMManager,
        backlog: TaskBacklog,
        *,
        screenshot_dir: Path | None = None,
        playbook_service: PlaybookService | None = None,
        service_registry: ServiceRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._vm = vm
        self._backlog = backlog
        self._screenshot_dir = screenshot_dir or Path("data/screenshots/discovery")
        self._playbook_service = playbook_service
        self._service_registry = service_registry or ServiceRegistry()

    async def discover(
        self,
        persona: Persona,
        source: TaskSource,
        computer: Any,
    ) -> list[DiscoveredTask]:
        """Run three-phase pipeline: navigate → extract → structure. Returns discovered tasks.

        The source's service config is resolved first (ADR-0002): url, type, and
        discovery hint may come from ``config/services/<name>.yaml``, and the
        navigation phase runs with that service active (chrome crop, Enter key).
        A source with invalid service config is skipped and logged.
        """
        try:
            service = resolve_service(
                source.service_key,
                registry=self._service_registry,
                settings=self._settings,
                source=source,
                persona_id=persona.id or None,
            )
        except ServiceConfigError as exc:
            logger.error("Skipping discovery for source %r: service config error: %s", source.name, exc)
            return []
        source = self._effective_source(source, service)

        adapter = get_adapter(source.type)
        if not adapter:
            return []

        try:
            with use_service(service):
                await navigate_if_url_bar_hidden(
                    service.config.chrome_height_px, source.url, computer.interface, f"discovery for {source.name!r}"
                )
                screenshots = await self._phase1_navigate(adapter, persona, source, computer)
            if not screenshots:
                return []

            raw_text = await self._phase2_extract(adapter, source, screenshots)
            if not raw_text:
                return []

            tasks = await self._phase3_structure(adapter, source, raw_text, persona)
            return tasks
        except Exception:
            # One broken source must not stop discovery for the others, but never fail silently.
            logger.exception("Discovery failed for source %r (persona %r)", source.name, persona.id)
            return []

    @staticmethod
    def _effective_source(source: TaskSource, service: ResolvedService) -> TaskSource:
        """Copy of ``source`` with url/type/hint filled from the resolved service (persona values win)."""
        return source.model_copy(
            update={"url": service.url, "type": service.type, "discovery_hint": service.discovery_hint}
        )

    def _build_nav_prompt(self, adapter: Any, persona: Persona, source: TaskSource) -> str:
        """Build navigation prompt, injecting login/discovery playbooks if available."""
        if self._playbook_service is None:
            return adapter.navigation_prompt(source, persona)

        site = self._playbook_service.load_site(source.name)
        if site is None:
            return adapter.navigation_prompt(source, persona)

        parts: list[str] = []

        # Login flow
        if site.flows.login:
            login_instructions = self._playbook_service.to_prompt_instructions(site.flows.login, "login")
            parts.append(login_instructions)

        # Discovery flow (replaces adapter nav prompt if available)
        if site.flows.discovery:
            discovery_instructions = self._playbook_service.to_prompt_instructions(site.flows.discovery, "discovery")
            if parts:
                parts.append(f"\nAfter login:\n\n{discovery_instructions}")
            else:
                parts.append(discovery_instructions)
        else:
            # Fall back to adapter prompt for discovery
            adapter_prompt = adapter.navigation_prompt(source, persona)
            if parts:
                parts.append(f"\nAfter login:\n\n{adapter_prompt}")
            else:
                parts.append(adapter_prompt)

        return "\n".join(parts)

    async def _phase1_navigate(
        self,
        adapter: Any,
        persona: Persona,
        source: TaskSource,
        computer: Any,
    ) -> list[bytes]:
        """Phase 1: Run ComputerAgent until DISCOVERY_READY, capture screenshots."""
        from soren.patches import apply_all

        apply_all()

        from cua_agent import ComputerAgent

        vlm_config = getattr(self._settings, "vlm", None)
        vlm_model = f"mlx/{vlm_config.model}" if vlm_config else "mlx/mlx-community/UI-TARS-1.5-7B-6bit"

        nav_prompt = self._build_nav_prompt(adapter, persona, source)
        messages = [{"role": "user", "content": nav_prompt}]

        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshots: list[bytes] = []
        max_screenshots_in_memory = 5  # Cap to avoid OOM on 32 GB machines
        step = 0

        agent = ComputerAgent(
            model=vlm_model,
            tools=[computer],
            instructions=nav_prompt,
            screenshot_delay=0.5,
            only_n_most_recent_images=5,
        )

        async for result in agent.run(messages):
            step += 1
            output = result.get("output", [])
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "computer_call_output":
                    img_data = screenshot_from_item(item)
                    if img_data:
                        screenshots.append(img_data)
                        screenshots = screenshots[-max_screenshots_in_memory:]
                if contains_text(item, DISCOVERY_READY):
                    return screenshots[-3:] if len(screenshots) >= 3 else screenshots
            if step >= DISCOVERY_STEP_BUDGET:
                break

        return screenshots[-3:] if len(screenshots) >= 3 else screenshots

    async def _phase2_extract(self, adapter: Any, source: TaskSource, screenshots: list[bytes]) -> str:
        """Phase 2: Send screenshots to Qwen3-VL with extraction prompt."""
        verification = getattr(self._settings, "verification", None)
        model = verification.model if verification else "qwen3-vl:8b"

        prompt = adapter.extraction_prompt(source)
        return await self._llm.generate(
            prompt=prompt,
            images=screenshots,
            model=model,
        )

    async def _phase3_structure(
        self,
        adapter: Any,
        source: TaskSource,
        raw_text: str,
        persona: Persona,
    ) -> list[DiscoveredTask]:
        """Phase 3: Send raw text to Gemma3:12B, parse to DiscoveredTask list."""
        struct_prompt = adapter.structuring_prompt(source, raw_text)
        full_prompt = f"{struct_prompt}\n\nRespond with a JSON array of task objects."
        text = await self._llm.generate(prompt=full_prompt, model=None)
        return self._parse_discovered_tasks(text, source, persona)

    def _parse_discovered_tasks(self, text: str, source: TaskSource, persona: Persona) -> list[DiscoveredTask]:
        """Parse LLM output to list of DiscoveredTask."""
        tasks: list[DiscoveredTask] = []
        text = text.strip()
        if not text:
            return tasks

        # Try to extract JSON array
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            json_str = text[start:end]
        else:
            json_str = text

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return tasks

        if isinstance(data, dict) and "tasks" in data:
            data = data["tasks"]
        if not isinstance(data, list):
            return tasks

        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                dt = DiscoveredTask(
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    task_type=item.get("task_type", "survey"),
                    source_name=source.name,
                    source_type=source.type,
                    source_url=source.url,
                    provider_task_id=item.get("provider_task_id"),
                    persona_id=persona.id,
                    priority=int(item.get("priority", 5)),
                    estimated_duration_minutes=item.get("estimated_duration_minutes"),
                    estimated_reward=item.get("estimated_reward"),
                    snippet=item.get("snippet", ""),
                    stable_metadata=json.dumps(
                        {
                            k: v
                            for k, v in item.items()
                            if k
                            in (
                                "estimated_reward",
                                "reward_currency",
                                "provider_task_id",
                                "sender",
                                "timestamp",
                                "requires_response",
                                "form_type",
                                "form_title",
                            )
                        },
                        sort_keys=True,
                    ),
                )
                tasks.append(dt)
            except Exception:
                continue
        return tasks
