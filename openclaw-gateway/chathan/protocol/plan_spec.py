"""
CHATHAN Protocol — Plan Specification

A PlanSpec is the structured output of the SKYNET ORACLE planning phase.
It describes *what* needs to be done, broken into steps with agent role
assignments, risk levels, and time estimates.

Flow: User request → AI planner → PlanSpec → approval → ExecutionSpec(s)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    """One discrete unit of work within a plan."""

    title: str
    description: str
    agent_role: str           # which agent handles this (e.g. "backend", "frontend")
    risk_level: str = "WRITE" # READ_ONLY | WRITE | ADMIN
    estimated_minutes: int = 5
    dependencies: list[int] = field(default_factory=list)  # indices of prerequisite steps
    skills_required: list[str] = field(default_factory=list)  # e.g. ["git", "filesystem"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "agent_role": self.agent_role,
            "risk_level": self.risk_level,
            "estimated_minutes": self.estimated_minutes,
            "dependencies": self.dependencies,
            "skills_required": self.skills_required,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanStep:
        return cls(
            title=d["title"],
            description=d.get("description", ""),
            agent_role=d.get("agent_role", "backend"),
            risk_level=d.get("risk_level", "WRITE"),
            estimated_minutes=d.get("estimated_minutes", 5),
            dependencies=d.get("dependencies", []),
            skills_required=d.get("skills_required", []),
        )


@dataclass
class PlanSpec:
    """
    Complete plan for a job — produced by the SKYNET ORACLE.

    A PlanSpec is immutable once approved.  Each step maps to one or more
    ExecutionSpecs that CHATHAN Workers execute.
    """

    job_id: str
    project_id: str
    summary: str
    tech_stack: dict[str, Any] = field(default_factory=dict)
    steps: list[PlanStep] = field(default_factory=list)
    total_estimated_minutes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.total_estimated_minutes == 0 and self.steps:
            self.total_estimated_minutes = sum(s.estimated_minutes for s in self.steps)

    @property
    def max_risk_level(self) -> str:
        """Return the highest risk level across all steps."""
        levels = {"READ_ONLY": 0, "WRITE": 1, "ADMIN": 2}
        if not self.steps:
            return "READ_ONLY"
        max_level = max(levels.get(s.risk_level, 1) for s in self.steps)
        return {0: "READ_ONLY", 1: "WRITE", 2: "ADMIN"}[max_level]

    @property
    def agent_roles_needed(self) -> set[str]:
        """Return the set of agent roles required by this plan."""
        return {s.agent_role for s in self.steps}

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project_id": self.project_id,
            "summary": self.summary,
            "tech_stack": self.tech_stack,
            "steps": [s.to_dict() for s in self.steps],
            "total_estimated_minutes": self.total_estimated_minutes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanSpec:
        steps = [PlanStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            job_id=d["job_id"],
            project_id=d["project_id"],
            summary=d.get("summary", ""),
            tech_stack=d.get("tech_stack", {}),
            steps=steps,
            total_estimated_minutes=d.get("total_estimated_minutes", 0),
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def from_ai_plan(cls, project_id: str, job_id: str, plan_dict: dict[str, Any]) -> PlanSpec:
        """
        Convert AI-generated plan JSON into a validated PlanSpec.

        Expected AI output format::

            {
                "summary": "Build a REST API...",
                "tech_stack": {"language": "python", "framework": "fastapi"},
                "tasks": [
                    {
                        "title": "Set up project structure",
                        "description": "Create directories and init files",
                        "milestone": "foundation",
                        "risk": "WRITE"
                    },
                    ...
                ]
            }
        """
        tasks = plan_dict.get("tasks", plan_dict.get("steps", []))
        steps = []
        for i, task in enumerate(tasks):
            steps.append(PlanStep(
                title=task.get("title", f"Step {i + 1}"),
                description=task.get("description", ""),
                agent_role=task.get("agent_role", task.get("assigned_to", "backend")),
                risk_level=task.get("risk", task.get("risk_level", "WRITE")),
                estimated_minutes=task.get("estimated_minutes", 5),
                dependencies=task.get("dependencies", []),
                skills_required=task.get("skills_required", []),
            ))

        return cls(
            job_id=job_id,
            project_id=project_id,
            summary=plan_dict.get("summary", ""),
            tech_stack=plan_dict.get("tech_stack", {}),
            steps=steps,
            metadata=plan_dict.get("metadata", {}),
        )
