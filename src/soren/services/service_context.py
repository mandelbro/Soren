"""Per-task active service context (ADR-0002 Decision 6).

The executor, discovery, and training activate the resolved service for the
duration of their agent loop. The CUA patches (screenshot crop, click offset,
Enter-key handling) read it at call time. A ``ContextVar`` is used instead of
``os.environ`` so the operator's ``SOREN_CHROME_CROP_Y`` override stays
distinguishable from configured values, and so the value follows the asyncio
task that activated it without leaking into unrelated tasks.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from soren.services.service_resolution import ResolvedService, parse_chrome_height_env

_ACTIVE_SERVICE: ContextVar[ResolvedService | None] = ContextVar("soren_active_service", default=None)
# A new id per ``use_service`` block, so per-screenshot state (the applied crop) can't carry over between tasks.
_SCOPE: ContextVar[int] = ContextVar("soren_service_scope", default=0)
_scope_ids = itertools.count(1)


def get_active_service() -> ResolvedService | None:
    """The service activated for the current task, if any."""
    return _ACTIVE_SERVICE.get()


def current_scope() -> int:
    """Identifies the current ``use_service`` block (0 outside any)."""
    return _SCOPE.get()


@contextmanager
def use_service(service: ResolvedService | None) -> Iterator[ResolvedService | None]:
    """Activate ``service`` for the enclosed block (``None`` explicitly clears it). Always restored on exit."""
    token = _ACTIVE_SERVICE.set(service)
    scope_token = _SCOPE.set(next(_scope_ids))
    try:
        yield service
    finally:
        _SCOPE.reset(scope_token)
        _ACTIVE_SERVICE.reset(token)


def current_chrome_height_px() -> int:
    """Chrome height to crop from agent screenshots right now.

    With an active service, its resolved value (which already applied the
    env override at resolution time). Without one, the legacy env-only
    behavior: ``SOREN_CHROME_CROP_Y`` if valid, else 0.
    """
    active = get_active_service()
    if active is not None:
        return active.config.chrome_height_px
    value = parse_chrome_height_env(os.environ)
    if value is None:
        return 0
    from soren.services.service_deprecations import env_crop_message, warn_once

    warn_once("env_chrome_crop", env_crop_message(value))
    return value
