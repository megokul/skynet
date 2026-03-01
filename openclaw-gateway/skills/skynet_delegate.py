"""
SKYNET Delegate Skill

Wraps the OpenClaw gateway HTTP API so that external orchestrators can
dispatch actions and query system state without knowing the gateway's
internal structure.

Tools exposed:
    skynet_route_task   — POST /action  (dispatch an action to the CLAW worker)
    skynet_system_state — GET  /status  (check worker / SSH connectivity)
"""
from __future__ import annotations

from typing import Any

import httpx

from skills.base import SkillContext


class SkynetDelegateSkill:
    name    = "skynet_delegate"
    version = "1.0.0"

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name":        "skynet_route_task",
                "description": (
                    "Dispatch an action to the connected CLAW worker via the "
                    "OpenClaw gateway. Returns the worker's result."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type":        "string",
                            "description": "Action name (e.g. git_status, run_coding_agent).",
                        },
                        "params": {
                            "type":        "object",
                            "description": "Action-specific parameters.",
                        },
                        "confirmed": {
                            "type":        "boolean",
                            "description": "Skip terminal confirmation on the worker.",
                            "default":     False,
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name":        "skynet_system_state",
                "description": (
                    "Return the current state of the OpenClaw gateway: whether "
                    "a CLAW worker is connected and SSH fallback availability."
                ),
                "input_schema": {
                    "type":       "object",
                    "properties": {},
                },
            },
        ]

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: SkillContext,
    ) -> dict[str, Any]:
        if tool_name == "skynet_route_task":
            return await self._route_task(tool_input, context)
        if tool_name == "skynet_system_state":
            return await self._system_state(context)
        raise ValueError(f"Unknown tool: {tool_name!r}")

    # ── Private helpers ────────────────────────────────────────────────────

    async def _route_task(
        self,
        tool_input: dict[str, Any],
        context: SkillContext,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "action":    tool_input["action"],
            "params":    tool_input.get("params", {}),
            "confirmed": bool(tool_input.get("confirmed", False)),
        }
        async with httpx.AsyncClient(timeout=1800) as client:
            resp = await client.post(
                f"{context.gateway_api_url}/action",
                json=body,
            )
        resp.raise_for_status()
        return resp.json()

    async def _system_state(self, context: SkillContext) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{context.gateway_api_url}/status")
        resp.raise_for_status()
        return resp.json()
