"""Host and base playbook Pydantic models.

HostPlaybook: host-specific navigation patterns (e.g., quickform.example quirks).
BasePlaybook: generic task-type patterns (e.g., survey.yaml structure).
See discovery document Section 4 for the cascading hierarchy.
"""

from __future__ import annotations

from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Literal

from pydantic import BaseModel, Field


class HostPlaybook(BaseModel):
    """Host-level playbook with domain-specific navigation overrides."""

    host_domain: str
    url_patterns: list[str] = []
    version: int = 1
    learned_from: Literal["auto", "human"] = "auto"
    navigation: dict[str, Any] = {}
    quirks: list[str] = []
    verified_at: datetime | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    model_config = {"from_attributes": True}

    def matches_url(self, url: str) -> bool:
        """Check if a URL matches any of the host's url_patterns using glob matching."""
        return any(fnmatch(url, pattern) for pattern in self.url_patterns)


class BasePlaybook(BaseModel):
    """Base-level playbook for generic task-type patterns (mirrors survey.yaml structure)."""

    task_type: str
    version: int = 1
    patterns: dict[str, Any] = {}

    model_config = {"from_attributes": True}
