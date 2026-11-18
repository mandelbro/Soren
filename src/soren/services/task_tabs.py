"""TabTransitionHandler — new-tab detection, host playbook delegation, and platform return.

Used by TaskExecutor's learning loop (via LearningMonitor). When a survey
platform opens the actual survey in a new tab on another host, the handler
logs a ``tab_switch`` step, injects that host's playbook into the agent's
messages (DELEGATE_TO_HOST), and later switches back to the platform tab.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from soren.services.vm_manager import VMManager

logger = logging.getLogger(__name__)


def host_domain_of(url: str) -> str | None:
    """Extract domain from a URL for host playbook resolution."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        return parsed.hostname or None
    except Exception:
        return None


class TabTransitionHandler:
    """Tab transitions for one task execution."""

    def __init__(self, vm: VMManager, load_host_content: Callable[[str], str | None]) -> None:
        self._vm = vm
        self._load_host_content = load_host_content

    async def handle_transition(
        self,
        messages: list[dict[str, Any]],
        step_log: list[dict[str, Any]],
        steps_taken: int,
        pre_tab_count: int | None,
        platform_domain: str | None,
    ) -> int | None:
        """Detect and handle tab transitions during execution.

        Returns the updated tab count (to prevent repeated triggers), or the
        original pre_tab_count if no transition was detected.
        """
        if pre_tab_count is None:
            return None

        try:
            new_tab_detected = await self._vm.wait_for_new_tab(pre_tab_count, timeout_s=1.0)
            if not new_tab_detected:
                return pre_tab_count

            # New tab opened — get URL and extract host domain
            tab_url = await self._vm.get_active_tab_url()
            host_domain = host_domain_of(tab_url)
            if not host_domain or host_domain == platform_domain:
                # Same domain or can't parse — update count but skip delegation
                try:
                    return await self._vm.get_tab_count()
                except Exception:
                    return pre_tab_count

            # Log the tab transition
            step_log.append(
                {
                    "step": steps_taken,
                    "action_type": "tab_switch",
                    "action_data": {"url": tab_url, "host_domain": host_domain},
                    "vlm_reasoning": f"DELEGATE_TO_HOST: new tab detected at {host_domain}",
                }
            )

            # Resolve host-specific playbook content and inject into agent context
            host_content = self._load_host_content(host_domain)
            if host_content:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"DELEGATE_TO_HOST: You are now on {host_domain}. "
                            f"Follow these host-specific instructions:\n\n{host_content}"
                        ),
                    }
                )
                logger.info("Injected host playbook content for %s", host_domain)

            # Update tab count to prevent repeated triggers
            return await self._vm.get_tab_count()

        except Exception:
            logger.debug("Tab transition handling failed", exc_info=True)
            return pre_tab_count

    async def return_to_platform(
        self,
        platform_domain: str | None,
        step_log: list[dict[str, Any]],
        steps_taken: int,
    ) -> None:
        """Switch back to the platform tab after delegated host execution."""
        if not platform_domain:
            return
        try:
            tab_idx = await self._vm.find_tab_by_domain(platform_domain)
            if tab_idx is not None:
                switched = await self._vm.switch_to_tab(tab_idx)
                if switched:
                    step_log.append(
                        {
                            "step": steps_taken,
                            "action_type": "tab_switch",
                            "action_data": {"domain": platform_domain, "tab_index": tab_idx},
                            "vlm_reasoning": f"returns_to_platform: switched back to {platform_domain}",
                        }
                    )
            else:
                logger.warning("Platform tab for %s not found; staying on current tab", platform_domain)
        except Exception:
            logger.debug("Platform return failed", exc_info=True)
