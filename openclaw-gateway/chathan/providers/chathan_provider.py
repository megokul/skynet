"""
CHATHAN Providers — CHATHAN Worker Provider

The primary execution provider.  Dispatches ExecutionSpec steps to the
laptop agent via HTTP API → WebSocket → CHATHAN Worker.

This wraps the existing gateway HTTP API that communicates with the
agent over the WebSocket connection.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from chathan.protocol.execution_spec import ExecutionSpec
from .base_provider import BaseExecutionProvider, ExecutionResult

logger = logging.getLogger("skynet.provider.chathan")


class ChathanProvider(BaseExecutionProvider):
    """Execute via the CHATHAN Worker (laptop agent over WebSocket)."""

    name = "chathan"

    def __init__(self, gateway_api_url: str = "http://127.0.0.1:8766"):
        self.gateway_api_url = gateway_api_url

    async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        """
        Execute each step in the spec sequentially via the agent HTTP API.

        Steps are sent one at a time.  If any step fails, execution stops
        and the result reflects the failure.
        """
        result = ExecutionResult(job_id=spec.job_id, status="running")
        logs: list[str] = []
        all_step_results: list[dict[str, Any]] = []

        for i, step in enumerate(spec.steps):
            logger.info(
                "Executing step %d/%d: %s (%s)",
                i + 1, len(spec.steps), step.action, step.description,
            )

            # Inject sandbox_root as working_dir if not already set.
            params = dict(step.params)
            if spec.sandbox_root and "working_dir" not in params:
                params["working_dir"] = spec.sandbox_root

            step_result = await self._send_action(
                step.action, params, confirmed=True,
            )
            all_step_results.append({
                "step_id": step.id,
                "action": step.action,
                "result": step_result,
            })

            if step_result.get("status") == "error":
                result.status = "failed"
                result.error = step_result.get("error", "Unknown error")
                result.exit_code = 1
                logs.append(f"FAILED step {i + 1}: {result.error}")
                break

            inner = step_result.get("result", {})
            rc = inner.get("returncode", 0)
            if inner.get("stdout"):
                logs.append(inner["stdout"])
            if inner.get("stderr"):
                logs.append(f"STDERR: {inner['stderr']}")

            if rc != 0:
                result.status = "failed"
                result.error = f"Step {i + 1} ({step.action}) exited with code {rc}"
                result.exit_code = rc
                break
        else:
            # All steps completed successfully.
            result.status = "succeeded"
            result.exit_code = 0

        result.logs = "\n".join(logs)
        result.step_results = all_step_results
        return result

    async def health_check(self) -> bool:
        """Check if the agent is connected by hitting the /status endpoint."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.gateway_api_url}/status",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()
                    return data.get("agent_connected", False)
        except Exception:
            return False

    async def cancel(self, job_id: str) -> bool:
        """Send emergency stop to the agent."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.gateway_api_url}/emergency-stop",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()
                    return data.get("status") == "ok"
        except Exception:
            return False

    async def _send_action(
        self,
        action: str,
        params: dict[str, Any],
        confirmed: bool = True,
    ) -> dict[str, Any]:
        """Send a single action to the agent via the gateway HTTP API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.gateway_api_url}/action",
                    json={
                        "action": action,
                        "params": params,
                        "confirmed": confirmed,
                    },
                    timeout=aiohttp.ClientTimeout(total=130),
                ) as resp:
                    return await resp.json()
        except Exception as exc:
            return {"status": "error", "error": f"Agent unreachable: {exc}"}
