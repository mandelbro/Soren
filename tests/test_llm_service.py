"""Tests for LLMService and MemoryGuard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.llm import LLMConfig
from soren.services.config_loader import SorenSettings
from soren.services.llm_service import LLMService
from soren.services.memory_guard import MemoryGuard


@pytest.fixture
def settings() -> SorenSettings:
    return SorenSettings(llm=LLMConfig(primary_model="gemma3:12b", fallback_model="gemma3:4b"))


@pytest.fixture
def llm_service(settings: SorenSettings) -> LLMService:
    return LLMService(settings)


class TestLLMService:
    @pytest.mark.asyncio
    async def test_generate_sends_keep_alive_zero(self, llm_service: LLMService) -> None:
        """LLMService.generate() sends request with keep_alive=0."""
        with patch.object(llm_service._client, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = MagicMock(response="hello")
            with patch.object(
                llm_service._memory_guard,
                "get_reasoning_model",
                new_callable=AsyncMock,
                return_value="gemma3:12b",
            ):
                await llm_service.generate("test prompt")
            mock_gen.assert_called_once()
            assert mock_gen.call_args.kwargs.get("keep_alive") == 0

    @pytest.mark.asyncio
    async def test_generate_passes_images_when_provided(self, llm_service: LLMService) -> None:
        """LLMService.generate() passes images when provided."""
        with patch.object(llm_service._client, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = MagicMock(response="ok")
            with patch.object(
                llm_service._memory_guard,
                "get_reasoning_model",
                new_callable=AsyncMock,
                return_value="gemma3:12b",
            ):
                await llm_service.generate("prompt", images=[b"fake-png-bytes"])
            mock_gen.assert_called_once()
            assert mock_gen.call_args.kwargs.get("images") == [b"fake-png-bytes"]

    @pytest.mark.asyncio
    async def test_generate_structured_parses_response_into_pydantic(self, llm_service: LLMService) -> None:
        """LLMService.generate_structured() parses response into Pydantic model."""
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            name: str
            count: int

        with patch.object(llm_service._client, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = MagicMock(response='{"name": "foo", "count": 42}')
            with patch.object(
                llm_service._memory_guard,
                "get_reasoning_model",
                new_callable=AsyncMock,
                return_value="gemma3:12b",
            ):
                result = await llm_service.generate_structured("extract", TestSchema)
            assert result.name == "foo"
            assert result.count == 42

    @pytest.mark.asyncio
    async def test_generate_structured_raises_on_invalid_json(self, llm_service: LLMService) -> None:
        """LLMService.generate_structured() raises on invalid JSON response."""
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            x: str

        with patch.object(llm_service._client, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = MagicMock(response="not valid json")
            with (
                patch.object(
                    llm_service._memory_guard,
                    "get_reasoning_model",
                    new_callable=AsyncMock,
                    return_value="gemma3:12b",
                ),
                pytest.raises(ValueError, match="Invalid JSON"),
            ):
                await llm_service.generate_structured("extract", TestSchema)

    @pytest.mark.asyncio
    async def test_is_available_returns_true_when_ollama_responds(self, llm_service: LLMService) -> None:
        """LLMService.is_available() returns True when Ollama responds."""
        with patch.object(llm_service._client, "list", new_callable=AsyncMock):
            result = await llm_service.is_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_is_available_returns_false_on_connection_error(self, llm_service: LLMService) -> None:
        """LLMService.is_available() returns False on connection error."""
        with patch.object(
            llm_service._client,
            "list",
            new_callable=AsyncMock,
            side_effect=ConnectionError(),
        ):
            result = await llm_service.is_available()
            assert result is False

    @pytest.mark.asyncio
    async def test_unload_model_sends_unload_request(self, llm_service: LLMService) -> None:
        """LLMService.unload_model() sends unload request."""
        with patch.object(llm_service._client, "generate", new_callable=AsyncMock) as mock_gen:
            await llm_service.unload_model("gemma3:12b")
            mock_gen.assert_called_once()
            assert mock_gen.call_args.kwargs.get("keep_alive") == 0


class TestMemoryGuard:
    def test_returns_primary_model_when_no_pressure(self) -> None:
        """MemoryGuard returns primary model when no pressure."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        import asyncio

        async def _run():
            with patch.object(guard, "_check_pressure", new_callable=AsyncMock, return_value=False):
                return await guard.get_reasoning_model(None)

        result = asyncio.run(_run())
        assert result == "primary"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_critical_pressure(self) -> None:
        """MemoryGuard returns fallback model on memory_pressure == 'critical'."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        with patch.object(guard, "_check_pressure", new_callable=AsyncMock, return_value=True):
            result = await guard.get_reasoning_model(None)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_fallback_is_sticky_within_cycle(self) -> None:
        """MemoryGuard fallback is sticky within a cycle (doesn't revert to primary)."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        with patch.object(guard, "_check_pressure", new_callable=AsyncMock, return_value=True):
            r1 = await guard.get_reasoning_model(None)
        with patch.object(guard, "_check_pressure", new_callable=AsyncMock, return_value=False):
            r2 = await guard.get_reasoning_model(None)
        assert r1 == "fallback"
        assert r2 == "fallback"

    @pytest.mark.asyncio
    async def test_reset_cycle_clears_fallback_flag(self) -> None:
        """MemoryGuard.reset_cycle() clears the fallback flag."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        with patch.object(guard, "_check_pressure", new_callable=AsyncMock, return_value=True):
            await guard.get_reasoning_model(None)
        guard.reset_cycle()
        with patch.object(guard, "_check_pressure", new_callable=AsyncMock, return_value=False):
            result = await guard.get_reasoning_model(None)
        assert result == "primary"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_warn_and_psutil_high(self) -> None:
        """MemoryGuard returns fallback on warn + psutil > 85%."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        with patch.object(guard, "_get_memory_pressure", return_value="warn"):
            with patch.object(guard, "_get_psutil_pressure", return_value=90.0):
                with patch.object(guard, "_get_ollama_memory", new_callable=AsyncMock, return_value=0.0):
                    with patch.object(guard, "_get_pageout_count", return_value=0):
                        result = await guard.get_reasoning_model(None)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_psutil_90_alone(self) -> None:
        """MemoryGuard returns fallback on psutil > 90% alone."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        with patch.object(guard, "_get_memory_pressure", return_value="normal"):
            with patch.object(guard, "_get_psutil_pressure", return_value=95.0):
                with patch.object(guard, "_get_ollama_memory", new_callable=AsyncMock, return_value=0.0):
                    with patch.object(guard, "_get_pageout_count", return_value=0):
                        result = await guard.get_reasoning_model(None)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_pageout_growth(self) -> None:
        """MemoryGuard returns fallback when pageouts exceed threshold."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        with patch.object(guard, "_get_memory_pressure", return_value="normal"):
            with patch.object(guard, "_get_psutil_pressure", return_value=50.0):
                with patch.object(guard, "_get_ollama_memory", new_callable=AsyncMock, return_value=0.0):
                    with patch.object(guard, "_get_pageout_count", return_value=15_000_000):
                        result = await guard.get_reasoning_model(None)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_no_fallback_below_pageout_threshold(self) -> None:
        """MemoryGuard does NOT trigger fallback when pageouts are below 10M threshold."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback"))
        with patch.object(guard, "_get_memory_pressure", return_value="normal"):
            with patch.object(guard, "_get_psutil_pressure", return_value=50.0):
                with patch.object(guard, "_get_ollama_memory", new_callable=AsyncMock, return_value=0.0):
                    with patch.object(guard, "_get_pageout_count", return_value=3_000_000):
                        result = await guard.get_reasoning_model(None)
        assert result == "primary"

    @pytest.mark.asyncio
    async def test_custom_pageout_threshold(self) -> None:
        """MemoryGuard respects custom pageout_threshold from config."""
        guard = MemoryGuard(LLMConfig(primary_model="primary", fallback_model="fallback", pageout_threshold=5_000_000))
        with patch.object(guard, "_get_memory_pressure", return_value="normal"):
            with patch.object(guard, "_get_psutil_pressure", return_value=50.0):
                with patch.object(guard, "_get_ollama_memory", new_callable=AsyncMock, return_value=0.0):
                    with patch.object(guard, "_get_pageout_count", return_value=6_000_000):
                        result = await guard.get_reasoning_model(None)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_cloud_model_bypasses_pressure_check(self) -> None:
        """MemoryGuard skips pressure checks for cloud models (name ending with -cloud)."""
        guard = MemoryGuard(LLMConfig(primary_model="gemma3:27b-cloud", fallback_model="gemma3:4b"))
        # Simulate extreme pressure — should still return primary for cloud models
        with patch.object(guard, "_get_memory_pressure", return_value="critical"):
            with patch.object(guard, "_get_psutil_pressure", return_value=99.0):
                with patch.object(guard, "_get_ollama_memory", new_callable=AsyncMock, return_value=0.0):
                    with patch.object(guard, "_get_pageout_count", return_value=100_000_000):
                        result = await guard.get_reasoning_model(None)
        assert result == "gemma3:27b-cloud"
        assert guard.fallback_triggered is False

    @pytest.mark.asyncio
    async def test_cloud_model_records_skipped_metrics(self) -> None:
        """MemoryGuard records metrics with skipped=True for cloud models."""
        guard = MemoryGuard(LLMConfig(primary_model="gemma3:27b-cloud", fallback_model="gemma3:4b"))
        with patch.object(guard, "_check_pressure", new_callable=AsyncMock) as mock_pressure:
            await guard.get_reasoning_model(None)
            mock_pressure.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_cloud_model_still_checks_pressure(self) -> None:
        """MemoryGuard still checks pressure for non-cloud models."""
        guard = MemoryGuard(LLMConfig(primary_model="gemma3:12b", fallback_model="gemma3:4b"))
        with patch.object(guard, "_check_pressure", new_callable=AsyncMock, return_value=True) as mock_pressure:
            result = await guard.get_reasoning_model(None)
            mock_pressure.assert_called_once()
        assert result == "gemma3:4b"
