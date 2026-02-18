"""
Event Types — Event data structures and type definitions.

Defines all event types used in SKYNET's reactive intelligence system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(Enum):
    """
    All event types in SKYNET.

    Categories:
    - Task Lifecycle: Events related to task execution flow
    - System Events: Worker and provider status changes
    - Error Events: Failures and problems
    - Opportunity Events: System conditions enabling proactive actions
    """

    # ========================================================================
    # Task Lifecycle Events
    # ========================================================================

    TASK_CREATED = "task.created"
    """Task created by user or system."""

    TASK_PLANNED = "task.planned"
    """Plan generated for task."""

    TASK_APPROVED = "task.approved"
    """Plan approved (manually or auto)."""

    TASK_DENIED = "task.denied"
    """Plan denied by user."""

    TASK_QUEUED = "task.queued"
    """Task queued for execution."""

    TASK_STARTED = "task.started"
    """Execution started by worker."""

    TASK_STEP_COMPLETED = "task.step_completed"
    """Single step within task completed."""

    TASK_COMPLETED = "task.completed"
    """Task execution completed successfully."""

    TASK_FAILED = "task.failed"
    """Task execution failed."""

    TASK_CANCELLED = "task.cancelled"
    """Task cancelled by user or system."""

    # ========================================================================
    # System Events
    # ========================================================================

    WORKER_ONLINE = "worker.online"
    """Worker registered and available."""

    WORKER_OFFLINE = "worker.offline"
    """Worker went offline or timed out."""

    WORKER_HEARTBEAT = "worker.heartbeat"
    """Worker sent heartbeat update."""

    PROVIDER_HEALTHY = "provider.healthy"
    """Execution provider became healthy."""

    PROVIDER_UNHEALTHY = "provider.unhealthy"
    """Execution provider became unhealthy."""

    PROVIDER_DEGRADED = "provider.degraded"
    """Execution provider performance degraded."""

    # ========================================================================
    # Error Events
    # ========================================================================

    ERROR_DETECTED = "error.detected"
    """Generic error detected."""

    DEPLOYMENT_FAILED = "deployment.failed"
    """Deployment operation failed."""

    TIMEOUT_OCCURRED = "timeout.occurred"
    """Operation timed out."""

    RESOURCE_EXHAUSTED = "resource.exhausted"
    """System resources exhausted (disk, memory, etc.)."""

    # ========================================================================
    # Opportunity Events (for autonomous initiative)
    # ========================================================================

    SYSTEM_IDLE = "system.idle"
    """System has been idle with no active tasks."""

    RESOURCE_AVAILABLE = "resource.available"
    """System resources available for opportunistic work."""

    OPTIMIZATION_OPPORTUNITY = "optimization.opportunity"
    """System detected optimization opportunity."""

    MAINTENANCE_DUE = "maintenance.due"
    """Scheduled maintenance is due."""


@dataclass
class Event:
    """
    Base event class for all SKYNET events.

    Events are immutable records of things that happened in the system.
    They flow through the EventBus to registered handlers.

    Attributes:
        type: Event type (from EventType enum or custom string)
        payload: Event-specific data
        source: Where event originated (worker, orchestrator, sentinel, etc.)
        timestamp: When event occurred (auto-generated)
        metadata: Additional context (optional)
    """

    type: str | EventType
    payload: dict[str, Any]
    source: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Normalize EventType to string."""
        if isinstance(self.type, EventType):
            self.type = self.type.value

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary format."""
        return {
            "type": self.type,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        """Create event from dictionary."""
        return cls(
            type=data["type"],
            payload=data["payload"],
            source=data["source"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        """String representation for logging."""
        return (
            f"Event(type={self.type}, source={self.source}, "
            f"timestamp={self.timestamp.isoformat()})"
        )


# ============================================================================
# Convenience Event Creators
# ============================================================================


def task_event(
    event_type: EventType, job_id: str, source: str, **extra_payload
) -> Event:
    """
    Create a task lifecycle event.

    Args:
        event_type: Task event type
        job_id: Job identifier
        source: Event source
        **extra_payload: Additional payload fields

    Returns:
        Event instance
    """
    payload = {"job_id": job_id, **extra_payload}
    return Event(type=event_type, payload=payload, source=source)


def system_event(
    event_type: EventType, component: str, source: str, **extra_payload
) -> Event:
    """
    Create a system event.

    Args:
        event_type: System event type
        component: Component name (worker_id, provider_name, etc.)
        source: Event source
        **extra_payload: Additional payload fields

    Returns:
        Event instance
    """
    payload = {"component": component, **extra_payload}
    return Event(type=event_type, payload=payload, source=source)


def error_event(
    event_type: EventType,
    error_message: str,
    source: str,
    context: dict[str, Any] | None = None,
) -> Event:
    """
    Create an error event.

    Args:
        event_type: Error event type
        error_message: Error description
        source: Event source
        context: Error context (optional)

    Returns:
        Event instance
    """
    payload = {"error_message": error_message, "context": context or {}}
    return Event(type=event_type, payload=payload, source=source)
