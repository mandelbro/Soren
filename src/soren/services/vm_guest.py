"""GuestShell — what VMManager's components need from the connected VM.

``VMManager`` implements this protocol and passes itself to its components
(``SafariBrowser``, ``SessionPreparer``). Calls resolve on the manager at call
time, so the manager stays the single owner of the connection (and the single
place tests patch ``_run_osascript`` / ``ensure_ready`` / ``connect_computer``).
"""

from __future__ import annotations

from typing import Any, Protocol


class GuestShell(Protocol):
    @property
    def computer(self) -> Any:
        """The connected CUA ``Computer``, or None before ``connect_computer()``."""
        ...

    async def ensure_connected(self) -> None:
        """Make sure the VM is running and a ``Computer`` is connected."""
        ...

    async def run_osascript(self, script: str) -> str:
        """Run AppleScript in the VM and return stripped stdout. Raises if not connected."""
        ...

    async def run_command(self, command: str) -> Any:
        """Run a shell command in the VM (``computer.interface.run_command``)."""
        ...
