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
