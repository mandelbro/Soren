"""LumeClient behaviour against the Lume 0.7.x HTTP API.

Requests go through httpx.MockTransport so assertions cover what actually goes over the
wire (paths, JSON bodies) and how real status codes are surfaced.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from soren.services.lume_client import LumeAPIError, LumeClient, LumeConnectionError, LumeError

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> tuple[LumeClient, list[httpx.Request]]:
    """Build a client whose requests are recorded and answered by ``handler``."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return LumeClient(transport=httpx.MockTransport(recording)), seen


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content) if request.content else {}


class TestApiErrors:
    async def test_http_error_raises_lume_api_error_with_server_message(self) -> None:
        """Lume 0.7 returns 409 when two macOS guests already run; the server message is kept."""
        client, _ = _client(lambda r: httpx.Response(409, json={"message": "Maximum of 2 macOS VMs running"}))

        with pytest.raises(LumeAPIError) as exc_info:
            await client.start_vm("soren-vm")

        assert exc_info.value.status_code == 409
        assert "Maximum of 2 macOS VMs running" in str(exc_info.value)

    async def test_api_error_message_names_lume_for_retry_classification(self) -> None:
        """RetryPolicy classifies by message text; 'lume' routes the failure to vm_error."""
        client, _ = _client(lambda r: httpx.Response(400, json={"message": "Not enough disk space"}))

        with pytest.raises(LumeAPIError) as exc_info:
            await client.start_vm("soren-vm")

        assert "lume" in str(exc_info.value).lower()

    async def test_non_json_error_body_falls_back_to_text(self) -> None:
        client, _ = _client(lambda r: httpx.Response(400, text="Missing VM name"))

        with pytest.raises(LumeAPIError) as exc_info:
            await client.stop_vm("soren-vm")

        assert "Missing VM name" in str(exc_info.value)

    def test_api_and_connection_errors_share_lume_error_base(self) -> None:
        """Callers can catch every Lume failure with one except clause."""
        assert issubclass(LumeAPIError, LumeError)
        assert issubclass(LumeConnectionError, LumeError)


class TestRunAndStop:
    async def test_start_vm_requests_vnc_explicitly(self) -> None:
        """Screenshots depend on VNC, so the run request asks for it rather than relying on the default."""
        client, seen = _client(lambda r: httpx.Response(202, json={"message": "VM start initiated"}))

        await client.start_vm("soren-vm")

        assert seen[0].method == "POST"
        assert seen[0].url.path == "/lume/vms/soren-vm/run"
        assert _body(seen[0]) == {"noDisplay": False, "vnc": "enabled"}

    async def test_stop_vm_is_graceful_by_default(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json={"message": "VM stopped successfully"}))

        await client.stop_vm("soren-vm")

        assert seen[0].url.path == "/lume/vms/soren-vm/stop"
        assert "force" not in _body(seen[0])

    async def test_stop_vm_force_sends_force_flag(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json={"message": "VM stopped successfully"}))

        await client.stop_vm("soren-vm", force=True)

        assert _body(seen[0])["force"] is True


class TestPullImage:
    async def test_pull_starts_async_and_polls_until_no_longer_pulling(self) -> None:
        """POST /lume/pull blocks for the whole download, so pulls use /pull/start + status polling."""
        statuses = iter(["pulling", "pulling", "stopped"])

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={"message": "Pull started", "name": "soren-pristine"})
            return httpx.Response(200, json={"name": "soren-pristine", "status": next(statuses)})

        client, seen = _client(handler)

        info = await client.pull_image("macos-sequoia-cua:latest", name="soren-pristine", poll_interval=0)

        assert seen[0].url.path == "/lume/pull/start"
        assert _body(seen[0]) == {"image": "macos-sequoia-cua:latest", "name": "soren-pristine"}
        assert [r.url.path for r in seen[1:]] == ["/lume/vms/soren-pristine"] * 3
        assert info["status"] == "stopped"

    async def test_pull_without_name_polls_the_name_lume_derives_from_the_image(self) -> None:
        """Lume names the VM after the image with its tag removed when no name is given."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={"message": "Pull started"})
            return httpx.Response(200, json={"status": "stopped"})

        client, seen = _client(handler)

        await client.pull_image("macos-sequoia-cua:latest", poll_interval=0)

        assert "name" not in _body(seen[0])
        assert seen[1].url.path == "/lume/vms/macos-sequoia-cua"

    async def test_failed_pull_raises_api_error(self) -> None:
        """Lume reports a failed async pull as a 400 on the status endpoint."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={"message": "Pull started"})
            return httpx.Response(400, json={"message": "Pull failed for 'soren-pristine': manifest unknown"})

        client, _ = _client(handler)

        with pytest.raises(LumeAPIError) as exc_info:
            await client.pull_image("bad:image", name="soren-pristine", poll_interval=0)

        assert "manifest unknown" in str(exc_info.value)

    async def test_pull_that_never_finishes_raises_after_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={"message": "Pull started"})
            return httpx.Response(200, json={"status": "pulling", "downloadProgress": 12})

        client, _ = _client(handler)

        with pytest.raises(LumeError) as exc_info:
            await client.pull_image("big:image", name="soren-pristine", poll_interval=0, timeout_s=0)

        assert "soren-pristine" in str(exc_info.value)
