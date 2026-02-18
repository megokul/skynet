"""
CHATHAN Protocol — Execution Specification

An ExecutionSpec is the concrete execution contract sent from SKYNET Core
to a CHATHAN Worker.  It specifies exactly which actions to perform, in
what order, with what parameters, timeouts, and approval requirements.

Flow: PlanSpec → (per-step) ExecutionSpec → CHATHAN Worker → ExecutionResult
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionStep:
    """One atomic action within an ExecutionSpec."""

    id: str = ""
    action: str = ""              # file_write | git_commit | run_tests | ...
    params: dict[str, Any] = field(default_factory=dict)
    timeout_sec: int = 120
    requires_approval: bool = False
    description: str = ""         # human-readable description

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:8]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "params": self.params,
            "timeout_sec": self.timeout_sec,
            "requires_approval": self.requires_approval,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionStep:
        return cls(
            id=d.get("id", ""),
            action=d["action"],
            params=d.get("params", {}),
            timeout_sec=d.get("timeout_sec", 120),
            requires_approval=d.get("requires_approval", False),
            description=d.get("description", ""),
        )


@dataclass
class ExecutionSpec:
    """
    Complete execution contract for a CHATHAN Worker.

    One ExecutionSpec corresponds to one PlanStep.  The SKYNET Core builds
    ExecutionSpecs from the PlanSpec and dispatches them to the appropriate
    worker/provider.
    """

    job_id: str = ""
    project_id: str = ""
    plan_step_index: int = 0       # which PlanStep this corresponds to
    provider: str = "chathan"      # execution provider (pluggable)
    risk_level: str = "WRITE"      # READ_ONLY | WRITE | ADMIN
    sandbox_root: str = ""         # project working directory
    steps: list[ExecutionStep] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project_id": self.project_id,
            "plan_step_index": self.plan_step_index,
            "provider": self.provider,
            "risk_level": self.risk_level,
            "sandbox_root": self.sandbox_root,
            "steps": [s.to_dict() for s in self.steps],
            "env": self.env,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionSpec:
        steps = [ExecutionStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            job_id=d.get("job_id", ""),
            project_id=d.get("project_id", ""),
            plan_step_index=d.get("plan_step_index", 0),
            provider=d.get("provider", "chathan"),
            risk_level=d.get("risk_level", "WRITE"),
            sandbox_root=d.get("sandbox_root", ""),
            steps=steps,
            env=d.get("env", {}),
            metadata=d.get("metadata", {}),
        )


@dataclass
class ExecutionResult:
    """Result returned by a CHATHAN Worker after executing an ExecutionSpec."""

    job_id: str = ""
    plan_step_index: int = 0
    status: str = "pending"        # pending | running | succeeded | failed | cancelled
    logs: str = ""
    artifacts: list[str] = field(default_factory=list)
    exit_code: int = -1
    error: str = ""
    step_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "plan_step_index": self.plan_step_index,
            "status": self.status,
            "logs": self.logs,
            "artifacts": self.artifacts,
            "exit_code": self.exit_code,
            "error": self.error,
            "step_results": self.step_results,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionResult:
        return cls(
            job_id=d.get("job_id", ""),
            plan_step_index=d.get("plan_step_index", 0),
            status=d.get("status", "pending"),
            logs=d.get("logs", ""),
            artifacts=d.get("artifacts", []),
            exit_code=d.get("exit_code", -1),
            error=d.get("error", ""),
            step_results=d.get("step_results", []),
        )
