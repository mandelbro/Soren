"""Playbook configuration model."""

from pydantic import BaseModel, Field


class PlaybooksConfig(BaseModel):
    """Configuration for service playbooks."""

    base_dir: str = Field(default="data/playbooks", description="Base directory for playbook markdown files")
    write_back: bool = Field(default=True, description="Enable LLM write-back after successful execution")
