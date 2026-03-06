"""DiscoveryAdapter protocol and adapter registry."""

from typing import Protocol

from soren.models.persona import Persona
from soren.models.task_source import TaskSource


class DiscoveryAdapter(Protocol):
    """Protocol for discovery adapters: three-prompt interface for the pipeline."""

    @property
    def source_type(self) -> str:
        """Adapter's source type (e.g. survey_platform, email)."""
        ...

    @property
    def expected_fields(self) -> list[str]:
        """Fields the structuring prompt should produce per task."""
        ...

    def navigation_prompt(self, source: TaskSource, persona: Persona) -> str:
        """Instructions for UI-TARS (~20-30 lines). Include DISCOVERY_READY sentinel."""
        ...

    def extraction_prompt(self, source: TaskSource) -> str:
        """Instructions for Qwen3-VL (~30-40 lines)."""
        ...

    def structuring_prompt(self, source: TaskSource, raw_text: str) -> str:
        """Instructions for Gemma3:12B (~20-30 lines)."""
        ...


# Sentinel token: adapter must include this in navigation_prompt; pipeline stops when detected
DISCOVERY_READY = "DISCOVERY_READY"

# Registry: source_type -> adapter instance (populated by concrete adapters)
_ADAPTER_REGISTRY: dict[str, DiscoveryAdapter] = {}


def register_adapter(adapter: DiscoveryAdapter) -> None:
    """Register an adapter for its source_type."""
    _ADAPTER_REGISTRY[adapter.source_type] = adapter


def get_adapter(source_type: str) -> DiscoveryAdapter | None:
    """Return adapter for source_type, or None if not registered."""
    return _ADAPTER_REGISTRY.get(source_type)


def get_adapter_registry() -> dict[str, DiscoveryAdapter]:
    """Return copy of adapter registry."""
    return dict(_ADAPTER_REGISTRY)
