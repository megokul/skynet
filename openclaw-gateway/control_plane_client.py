from __future__ import annotations

from typing import Any

import aiohttp

import gateway_config as cfg


class ControlPlaneClient:
    """HTTP client for gateway-to-control-plane coordination."""

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None, timeout_seconds: int = 10) -> None:
        self.base_url = str(base_url or cfg.ORCHESTRATOR_URL or "http://localhost:8000").rstrip("/")
        self.api_key = str(api_key or cfg.get_str("SKYNET_API_KEY", "")).strip()
        self.timeout_seconds = max(1, int(timeout_seconds))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _request(self, method: str, path: str, *, json_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
            async with session.request(method, url, json=json_payload) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def acquire_lease(self, lease_name: str, *, owner_id: str, ttl_seconds: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/leases/{lease_name}/acquire",
            json_payload={"owner_id": owner_id, "ttl_seconds": max(1, int(ttl_seconds))},
        )

    async def renew_lease(self, lease_name: str, *, owner_id: str, ttl_seconds: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/leases/{lease_name}/renew",
            json_payload={"owner_id": owner_id, "ttl_seconds": max(1, int(ttl_seconds))},
        )

    async def release_lease(self, lease_name: str, *, owner_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/leases/{lease_name}/release",
            json_payload={"owner_id": owner_id},
        )

    async def inspect_lease(self, lease_name: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/leases/{lease_name}")
