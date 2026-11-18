"""Configuration for enter key patch behavior.

Resolution order:

1. The active service (``soren.services.service_context``), which already
   layers settings.yaml ``services.<name>``, the service file, and global
   defaults (ADR-0002). No files are read on this path.
2. Legacy lookup when no service is active or an explicit ``service_id`` is
   given: resolved from settings.yaml and ``config/services/`` on demand.
3. The module default (``enter_only``; settable for tests).

Never raises: the patch runs inside the agent loop, so configuration
problems are logged and the default is used.
"""

from __future__ import annotations

import logging
from pathlib import Path

from soren.models.service import EnterKeyBehavior

logger = logging.getLogger(__name__)

# Overridable (tests / alternate roots). None = repo defaults.
SETTINGS_PATH: Path | None = None
SERVICES_DIR: Path | None = None

_default: EnterKeyBehavior = "enter_only"


def get_enter_key_behavior(service_id: str | None = None) -> EnterKeyBehavior:
    """Return enter key behavior for the active service, the given service, or the default."""
    if service_id is None:
        from soren.services.service_context import get_active_service

        active = get_active_service()
        if active is not None:
            return active.config.enter_key_behavior
    return _legacy_lookup(service_id)


def _legacy_lookup(service_id: str | None) -> EnterKeyBehavior:
    try:
        from soren.services.config_loader import load_settings
        from soren.services.service_registry import ServiceRegistry
        from soren.services.service_resolution import resolve_service

        settings = load_settings(SETTINGS_PATH)
        if service_id:
            resolved = resolve_service(
                service_id, registry=ServiceRegistry(SERVICES_DIR), settings=settings, environ={}
            )
            if resolved.source_of("enter_key_behavior") != "default":
                return resolved.config.enter_key_behavior
            return _default
        return settings.service_defaults.enter_key_behavior or settings.enter_key_behavior or _default
    except Exception as exc:
        logger.warning(
            "Could not resolve enter_key_behavior for %r (%s); using %s", service_id or "default", exc, _default
        )
        return _default


def set_default_enter_key_behavior(behavior: EnterKeyBehavior) -> None:
    """Set default behavior (for tests)."""
    global _default
    _default = behavior
