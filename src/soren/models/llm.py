"""LLM configuration model."""

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Configuration for Ollama LLM (reasoning model)."""

    primary_model: str = Field(default="gemma3:12b", description="Primary reasoning model")
    fallback_model: str = Field(default="gemma3:4b", description="Fallback under memory pressure")
    timeout_s: float = Field(default=120.0, ge=1.0, description="Per-call timeout in seconds")
