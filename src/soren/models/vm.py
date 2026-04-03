"""VM configuration model for Lume integration."""

from pydantic import BaseModel, Field


class VMConfig(BaseModel):
    """Configuration for the Lume macOS VM."""

    name: str = Field(default="soren-vm", description="Active VM name")
    pristine_name: str = Field(default="soren-pristine", description="Pristine clone source for recreation")
    memory_gb: int = Field(default=8, ge=4, le=32, description="VM memory in GB")
    display: str = Field(default="1280x720", description="Display resolution (e.g. 1280x720)")
    recreate_every_n_cycles: int = Field(default=10, ge=1, description="Full VM recreation interval")
    lume_url: str = Field(default="http://localhost:7777", description="Lume HTTP API base URL")
    session_isolation_enabled: bool = Field(
        default=True,
        description="Enable per-session desktop cleanup before automation starts",
    )
    create_new_space_per_session: bool = Field(
        default=True,
        description="Attempt to create/switch to a new Mission Control space for each session",
    )
    allowed_apps: list[str] = Field(
        default_factory=lambda: ["Safari"],
        description="Allowed foreground apps during session prep (system apps always exempt)",
    )
    app_quit_grace_period_s: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description="Delay after quitting non-whitelisted apps",
    )
    space_switch_retries: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Retry count for best-effort Mission Control space setup",
    )
