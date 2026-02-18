"""
CHATHAN Providers — CHATHAN Worker Provider

The primary execution provider.  Dispatches actions to the
laptop agent via HTTP API → WebSocket → CHATHAN Worker.

This wraps the existing gateway HTTP API that communicates with the
agent over the WebSocket connection.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger("skynet.provider.chathan")


class ChathanProvider:
    """Execute via the CHATHAN Worker (laptop agent over WebSocket)."""

    def __init__(self, gateway_api_url: str = "http://127.0.0.1:8766"):
        """
        Initialize ChathanProvider.

        Args:
            gateway_api_url: URL of the OpenClaw Gateway HTTP API
        """
        self.name = "chathan"
        self.gateway_api_url = gateway_api_url
        logger.info(f"Chathan provider initialized - gateway: {gateway_api_url}")

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute action via the OpenClaw Gateway HTTP API.

        This is a synchronous wrapper that runs the async HTTP call.

        Args:
            action: Action type (e.g., 'git_status', 'execute_command')
            params: Action parameters

        Returns:
            Execution result with status, output, exit_code
        """
        logger.info(f"[CHATHAN] Executing {action} with params: {params}")

        # Run the async operation synchronously (Celery tasks are sync)
        try:
            result = asyncio.run(self._execute_async(action, params))
            return result
        except Exception as e:
            logger.error(f"[CHATHAN] Failed to execute {action}: {e}")
            return {
                "status": "error",
                "output": f"Gateway communication error: {e}",
                "action": action,
                "provider": "chathan",
                "exit_code": -1,
            }

    async def _execute_async(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute action asynchronously via HTTP API.

        Args:
            action: Action type
            params: Action parameters

        Returns:
            Execution result
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.gateway_api_url}/action",
                    json={
                        "action": action,
                        "params": params,
                        "confirmed": True,  # Pre-approved via SKYNET orchestration
                    },
                    timeout=aiohttp.ClientTimeout(total=130),
                ) as resp:
                    data = await resp.json()

                    # Map gateway response to worker format
                    if data.get("status") == "error":
                        return {
                            "status": "error",
                            "output": data.get("error", "Unknown error"),
                            "action": action,
                            "provider": "chathan",
                            "exit_code": 1,
                        }

                    # Extract result from gateway response
                    result = data.get("result", {})
                    stdout = result.get("stdout", "")
                    stderr = result.get("stderr", "")
                    returncode = result.get("returncode", 0)

                    output = stdout
                    if stderr:
                        output += f"\n[STDERR]: {stderr}"

                    return {
                        "status": "success" if returncode == 0 else "error",
                        "output": output,
                        "action": action,
                        "provider": "chathan",
                        "exit_code": returncode,
                    }

        except aiohttp.ClientError as e:
            return {
                "status": "error",
                "output": f"Gateway unreachable: {e}",
                "action": action,
                "provider": "chathan",
                "exit_code": -1,
            }
        except Exception as e:
            return {
                "status": "error",
                "output": f"Unexpected error: {e}",
                "action": action,
                "provider": "chathan",
                "exit_code": -1,
            }

    def health_check(self) -> dict[str, Any]:
        """
        Check if the OpenClaw Gateway is available and an agent is connected.

        Returns:
            Health check result
        """
        try:
            result = asyncio.run(self._health_check_async())
            return result
        except Exception as e:
            logger.error(f"[CHATHAN] Health check failed: {e}")
            return {
                "status": "unhealthy",
                "provider": "chathan",
                "error": str(e),
            }

    async def _health_check_async(self) -> dict[str, Any]:
        """Check gateway status asynchronously."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.gateway_api_url}/status",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()
                    agent_connected = data.get("agent_connected", False)

                    if agent_connected:
                        return {
                            "status": "healthy",
                            "provider": "chathan",
                            "agent_connected": True,
                        }
                    else:
                        return {
                            "status": "degraded",
                            "provider": "chathan",
                            "agent_connected": False,
                            "message": "Gateway online but no agent connected",
                        }

        except aiohttp.ClientError as e:
            return {
                "status": "unhealthy",
                "provider": "chathan",
                "error": f"Gateway unreachable: {e}",
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        """
        Send emergency stop to the agent.

        Args:
            job_id: Job ID to cancel

        Returns:
            Cancellation result
        """
        try:
            result = asyncio.run(self._cancel_async(job_id))
            return result
        except Exception as e:
            logger.error(f"[CHATHAN] Cancellation failed: {e}")
            return {
                "status": "error",
                "provider": "chathan",
                "error": str(e),
            }

    async def _cancel_async(self, job_id: str) -> dict[str, Any]:
        """Send emergency stop asynchronously."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.gateway_api_url}/emergency-stop",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()
                    if data.get("status") == "emergency_stop_sent":
                        return {
                            "status": "success",
                            "provider": "chathan",
                            "message": f"Emergency stop sent for job {job_id}",
                        }
                    else:
                        return {
                            "status": "error",
                            "provider": "chathan",
                            "error": "Emergency stop failed",
                        }

        except aiohttp.ClientError as e:
            return {
                "status": "error",
                "provider": "chathan",
                "error": f"Gateway unreachable: {e}",
            }
