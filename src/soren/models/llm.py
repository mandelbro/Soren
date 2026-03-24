"""LLM configuration model."""

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Configuration for Ollama LLM (reasoning model)."""

    primary_model: str = Field(default="gemma3:12b", description="Primary reasoning model")
    fallback_model: str = Field(default="gemma3:4b", description="Fallback under memory pressure")
    timeout_s: float = Field(default=120.0, ge=1.0, description="Per-call timeout in seconds")
    pageout_threshold: int = Field(
        default=10_000_000,
        ge=0,
        description="vm_stat pageout count that triggers fallback (0 to disable)",
    )

    @property
    def is_cloud_model(self) -> bool:
        """True when primary_model targets Ollama Cloud (name ends with '-cloud')."""
        return self._model_is_cloud(self.primary_model)

    @staticmethod
    def _model_is_cloud(model: str) -> bool:
        """Check if a model identifier targets Ollama Cloud."""
        # Ollama Cloud models use the convention 'model:tag-cloud'
        return model.rstrip().endswith("-cloud")
