"""Async client for the Lume HTTP API (``lume serve``, default http://localhost:7777)."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel


class LumeConnectionError(Exception):
    """Raised when Lume HTTP API is unreachable."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


# --- Lume API response models ---


class VMInfo(BaseModel):
    """VM details from Lume API."""

    name: str
    status: str = ""
    memory: str = ""
    display: str = ""
    vncUrl: str = ""
    ip: str = ""


# --- LumeClient ---


class LumeClient:
    """Async wrapper around Lume's REST API at http://localhost:7777."""

    def __init__(self, base_url: str = "http://localhost:7777", timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

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
        """Execute HTTP request, handling connection errors."""
        url = self._url(path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(method, url, json=json, params=params)
                resp.raise_for_status()
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
        url = f"/lume/vms/{name}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
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
        """Start a VM."""
        payload: dict[str, Any] = {"noDisplay": no_display}
        if storage:
            payload["storage"] = storage
        return await self._request_dict("POST", f"/lume/vms/{name}/run", json=payload)

    async def stop_vm(self, name: str, storage: str | None = None) -> dict[str, Any]:
        """Stop a running VM."""
        payload: dict[str, Any] = {}
        if storage:
            payload["storage"] = storage
        return await self._request_dict("POST", f"/lume/vms/{name}/stop", json=payload)

    async def pull_image(self, image: str, name: str | None = None) -> dict[str, Any]:
        """Pull a VM image from registry."""
        payload: dict[str, Any] = {"image": image}
        if name:
            payload["name"] = name
        return await self._request_dict("POST", "/lume/pull", json=payload)
