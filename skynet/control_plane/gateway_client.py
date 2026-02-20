"""
OpenClaw gateway API client for SKYNET control plane.

Delegates execution requests to OpenClaw gateway endpoints.
"""

from __future__ import annotations

from typing import Any

import aiohttp


class GatewayClient:
    """HTTP client for OpenClaw gateway interactions."""

    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _base_url(host: str) -> str:
        return host.rstrip("/")

    async def get_gateway_status(self, host: str) -> dict[str, Any]:
        url = f"{self._base_url(host)}/status"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def execute_task(
        self,
        host: str,
        action: str,
        params: dict[str, Any] | None = None,
        confirmed: bool = True,
        task_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url(host)}/action"
        payload = {
            "action": action,
            "params": params or {},
            "confirmed": confirmed,
        }
        if task_id:
            payload["task_id"] = task_id
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        timeout = aiohttp.ClientTimeout(total=max(self.timeout_seconds, 130))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def get_worker_status(self, host: str) -> dict[str, Any]:
        # Current OpenClaw gateway exposes worker connectivity through /status.
        return await self.get_gateway_status(host)

    async def list_sessions(self, host: str) -> list[dict[str, Any]]:
        """
        Best-effort session listing.

        Returns an empty list if the endpoint is not available yet.
        """
        url = f"{self._base_url(host)}/sessions"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(url) as resp:
                    if resp.status == 404:
                        return []
                    resp.raise_for_status()
                    data = await resp.json()
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        sessions = data.get("sessions", [])
                        return sessions if isinstance(sessions, list) else []
            except aiohttp.ClientError:
                return []
        return []
