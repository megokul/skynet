"""
Memory Models — Data structures for SKYNET's cognitive memory system.

Defines all memory record types and their schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class MemoryType(str, Enum):
    """Types of memories SKYNET can store."""

    TASK_EXECUTION = "task_execution"  # Complete task execution with results
    FAILURE_PATTERN = "failure_pattern"  # Recurring failure modes
    SUCCESS_STRATEGY = "success_strategy"  # Proven successful approaches
    SYSTEM_STATE = "system_state"  # System state snapshots
    USER_PREFERENCE = "user_preference"  # Learned user preferences
    ENVIRONMENT_FACT = "environment_fact"  # Knowledge about environment
    PROVIDER_PERFORMANCE = "provider_performance"  # Provider success rates


@dataclass
class MemoryRecord:
    """
    Base memory record structure.

    All memories are stored with this structure in PostgreSQL.
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    memory_type: MemoryType = MemoryType.TASK_EXECUTION
    content: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    retrieval_count: int = 0
    importance_score: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "memory_type": self.memory_type.value,
            "content": self.content,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "retrieval_count": self.retrieval_count,
            "importance_score": self.importance_score,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryRecord:
        """Create MemoryRecord from dictionary."""
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            memory_type=MemoryType(data["memory_type"]),
            content=data["content"],
            metadata=data.get("metadata", {}),
            embedding=data.get("embedding"),
            retrieval_count=data.get("retrieval_count", 0),
            importance_score=data.get("importance_score", 0.0),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass
class TaskMemory:
    """
    Memory of a specific task execution.

    Stores complete execution context and results for learning.
    """

    request_id: str
    user_message: str
    plan: dict[str, Any]
    execution_result: dict[str, Any] | None = None
    risk_level: str = "LOW"
    duration_seconds: int = 0
    success: bool = False
    provider: str = "unknown"
    target: str = "unknown"
    error_message: str | None = None
    artifacts: list[str] = field(default_factory=list)
    learned_strategy: str | None = None

    def to_memory_record(self) -> MemoryRecord:
        """Convert to generic MemoryRecord for storage."""
        return MemoryRecord(
            memory_type=MemoryType.TASK_EXECUTION,
            content={
                "request_id": self.request_id,
                "user_message": self.user_message,
                "plan": self.plan,
                "execution_result": self.execution_result,
                "risk_level": self.risk_level,
                "duration_seconds": self.duration_seconds,
                "success": self.success,
                "provider": self.provider,
                "target": self.target,
                "error_message": self.error_message,
                "artifacts": self.artifacts,
                "learned_strategy": self.learned_strategy,
            },
            metadata={
                "provider": self.provider,
                "risk_level": self.risk_level,
                "success": self.success,
            },
        )


@dataclass
class FailurePattern:
    """
    Memory of a recurring failure pattern.

    Helps SKYNET avoid repeating mistakes.
    """

    error_type: str
    error_message: str
    context: dict[str, Any]
    occurrence_count: int = 1
    last_occurrence: datetime = field(default_factory=datetime.utcnow)
    suggested_fix: str | None = None
    related_tasks: list[str] = field(default_factory=list)

    def to_memory_record(self) -> MemoryRecord:
        """Convert to generic MemoryRecord for storage."""
        return MemoryRecord(
            memory_type=MemoryType.FAILURE_PATTERN,
            content={
                "error_type": self.error_type,
                "error_message": self.error_message,
                "context": self.context,
                "occurrence_count": self.occurrence_count,
                "last_occurrence": self.last_occurrence.isoformat(),
                "suggested_fix": self.suggested_fix,
                "related_tasks": self.related_tasks,
            },
            metadata={
                "error_type": self.error_type,
                "occurrence_count": self.occurrence_count,
            },
        )


@dataclass
class SuccessStrategy:
    """
    Memory of a proven successful strategy.

    Helps SKYNET reuse what works.
    """

    strategy_name: str
    description: str
    context: dict[str, Any]
    success_rate: float = 1.0
    times_used: int = 1
    avg_duration_seconds: int = 0
    applicable_to: list[str] = field(default_factory=list)  # Task types
    prerequisites: list[str] = field(default_factory=list)

    def to_memory_record(self) -> MemoryRecord:
        """Convert to generic MemoryRecord for storage."""
        return MemoryRecord(
            memory_type=MemoryType.SUCCESS_STRATEGY,
            content={
                "strategy_name": self.strategy_name,
                "description": self.description,
                "context": self.context,
                "success_rate": self.success_rate,
                "times_used": self.times_used,
                "avg_duration_seconds": self.avg_duration_seconds,
                "applicable_to": self.applicable_to,
                "prerequisites": self.prerequisites,
            },
            metadata={
                "strategy_name": self.strategy_name,
                "success_rate": self.success_rate,
            },
        )


@dataclass
class SystemStateSnapshot:
    """
    Memory of system state at a point in time.

    Tracks provider health, worker status, resource usage, etc.
    """

    state_type: str  # provider_health, worker_status, resource_usage
    state_data: dict[str, Any]
    snapshot_timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_memory_record(self) -> MemoryRecord:
        """Convert to generic MemoryRecord for storage."""
        return MemoryRecord(
            memory_type=MemoryType.SYSTEM_STATE,
            timestamp=self.snapshot_timestamp,
            content={
                "state_type": self.state_type,
                "state_data": self.state_data,
                "snapshot_timestamp": self.snapshot_timestamp.isoformat(),
            },
            metadata={
                "state_type": self.state_type,
            },
        )


@dataclass
class ImportanceScore:
    """
    Calculated importance score for a memory.

    Used for ranking memories during retrieval.
    """

    recency_score: float = 0.0
    success_score: float = 0.0
    relevance_score: float = 0.0
    frequency_score: float = 0.0
    total_score: float = 0.0

    # Weights for scoring (can be tuned)
    RECENCY_WEIGHT = 0.25
    SUCCESS_WEIGHT = 0.30
    RELEVANCE_WEIGHT = 0.35
    FREQUENCY_WEIGHT = 0.10

    @classmethod
    def calculate(
        cls,
        recency: float,
        success: float,
        relevance: float,
        frequency: float,
    ) -> ImportanceScore:
        """Calculate weighted importance score."""
        total = (
            recency * cls.RECENCY_WEIGHT
            + success * cls.SUCCESS_WEIGHT
            + relevance * cls.RELEVANCE_WEIGHT
            + frequency * cls.FREQUENCY_WEIGHT
        )

        return cls(
            recency_score=recency,
            success_score=success,
            relevance_score=relevance,
            frequency_score=frequency,
            total_score=total,
        )
