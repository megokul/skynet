"""
SKYNET Ledger — Data Models

Data classes for jobs, workers, and state management.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from dataclasses import dataclass, field


# =============================================================================
# Enums
# =============================================================================
class JobStatus(str, Enum):
    """Job lifecycle states."""
    CREATED = "created"
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class WorkerStatus(str, Enum):
    """Worker availability states."""
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    MAINTENANCE = "maintenance"


class RiskLevel(str, Enum):
    """Risk classification levels."""
    READ_ONLY = "READ_ONLY"
    WRITE = "WRITE"
    ADMIN = "ADMIN"


# =============================================================================
# Data Classes
# =============================================================================
@dataclass
class Job:
    """Represents a job in the ledger."""
    id: str
    project_id: str
    status: JobStatus = JobStatus.CREATED
    user_intent: str = ""
    plan_spec: dict = field(default_factory=dict)
    execution_spec: dict = field(default_factory=dict)
    provider: str = "openclaw"
    worker_id: str | None = None
    risk_level: RiskLevel = RiskLevel.WRITE
    approval_required: bool = True
    approved_at: str | None = None
    queued_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    result_summary: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status.value,
            "user_intent": self.user_intent,
            "plan_spec": self.plan_spec,
            "execution_spec": self.execution_spec,
            "provider": self.provider,
            "worker_id": self.worker_id,
            "risk_level": self.risk_level.value,
            "approval_required": self.approval_required,
            "approved_at": self.approved_at,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "result_summary": self.result_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            status=JobStatus(data.get("status", "created")),
            user_intent=data.get("user_intent", ""),
            plan_spec=data.get("plan_spec", {}),
            execution_spec=data.get("execution_spec", {}),
            provider=data.get("provider", "openclaw"),
            worker_id=data.get("worker_id"),
            risk_level=RiskLevel(data.get("risk_level", "WRITE")),
            approval_required=data.get("approval_required", True),
            approved_at=data.get("approved_at"),
            queued_at=data.get("queued_at"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error_message=data.get("error_message"),
            result_summary=data.get("result_summary"),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class Worker:
    """Represents a worker in the registry."""
    id: str
    provider_name: str
    status: WorkerStatus = WorkerStatus.OFFLINE
    capabilities: list[str] = field(default_factory=list)
    current_job_id: str | None = None
    last_heartbeat: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "provider_name": self.provider_name,
            "status": self.status.value,
            "capabilities": self.capabilities,
            "current_job_id": self.current_job_id,
            "last_heartbeat": self.last_heartbeat,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Worker":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            provider_name=data["provider_name"],
            status=WorkerStatus(data.get("status", "offline")),
            capabilities=data.get("capabilities", []),
            current_job_id=data.get("current_job_id"),
            last_heartbeat=data.get("last_heartbeat", datetime.now(timezone.utc).isoformat()),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class JobLock:
    """Represents a job lock for distributed locking."""
    job_id: str
    worker_id: str
    acquired_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None
    
    def is_expired(self) -> bool:
        """Check if lock has expired."""
        if not self.expires_at:
            return False
        expires = datetime.fromisoformat(self.expires_at)
        return datetime.now(timezone.utc) > expires
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "JobLock":
        """Create from dictionary."""
        return cls(
            job_id=data["job_id"],
            worker_id=data["worker_id"],
            acquired_at=data.get("acquired_at", datetime.now(timezone.utc).isoformat()),
            expires_at=data.get("expires_at"),
        )


@dataclass
class PlanSpec:
    """
    Human-readable plan for user approval.
    
    This is what the user sees before approving execution.
    """
    job_id: str
    user_intent: str
    proposed_steps: list[dict[str, str]]
    estimated_risk_level: RiskLevel
    expected_artifacts: list[str]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "job_id": self.job_id,
            "user_intent": self.user_intent,
            "proposed_steps": self.proposed_steps,
            "estimated_risk_level": self.estimated_risk_level.value,
            "expected_artifacts": self.expected_artifacts,
            "created_at": self.created_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PlanSpec":
        """Create from dictionary."""
        return cls(
            job_id=data["job_id"],
            user_intent=data["user_intent"],
            proposed_steps=data.get("proposed_steps", []),
            estimated_risk_level=RiskLevel(data.get("estimated_risk_level", "WRITE")),
            expected_artifacts=data.get("expected_artifacts", []),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )
    
    def to_markdown(self) -> str:
        """Convert to markdown for Telegram display."""
        lines = [
            f"📋 **Plan for Job {self.job_id}**",
            "",
            f"**Intent:** {self.user_intent}",
            "",
            f"**Risk Level:** {self.estimated_risk_level.value}",
            "",
            "**Proposed Steps:**",
        ]
        
        for i, step in enumerate(self.proposed_steps, 1):
            desc = step.get("description", "Unknown step")
            lines.append(f"{i}. {desc}")
        
        if self.expected_artifacts:
            lines.extend(["", "**Expected Artifacts:**"])
            for artifact in self.expected_artifacts:
                lines.append(f"  - {artifact}")
        
        return "\n".join(lines)
