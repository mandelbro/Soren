"""Shared naming helpers for per-service files."""

from __future__ import annotations

import re


def safe_service_name(name: str) -> str:
    """Sanitize a service/source name for use as a filename stem.

    Shared by site playbooks (``data/playbooks/site/``) and service definitions
    (``config/services/``) so the same name always maps to the same stem.
    """
    return re.sub(r"[^\w\-]", "_", name or "default").strip("_") or "default"
