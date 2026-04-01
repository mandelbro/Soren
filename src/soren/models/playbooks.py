"""Playbook configuration model."""

from pydantic import BaseModel, Field


class PlaybooksConfig(BaseModel):
    """Configuration for service playbooks."""

    base_dir: str = Field(default="data/playbooks", description="Base directory for playbook markdown files")
    write_back: bool = Field(default=True, description="Enable LLM write-back after successful execution")
    site_playbooks_dir: str = Field(
        default="data/playbooks/site", description="Directory for per-source site playbooks"
    )
    host_playbooks_dir: str = Field(default="data/playbooks/host", description="Directory for host-specific playbooks")
    base_playbooks_dir: str = Field(default="data/playbooks/base", description="Directory for built-in base playbooks")
    cascade_enabled: bool = Field(default=True, description="Enable cascading resolution (site > host > base)")
    auto_learn_host: bool = Field(default=True, description="Auto-generate host playbooks from successful runs")
    max_instruction_tokens: int = Field(
        default=4000, description="Max token estimate for playbook instructions in CUA prompt"
    )
