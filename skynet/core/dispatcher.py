"""
SKYNET Core - Dispatcher

Converts approved plans into executable specs, validates policy, and enqueues
jobs for worker execution.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from skynet.chathan.protocol.execution_spec import ExecutionSpec, ExecutionStep
from skynet.chathan.protocol.plan_spec import PlanSpec
from skynet.policy.engine import PolicyEngine
from skynet.queue.celery_app import enqueue_job
from skynet.shared.errors import PolicyViolationError

# Scheduler import (optional dependency)
try:
    from skynet.scheduler import ProviderScheduler
except ImportError:
    ProviderScheduler = None  # type: ignore

logger = logging.getLogger("skynet.core.dispatcher")

_RISK_ORDER = {"READ_ONLY": 0, "WRITE": 1, "ADMIN": 2}


class Dispatcher:
    """Translate PlanSpec -> ExecutionSpec and enqueue work."""

    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        enqueue_fn: Callable[[str, dict[str, Any]], str] | None = None,
        default_provider: str = "chathan",
        default_sandbox_root: str = ".",
        scheduler: ProviderScheduler | None = None,  # type: ignore
    ) -> None:
        self.policy = policy_engine or PolicyEngine()
        self.enqueue = enqueue_fn or enqueue_job
        self.default_provider = default_provider
        self.default_sandbox_root = default_sandbox_root
        self.scheduler = scheduler

        logger.info(
            f"Dispatcher initialized (default_provider={default_provider}, "
            f"scheduler={'enabled' if scheduler else 'disabled'})"
        )

    async def dispatch(
        self,
        job_id: str,
        plan_spec: PlanSpec | dict[str, Any],
    ) -> ExecutionSpec:
        """
        Build, validate, and enqueue an ExecutionSpec.

        Args:
            job_id: Job identifier to dispatch.
            plan_spec: PlanSpec dataclass or planner-style dict.

        Returns:
            ExecutionSpec that was accepted and queued.
        """
        normalized = self._normalize_plan(job_id, plan_spec)
        exec_spec = await self._plan_to_execution(normalized)
        task_id = self._validate_and_enqueue(exec_spec)
        exec_spec.metadata["queue_task_id"] = task_id

        logger.info(
            "Dispatched job %s with %d execution steps (provider=%s)",
            exec_spec.job_id,
            len(exec_spec.steps),
            exec_spec.provider,
        )
        return exec_spec

    def _normalize_plan(
        self,
        job_id: str,
        plan_spec: PlanSpec | dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize supported plan formats into one internal structure."""
        if isinstance(plan_spec, PlanSpec):
            return {
                "job_id": job_id,
                "project_id": plan_spec.project_id,
                "summary": plan_spec.summary,
                "working_dir": plan_spec.metadata.get("working_dir", self.default_sandbox_root),
                "steps": [
                    {
                        "title": step.title,
                        "description": step.description,
                        "risk_level": step.risk_level,
                    }
                    for step in plan_spec.steps
                ],
                "max_risk_level": plan_spec.max_risk_level,
            }

        steps = plan_spec.get("steps", [])
        normalized_steps = []
        for idx, step in enumerate(steps):
            normalized_steps.append(
                {
                    "title": step.get("title", f"Step {idx + 1}"),
                    "description": step.get("description", ""),
                    "risk_level": step.get("risk_level", "WRITE"),
                }
            )

        max_risk = plan_spec.get("max_risk_level")
        if not max_risk:
            max_risk = self._max_risk_level(normalized_steps)

        return {
            "job_id": job_id,
            "project_id": plan_spec.get("project_id", "default"),
            "summary": plan_spec.get("summary", ""),
            "working_dir": plan_spec.get("working_dir", self.default_sandbox_root),
            "steps": normalized_steps,
            "max_risk_level": max_risk,
        }

    async def _plan_to_execution(self, normalized_plan: dict[str, Any]) -> ExecutionSpec:
        """Map plan steps to concrete execution actions."""
        execution_steps: list[ExecutionStep] = []
        unmapped_steps: list[int] = []

        for idx, plan_step in enumerate(normalized_plan["steps"]):
            mapped = self._map_step_to_action(
                title=plan_step["title"],
                description=plan_step["description"],
                risk_level=plan_step["risk_level"],
                working_dir=normalized_plan["working_dir"],
            )
            if mapped["is_fallback"]:
                unmapped_steps.append(idx)

            execution_steps.append(
                ExecutionStep(
                    action=mapped["action"],
                    params=mapped["params"],
                    timeout_sec=mapped["timeout_sec"],
                    requires_approval=mapped["requires_approval"],
                    description=plan_step["description"] or plan_step["title"],
                )
            )

        # Select provider (use scheduler if available)
        execution_spec_dict = {
            "job_id": normalized_plan["job_id"],
            "steps": [step.to_dict() if hasattr(step, 'to_dict') else step for step in execution_steps],
        }

        if self.scheduler:
            try:
                selected_provider = await self.scheduler.select_provider(
                    execution_spec_dict, fallback=self.default_provider
                )
                logger.info(f"Scheduler selected provider: {selected_provider}")
            except Exception as e:
                logger.warning(f"Scheduler selection failed: {e}, using default: {self.default_provider}")
                selected_provider = self.default_provider
        else:
            selected_provider = self.default_provider
            logger.debug(f"Using default provider (no scheduler): {selected_provider}")

        return ExecutionSpec(
            job_id=normalized_plan["job_id"],
            project_id=normalized_plan["project_id"],
            plan_step_index=0,
            provider=selected_provider,
            risk_level=normalized_plan["max_risk_level"],
            sandbox_root=normalized_plan["working_dir"],
            steps=execution_steps,
            metadata={
                "summary": normalized_plan["summary"],
                "unmapped_steps": unmapped_steps,
            },
        )

    def _validate_and_enqueue(self, exec_spec: ExecutionSpec) -> str:
        """Run policy validation and enqueue if allowed."""
        decision = self.policy.validate_execution(exec_spec)
        if not decision.allowed:
            raise PolicyViolationError(
                message="; ".join(decision.reasons) or "Execution blocked by policy",
                risk_level=decision.risk_level,
            )

        return self.enqueue(exec_spec.job_id, exec_spec.to_dict())

    def _map_step_to_action(
        self,
        title: str,
        description: str,
        risk_level: str,
        working_dir: str,
    ) -> dict[str, Any]:
        """Map plan text into one concrete execution action."""
        text = f"{title} {description}".lower()
        requires_approval = self.policy.requires_approval(risk_level)

        if "git" in text and "status" in text:
            return self._mapped("git_status", {"working_dir": working_dir}, 90, requires_approval)

        if "git" in text and "push" in text:
            return self._mapped("git_push", {"working_dir": working_dir}, 180, requires_approval)

        if "deploy" in text or "production" in text:
            return self._mapped(
                "docker_compose_up",
                {"working_dir": working_dir},
                900,
                requires_approval,
            )

        if re.search(r"\b(pytest|unittest|tests?|testing)\b", text):
            return self._mapped("run_tests", {"working_dir": working_dir}, 300, requires_approval)

        if "lint" in text:
            return self._mapped("lint_project", {"working_dir": working_dir}, 180, requires_approval)

        if "install" in text and ("dependency" in text or "package" in text):
            return self._mapped(
                "install_dependencies",
                {"working_dir": working_dir},
                480,
                requires_approval,
            )

        if "docker" in text and "build" in text:
            return self._mapped("docker_build", {"working_dir": working_dir}, 900, requires_approval)

        if "build" in text:
            return self._mapped("build_project", {"working_dir": working_dir}, 600, requires_approval)

        # Safe fallback for unknown phrasing.
        return {
            "action": "list_directory",
            "params": {"directory": working_dir},
            "timeout_sec": 60,
            "requires_approval": requires_approval,
            "is_fallback": True,
        }

    def _mapped(
        self,
        action: str,
        params: dict[str, Any],
        timeout_sec: int,
        requires_approval: bool,
    ) -> dict[str, Any]:
        return {
            "action": action,
            "params": params,
            "timeout_sec": timeout_sec,
            "requires_approval": requires_approval,
            "is_fallback": False,
        }

    def _max_risk_level(self, steps: list[dict[str, Any]]) -> str:
        max_level = 0
        for step in steps:
            step_level = _RISK_ORDER.get(step.get("risk_level", "WRITE"), 1)
            if step_level > max_level:
                max_level = step_level
        return {0: "READ_ONLY", 1: "WRITE", 2: "ADMIN"}[max_level]
