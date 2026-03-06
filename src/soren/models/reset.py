"""Reset result model for browser state reset."""

from pydantic import BaseModel, Field


class ResetResult(BaseModel):
    """Result of a browser state reset operation."""

    success: bool = Field(description="Whether the reset completed successfully")
    duration_seconds: float = Field(description="Total time taken for the reset")
    window_count: int = Field(description="Number of Safari windows after reset")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues encountered")
