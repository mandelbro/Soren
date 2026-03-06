"""VLM configuration model for vision-language models (e.g. UI-TARS)."""

from pydantic import BaseModel, Field


class VLMConfig(BaseModel):
    """Configuration for VLM used by ComputerAgent (e.g. mlx UI-TARS)."""

    provider: str = Field(default="mlx", description="Provider prefix (mlx, ollama, etc.)")
    model: str = Field(
        default="mlx-community/UI-TARS-1.5-7B-6bit",
        description="Model identifier for ComputerAgent",
    )

    def full_model_str(self) -> str:
        """Return full model string for ComputerAgent: provider/model."""
        return f"{self.provider}/{self.model}"
