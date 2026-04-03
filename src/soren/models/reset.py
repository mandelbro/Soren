"""Reset result model for browser state reset."""

from pydantic import BaseModel, Field


class ResetResult(BaseModel):
    """Result of a browser state reset operation."""

    success: bool = Field(description="Whether the reset completed successfully")
    duration_seconds: float = Field(description="Total time taken for the reset")
    window_count: int = Field(description="Number of Safari windows after reset")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues encountered")


class SessionPrepResult(BaseModel):
    """Result of per-session desktop preparation before automation."""

    success: bool = Field(description="Whether desktop preparation reached a usable state")
    duration_seconds: float = Field(description="Total time taken for session preparation")
    attempted_new_space: bool = Field(description="Whether Mission Control new-space flow was attempted")
    frontmost_app: str = Field(default="", description="Frontmost app after preparation")
    closed_apps: list[str] = Field(default_factory=list, description="Apps quit during whitelist enforcement")
    remaining_disallowed_apps: list[str] = Field(
        default_factory=list,
        description="Disallowed apps still running after enforcement",
    )
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues encountered")
