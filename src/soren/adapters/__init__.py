"""Discovery adapters for task sources."""

from soren.adapters import email_adapter, generic_form, survey_platform  # noqa: F401
from soren.adapters.base import DiscoveryAdapter, get_adapter, get_adapter_registry

__all__ = ["DiscoveryAdapter", "get_adapter", "get_adapter_registry"]
