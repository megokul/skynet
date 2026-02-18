"""
SKYNET Delegate Skill - OpenClaw <-> SKYNET control-plane integration.

Flow:
1. OpenClaw runtime decides an action.
2. OpenClaw calls SKYNET /v1/route-task (optional orchestration hop).
3. SKYNET selects an OpenClaw gateway and forwards execution.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import uuid4

import aiohttp

from .base import BaseSkill, SkillContext

logger = logging.getLogger("skynet.skills.delegate")


class SkynetDelegateSkill(BaseSkill):
    """
    Delegate orchestration decisions to SKYNET control plane.

    Exposes tools for:
    - routing an action through SKYNET's gateway selection layer
    - querying topology/system state
    """

    name = "skynet_delegate"
    description = "Delegate routing and orchestration metadata to SKYNET control plane"
    version = "2.1.0"

    allowed_roles = []
    requires_approval = set()
    plan_auto_approved = {
        "skynet_route_task",
        "skynet_system_state",
    }

    def __init__(self):
        self.skynet_api_url = os.getenv("SKYNET_ORCHESTRATOR_URL", "http://localhost:8000")
        self.skynet_api_key = os.getenv("SKYNET_API_KEY", "").strip()
        logger.info(f"SKYNET Delegate initialized (API: {self.skynet_api_url})")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.skynet_api_key:
            headers["X-API-Key"] = self.skynet_api_key
        return headers

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "skynet_route_task",
                "description": (
                    "Route an action through SKYNET control plane to a selected OpenClaw gateway. "
                    "Use this when orchestration should pick the target gateway."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "Action to execute"},
                        "params": {"type": "object", "description": "Action parameters"},
                        "task_id": {"type": "string", "description": "Optional external task id"},
                        "gateway_id": {
                            "type": "string",
                            "description": "Optional preferred gateway id",
                        },
                        "confirmed": {
                            "type": "boolean",
                            "description": "Set false to request dry-run behavior when supported",
                            "default": True,
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "skynet_system_state",
                "description": "Read SKYNET control-plane topology state (gateways/workers).",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: SkillContext,
    ) -> str:
        del context  # Context currently unused for these stateless API calls.

        if tool_name == "skynet_route_task":
            return await self._route_task(tool_input)
        if tool_name == "skynet_system_state":
            return await self._get_system_state()
        return f"ERROR: Unknown tool '{tool_name}'"

    async def _route_task(self, tool_input: dict[str, Any]) -> str:
        try:
            payload = {
                "task_id": tool_input.get("task_id") or f"task-{uuid4().hex[:12]}",
                "action": tool_input["action"],
                "params": tool_input.get("params", {}),
                "gateway_id": tool_input.get("gateway_id"),
                "confirmed": tool_input.get("confirmed", True),
            }

            logger.info(
                f"Routing task via SKYNET (task_id={payload['task_id']}, action={payload['action']})"
            )
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                async with session.post(
                    f"{self.skynet_api_url}/v1/route-task",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"SKYNET route-task failed: {resp.status} - {error_text}")
                        return f"ERROR: SKYNET API returned {resp.status}: {error_text}"
                    result = await resp.json()

            return (
                f"[OK] Routed task {result.get('task_id')}\n"
                f"Gateway: {result.get('gateway_id')} ({result.get('gateway_host')})\n"
                f"Status: {result.get('status')}\n"
                f"Result: {result.get('result')}"
            )
        except aiohttp.ClientError as exc:
            logger.error(f"Failed to connect to SKYNET: {exc}")
            return f"ERROR: Cannot reach SKYNET API at {self.skynet_api_url}: {exc}"
        except Exception as exc:
            logger.error(f"Route task failed: {exc}", exc_info=True)
            return f"ERROR: Route task failed: {exc}"

    async def _get_system_state(self) -> str:
        try:
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                async with session.get(
                    f"{self.skynet_api_url}/v1/system-state",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return f"ERROR: SKYNET API returned {resp.status}: {error_text}"
                    state = await resp.json()

            return (
                "[OK] SKYNET system state\n"
                f"Gateways: {state.get('gateway_count')} | Workers: {state.get('worker_count')}\n"
                f"Timestamp: {state.get('generated_at')}"
            )
        except Exception as exc:
            logger.error(f"System-state query failed: {exc}", exc_info=True)
            return f"ERROR: Failed to query system state: {exc}"
