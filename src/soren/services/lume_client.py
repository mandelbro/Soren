"""Async client for the Lume HTTP API (``lume serve``, default http://localhost:7777)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

_PULL_POLL_INTERVAL_S = 5.0
_PULL_TIMEOUT_S = 3600.0


class LumeError(Exception):
    """Base class for every Lume failure, so callers can catch them with one clause."""


class LumeConnectionError(LumeError):
    """Raised when Lume HTTP API is unreachable."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class LumeAPIError(LumeError):
    """Raised when Lume answers with an error status (e.g. 409 guest limit, 400 low disk)."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Lume API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


def _error_message(resp: httpx.Response) -> str:
    """Lume error bodies are ``{"message": ...}``; fall back to raw text otherwise."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return body["message"]
    return resp.text


class LumeClient:
    """Async wrapper around Lume's REST API at http://localhost:7777."""

    def __init__(
        self,
        base_url: str = "http://localhost:7777",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self._base_url}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Execute HTTP request, translating transport and HTTP failures into Lume errors."""
        url = self._url(path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                resp = await client.request(method, url, json=json, params=params)
                if resp.is_error:
                    raise LumeAPIError(resp.status_code, _error_message(resp))
                return resp.json() if resp.content else {}
        except httpx.ConnectError as e:
            raise LumeConnectionError(
                f"Cannot connect to Lume API at {self._base_url}. Is `lume serve` running?",
                cause=e,
            ) from e
        except httpx.TimeoutException as e:
            raise LumeConnectionError(
                f"Lume API request timed out after {self._timeout}s.",
                cause=e,
            ) from e

    async def _request_dict(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute request for endpoints that return a JSON object."""
        result = await self._request(method, path, json=json, params=params)
        return result if isinstance(result, dict) else {}

    async def list_vms(self, storage: str | None = None) -> list[dict[str, Any]]:
        """List all VMs."""
        params = {"storage": storage} if storage else None
        result = await self._request("GET", "/lume/vms", params=params)
        return result if isinstance(result, list) else []

    async def get_vm(self, name: str, storage: str | None = None) -> dict[str, Any]:
        """Get VM details by name."""
        params = {"storage": storage} if storage else None
        return await self._request_dict("GET", f"/lume/vms/{name}", params=params)

    async def create_vm(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new VM (async, returns 202)."""
        return await self._request_dict("POST", "/lume/vms", json=config)

    async def delete_vm(self, name: str, storage: str | None = None) -> dict[str, Any]:
        """Delete a VM and its files."""
        params = {"storage": storage} if storage else None
        return await self._request_dict("DELETE", f"/lume/vms/{name}", params=params)

    async def clone_vm(
        self,
        source: str,
        target: str,
        *,
        source_location: str | None = None,
        dest_location: str | None = None,
    ) -> dict[str, Any]:
        """Clone a VM."""
        payload: dict[str, Any] = {"name": source, "newName": target}
        if source_location:
            payload["sourceLocation"] = source_location
        if dest_location:
            payload["destLocation"] = dest_location
        return await self._request_dict("POST", "/lume/vms/clone", json=payload)

    async def start_vm(
        self,
        name: str,
        *,
        no_display: bool = False,
        storage: str | None = None,
    ) -> dict[str, Any]:
        """Start a VM with VNC explicitly enabled (screenshots are captured over VNC)."""
        payload: dict[str, Any] = {"noDisplay": no_display, "vnc": "enabled"}
        if storage:
            payload["storage"] = storage
        return await self._request_dict("POST", f"/lume/vms/{name}/run", json=payload)

    async def stop_vm(self, name: str, storage: str | None = None, *, force: bool = False) -> dict[str, Any]:
        """Stop a running VM. Graceful by default; ``force`` skips the guest shutdown wait."""
        payload: dict[str, Any] = {}
        if force:
            payload["force"] = True
        if storage:
            payload["storage"] = storage
        return await self._request_dict("POST", f"/lume/vms/{name}/stop", json=payload)

    async def pull_image(
        self,
        image: str,
        name: str | None = None,
        *,
        poll_interval: float = _PULL_POLL_INTERVAL_S,
        timeout_s: float = _PULL_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Pull a VM image and wait for it to finish; returns the VM's final details.

        ``POST /lume/pull`` holds the connection for the whole download, so this starts an
        async pull and polls ``GET /lume/vms/:name`` (status ``pulling``) instead. A failed
        pull surfaces as a 400 on that status endpoint and raises ``LumeAPIError``.
        """
        payload: dict[str, Any] = {"image": image}
        if name:
            payload["name"] = name
        await self._request_dict("POST", "/lume/pull/start", json=payload)

        vm_name = name or image.split(":", 1)[0]
        deadline = time.monotonic() + timeout_s
        while True:
            info = await self.get_vm(vm_name)
            if info.get("status") != "pulling":
                return info
            if time.monotonic() >= deadline:
                raise LumeError(f"Pull of {image} into '{vm_name}' did not finish within {timeout_s:.0f}s")
            await asyncio.sleep(poll_interval)
