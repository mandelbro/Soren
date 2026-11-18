"""VMManager facade contract: delegation to SessionPreparer / SafariBrowser and the GuestShell it provides."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.vm import VMConfig
from soren.services.vm_manager import VMManager


def _manager() -> VMManager:
    return VMManager(VMConfig(name="test-vm"))


@pytest.mark.parametrize(
    ("method", "args", "component", "target", "forwarded"),
    [
        (
            "prepare_session_environment",
            (["Calendar"],),
            "_session_prep",
            "prepare_session_environment",
            (["Calendar"],),
        ),
        ("open_required_apps", (["Calendar"],), "_session_prep", "open_required_apps", (["Calendar"],)),
        ("reset_browser_state", (), "_browser", "reset_browser_state", ()),
        ("get_tab_count", (), "_browser", "get_tab_count", ()),
        ("get_active_tab_url", (), "_browser", "get_active_tab_url", ()),
        ("switch_to_tab", (3,), "_browser", "switch_to_tab", (3,)),
        ("find_tab_by_domain", ("x.example",), "_browser", "find_tab_by_domain", ("x.example",)),
        ("wait_for_new_tab", (2, 9.5), "_browser", "wait_for_new_tab", (2, 9.5)),
    ],
)
async def test_delegators_forward_arguments_and_results(method, args, component, target, forwarded) -> None:
    manager = _manager()
    sentinel = object()
    with patch.object(getattr(manager, component), target, AsyncMock(return_value=sentinel)) as delegate:
        result = await getattr(manager, method)(*args)
    delegate.assert_awaited_once_with(*forwarded)
    # open_required_apps is declared -> None; every other delegator returns the component's result.
    assert result is (None if method == "open_required_apps" else sentinel)


async def test_wait_for_new_tab_default_timeout_forwarded() -> None:
    manager = _manager()
    with patch.object(manager._browser, "wait_for_new_tab", AsyncMock(return_value=True)) as delegate:
        await manager.wait_for_new_tab(1)
    delegate.assert_awaited_once_with(1, 5.0)


class TestGuestShell:
    async def test_ensure_connected_connects_when_needed(self) -> None:
        manager = _manager()
        with (
            patch.object(manager, "ensure_ready", AsyncMock()) as ready,
            patch.object(manager, "connect_computer", AsyncMock()) as connect,
        ):
            await manager.ensure_connected()
        ready.assert_awaited_once()
        connect.assert_awaited_once()

    async def test_ensure_connected_skips_connect_when_connected(self) -> None:
        manager = _manager()
        manager._computer = MagicMock()
        with (
            patch.object(manager, "ensure_ready", AsyncMock()) as ready,
            patch.object(manager, "connect_computer", AsyncMock()) as connect,
        ):
            await manager.ensure_connected()
        ready.assert_awaited_once()
        connect.assert_not_awaited()

    @pytest.mark.parametrize("method", ["reset_browser_state", "prepare_session_environment"])
    async def test_components_ensure_connection_through_the_manager(self, method: str) -> None:
        manager = _manager()
        with patch.object(manager, "ensure_connected", AsyncMock(side_effect=RuntimeError("stop here"))) as ensure:
            with pytest.raises(RuntimeError, match="stop here"):
                await getattr(manager, method)()
        ensure.assert_awaited_once()

    async def test_run_osascript_is_late_bound(self) -> None:
        """Components reach _run_osascript at call time, so replacing it after construction takes effect."""
        manager = _manager()
        manager._computer = MagicMock()
        with patch.object(manager, "_run_osascript", AsyncMock(return_value="7")) as runner:
            assert await manager.get_tab_count() == 7
        runner.assert_awaited_once()

    async def test_run_command_requires_connection(self) -> None:
        with pytest.raises(RuntimeError, match="not connected"):
            await _manager().run_command("true")

    def test_computer_property_reflects_connection(self) -> None:
        manager = _manager()
        assert manager.computer is None
        manager._computer = sentinel = MagicMock()
        assert manager.computer is sentinel
