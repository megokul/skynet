"""
SKYNET Delegate Skill - Integration with SKYNET Control Plane API.

This skill connects OpenClaw to the SKYNET orchestrator for AI-powered planning,
policy enforcement, and execution governance.

Flow:
1. User sends request to OpenClaw
2. OpenClaw calls skynet_plan tool
3. Tool sends request to SKYNET /v1/plan endpoint
4. SKYNET returns execution plan with approval gates
5. OpenClaw executes plan steps
6. OpenClaw reports progress via /v1/report endpoint
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
    SKYNET delegation skill for control plane integration.

    Provides tools for:
    - Requesting execution plans from SKYNET
    - Reporting execution progress
    - Validating actions against policy
    """

    name = "skynet_delegate"
    description = "Delegate planning and policy decisions to SKYNET control plane"
    version = "1.0.0"

    # All agents can delegate to SKYNET
    allowed_roles = []

    # Planning itself doesn't require approval (SKYNET handles that)
    requires_approval = set()
    plan_auto_approved = {"skynet_plan", "skynet_report", "skynet_policy_check"}

    def __init__(self):
        """Initialize SKYNET delegate skill."""
        # Get SKYNET API URL from environment
        self.skynet_api_url = os.getenv(
            "SKYNET_ORCHESTRATOR_URL", "http://localhost:8000"
        )
        logger.info(f"SKYNET Delegate initialized (API: {self.skynet_api_url})")

    def get_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions for SKYNET integration."""
        return [
            {
                "name": "skynet_plan",
                "description": (
                    "Request an execution plan from SKYNET control plane. "
                    "SKYNET will analyze the task, generate structured steps, "
                    "classify risk level, and determine approval gates. "
                    "Use this for ANY significant task that requires planning."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_message": {
                            "type": "string",
                            "description": "User's task description or request",
                        },
                        "context": {
                            "type": "object",
                            "description": "Optional context (repo, branch, environment, etc.)",
                            "properties": {
                                "repo": {"type": "string"},
                                "branch": {"type": "string"},
                                "environment": {"type": "string", "enum": ["dev", "staging", "prod"]},
                            },
                        },
                        "constraints": {
                            "type": "object",
                            "description": "Budget and safety constraints",
                            "properties": {
                                "max_cost_usd": {"type": "number", "default": 1.50},
                                "time_budget_min": {"type": "integer", "default": 30},
                                "allowed_targets": {
                                    "type": "array",
                                    "items": {"type": "string", "enum": ["laptop", "ec2", "docker"]},
                                    "default": ["laptop"],
                                },
                            },
                        },
                    },
                    "required": ["user_message"],
                },
            },
            {
                "name": "skynet_report",
                "description": (
                    "Report execution progress back to SKYNET. "
                    "Use this after completing steps to update SKYNET on status."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "request_id": {
                            "type": "string",
                            "description": "Request ID from skynet_plan response",
                        },
                        "step_number": {
                            "type": "integer",
                            "description": "Step number that was executed",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["completed", "failed", "in_progress"],
                            "description": "Step execution status",
                        },
                        "output": {
                            "type": "string",
                            "description": "Step execution output/logs",
                        },
                        "error": {
                            "type": "string",
                            "description": "Error message if status is 'failed'",
                        },
                    },
                    "required": ["request_id", "step_number", "status"],
                },
            },
            {
                "name": "skynet_policy_check",
                "description": (
                    "Check if an action is allowed by SKYNET policy before executing. "
                    "Use this for pre-validation of sensitive operations."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "Action to validate (e.g., 'deploy_prod', 'delete_database')",
                        },
                        "target": {
                            "type": "string",
                            "enum": ["laptop", "ec2", "docker"],
                            "description": "Target execution environment",
                        },
                    },
                    "required": ["action"],
                },
            },
        ]

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: SkillContext,
    ) -> str:
        """Execute SKYNET delegation tool."""
        if tool_name == "skynet_plan":
            return await self._request_plan(tool_input, context)
        elif tool_name == "skynet_report":
            return await self._send_report(tool_input, context)
        elif tool_name == "skynet_policy_check":
            return await self._check_policy(tool_input, context)
        else:
            return f"ERROR: Unknown tool '{tool_name}'"

    async def _request_plan(
        self, tool_input: dict[str, Any], context: SkillContext
    ) -> str:
        """Request execution plan from SKYNET /v1/plan endpoint."""
        try:
            # Generate request ID
            request_id = str(uuid4())

            # Build request payload
            payload = {
                "request_id": request_id,
                "user_message": tool_input["user_message"],
                "context": tool_input.get("context", {
                    "repo": None,
                    "branch": "main",
                    "environment": "dev",
                    "recent_actions": [],
                }),
                "constraints": tool_input.get("constraints", {
                    "max_cost_usd": 1.50,
                    "time_budget_min": 30,
                    "allowed_targets": ["laptop"],
                    "requires_approval_for": ["deploy_prod", "send_email"],
                }),
            }

            # Call SKYNET API
            logger.info(f"Requesting plan from SKYNET (request_id={request_id})")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.skynet_api_url}/v1/plan",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"SKYNET API error: {resp.status} - {error_text}")
                        return f"ERROR: SKYNET API returned {resp.status}: {error_text}"

                    result = await resp.json()

            # Format response for AI
            decision = result["decision"]
            execution_plan = result["execution_plan"]
            approval_gates = result.get("approval_gates", [])

            response_parts = [
                f"[OK] SKYNET Plan Generated (ID: {request_id})",
                f"",
                f"Decision: {decision['mode'].upper()}",
                f"Risk Level: {decision['risk_level'].upper()}",
                f"AI Model Policy: {decision['model_policy']['default']} (escalate to {', '.join(decision['model_policy']['escalation'])})",
            ]

            if decision.get("reason"):
                response_parts.append(f"Reason: {decision['reason']}")

            response_parts.extend([
                f"",
                f"Execution Plan ({len(execution_plan)} steps):",
            ])

            for step in execution_plan:
                agent_emoji = {
                    "coder": "💻",
                    "tester": "🧪",
                    "builder": "🏗️",
                    "deployer": "🚀",
                    "git": "📦",
                    "executor": "⚙️",
                }.get(step["agent"], "🔧")

                response_parts.append(
                    f"  {step['step']}. [{step['agent']}] {step['action']} "
                    f"(target: {step['target']})"
                )

            if approval_gates:
                response_parts.extend([
                    f"",
                    f"⚠️  Approval Gates ({len(approval_gates)}):",
                ])
                for gate in approval_gates:
                    response_parts.append(
                        f"  - {gate['gate']} at step {gate['when_step']}: {gate.get('reason', 'approval required')}"
                    )

            response_parts.extend([
                f"",
                f"Artifacts: {result['artifacts']['s3_prefix']}",
                f"",
                f"Use skynet_report to report progress as you execute steps.",
            ])

            return "\n".join(response_parts)

        except aiohttp.ClientError as e:
            logger.error(f"Failed to connect to SKYNET: {e}")
            return f"ERROR: Cannot reach SKYNET API at {self.skynet_api_url}: {e}"
        except Exception as e:
            logger.error(f"Plan request failed: {e}", exc_info=True)
            return f"ERROR: Plan request failed: {e}"

    async def _send_report(
        self, tool_input: dict[str, Any], context: SkillContext
    ) -> str:
        """Send progress report to SKYNET /v1/report endpoint."""
        try:
            # Build report payload
            payload = {
                "request_id": tool_input["request_id"],
                "step_reports": [
                    {
                        "step": tool_input["step_number"],
                        "status": tool_input["status"],
                        "output": tool_input.get("output"),
                        "error": tool_input.get("error"),
                        "artifacts_uploaded": [],
                    }
                ],
                "overall_status": tool_input["status"],
                "metadata": {},
            }

            # Call SKYNET API
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.skynet_api_url}/v1/report",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return f"ERROR: SKYNET API returned {resp.status}: {error_text}"

                    result = await resp.json()

            response = f"[OK] Progress reported to SKYNET (request_id: {tool_input['request_id']})"
            if result.get("next_action"):
                response += f"\nNext: {result['next_action']}"

            return response

        except Exception as e:
            logger.error(f"Report failed: {e}", exc_info=True)
            return f"ERROR: Failed to send report: {e}"

    async def _check_policy(
        self, tool_input: dict[str, Any], context: SkillContext
    ) -> str:
        """Check action against SKYNET policy."""
        try:
            # Build policy check payload
            payload = {
                "action": tool_input["action"],
                "target": tool_input.get("target"),
                "context": {},
            }

            # Call SKYNET API
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.skynet_api_url}/v1/policy/check",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return f"ERROR: SKYNET API returned {resp.status}: {error_text}"

                    result = await resp.json()

            # Format response
            if result["allowed"]:
                status = "[ALLOWED]"
            else:
                status = "[DENIED]"

            response_parts = [
                f"{status} - {tool_input['action']}",
                f"Risk Level: {result['risk_level'].upper()}",
                f"Requires Approval: {'Yes' if result['requires_approval'] else 'No'}",
                f"Reason: {result['reason']}",
            ]

            return "\n".join(response_parts)

        except Exception as e:
            logger.error(f"Policy check failed: {e}", exc_info=True)
            return f"ERROR: Policy check failed: {e}"
