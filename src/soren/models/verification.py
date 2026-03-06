"""Verification VLM configuration for screenshot extraction."""

from pydantic import BaseModel, Field


class VerificationConfig(BaseModel):
    """Configuration for verification/extraction VLM (e.g. Qwen3-VL)."""

    provider: str = Field(default="ollama", description="Provider for vision model")
    model: str = Field(
        default="qwen3-vl:8b",
        description="Model for screenshot extraction (Phase 2 discovery)",
    )

    model_config = {"from_attributes": True, "extra": "ignore"}
