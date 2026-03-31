"""Tests for VMManager tab introspection methods (Task 018)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.vm import VMConfig
from soren.services.vm_manager import VMManager


@pytest.fixture
def vm_manager() -> VMManager:
    """VMManager with mocked computer interface."""
    vm = VMManager(VMConfig())
    # Simulate a connected computer
    mock_computer = MagicMock()
    mock_computer.interface = MagicMock()
    vm._computer = mock_computer
    return vm


class TestGetTabCount:
    @pytest.mark.asyncio
    async def test_returns_tab_count(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, return_value="3"):
            result = await vm_manager.get_tab_count()
        assert result == 3

    @pytest.mark.asyncio
    async def test_returns_zero_on_error(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await vm_manager.get_tab_count()
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_non_numeric(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, return_value=""):
            result = await vm_manager.get_tab_count()
        assert result == 0

    @pytest.mark.asyncio
    async def test_raises_when_no_computer(self):
        vm = VMManager(VMConfig())
        with pytest.raises(RuntimeError, match="Computer not connected"):
            await vm.get_tab_count()


class TestGetActiveTabUrl:
    @pytest.mark.asyncio
    async def test_returns_url(self, vm_manager):
        with patch.object(
            vm_manager, "_run_osascript", new_callable=AsyncMock, return_value="https://prolific.com/studies"
        ):
            result = await vm_manager.get_active_tab_url()
        assert result == "https://prolific.com/studies"

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await vm_manager.get_active_tab_url()
        assert result == ""


class TestSwitchToTab:
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, return_value=""):
            result = await vm_manager.switch_to_tab(2)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await vm_manager.switch_to_tab(2)
        assert result is False


class TestFindTabByDomain:
    @pytest.mark.asyncio
    async def test_finds_matching_tab(self, vm_manager):
        # osascript returns tab index where URL contains domain
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, return_value="2"):
            result = await vm_manager.find_tab_by_domain("qualtrics.com")
        assert result == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, return_value="0"):
            result = await vm_manager.find_tab_by_domain("nonexistent.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, vm_manager):
        with patch.object(vm_manager, "_run_osascript", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await vm_manager.find_tab_by_domain("qualtrics.com")
        assert result is None


class TestWaitForNewTab:
    @pytest.mark.asyncio
    async def test_detects_new_tab(self, vm_manager):
        # First call returns 1 (initial), second call returns 2 (new tab opened)
        call_count = 0

        async def mock_get_tab_count():
            nonlocal call_count
            call_count += 1
            return 1 if call_count == 1 else 2

        with patch.object(vm_manager, "get_tab_count", side_effect=mock_get_tab_count):
            result = await vm_manager.wait_for_new_tab(initial_count=1, timeout_s=2.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self, vm_manager):
        with patch.object(vm_manager, "get_tab_count", new_callable=AsyncMock, return_value=1):
            result = await vm_manager.wait_for_new_tab(initial_count=1, timeout_s=0.5)
        assert result is False
