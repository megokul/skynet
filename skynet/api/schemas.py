"""SKYNET API schemas for control-plane endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Service health response."""

    status: str = Field("ok", description="Service status")
    version: str = Field(..., description="API version (matches skynet.__version__)")
    components: dict[str, str] = Field(default_factory=dict, description="Component status")


class RegisterGatewayRequest(BaseModel):
    """Register or update an OpenClaw gateway."""

    gateway_id: str
    host: str
    capabilities: list[str] = Field(default_factory=list)
    status: str = "online"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterGatewayResponse(BaseModel):
    """Gateway registration response."""

    gateway_id: str
    host: str
    capabilities: list[str] = Field(default_factory=list)
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    registered_at: str
    last_heartbeat: str


class RegisterWorkerRequest(BaseModel):
    """Register or update worker metadata."""

    worker_id: str
    gateway_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    status: str = "online"
    capacity: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterWorkerResponse(BaseModel):
    """Worker registration response."""

    worker_id: str
    gateway_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    status: str
    capacity: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    registered_at: str
    last_heartbeat: str


class RouteTaskRequest(BaseModel):
    """Route one action to an OpenClaw gateway."""

    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    gateway_id: str | None = None
    task_id: str | None = None
    confirmed: bool = True


class RouteTaskResponse(BaseModel):
    """Route-task response."""

    task_id: str
    gateway_id: str
    gateway_host: str
    status: str
    result: dict[str, Any] = Field(default_factory=dict)


class SystemGatewayState(BaseModel):
    """Gateway item in system-state response."""

    gateway_id: str
    host: str
    capabilities: list[str] = Field(default_factory=list)
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    registered_at: str
    last_heartbeat: str


class SystemWorkerState(BaseModel):
    """Worker item in system-state response."""

    worker_id: str
    gateway_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    status: str
    capacity: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    registered_at: str
    last_heartbeat: str


class SystemStateResponse(BaseModel):
    """Global infrastructure state."""

    gateway_count: int
    worker_count: int
    gateways: list[SystemGatewayState] = Field(default_factory=list)
    workers: list[SystemWorkerState] = Field(default_factory=list)
    generated_at: str


class QueueTaskRequest(BaseModel):
    """Enqueue a control-plane task for scheduler execution."""

    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    priority: int = 0
    dependencies: list[str] = Field(default_factory=list)
    required_files: list[str] = Field(default_factory=list)
    gateway_id: str | None = None


class TaskState(BaseModel):
    """Task row as exposed by control-plane APIs."""

    id: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    status: str
    priority: int = 0
    dependencies: list[str] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)
    required_files: list[str] = Field(default_factory=list)
    locked_by: str | None = None
    locked_at: str | None = None
    claim_token: str | None = None
    gateway_id: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: str
    updated_at: str
    completed_at: str | None = None


class QueueTaskResponse(BaseModel):
    """Task enqueue response."""

    task: TaskState


class ClaimTaskRequest(BaseModel):
    """Explicit claim request for pull workers/tests.

    Lock timeout is controlled server-side by SKYNET_CONTROL_TASK_LOCK_TIMEOUT.
    """

    worker_id: str


class ClaimTaskResponse(BaseModel):
    """Response for explicit claim attempts."""

    claimed: bool
    task: TaskState | None = None


class NextTaskResponse(BaseModel):
    """Dry-run next-task eligibility without locking."""

    eligible: bool
    agent_id: str
    task: TaskState | None = None


class StartTaskRequest(BaseModel):
    """Transition claimed task to running."""

    worker_id: str
    claim_token: str


class CompleteTaskRequest(BaseModel):
    """Mark claimed task complete/failed."""

    worker_id: str
    claim_token: str
    success: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class TaskMutationResponse(BaseModel):
    """Generic task mutation result."""

    ok: bool


class ReleaseTaskRequest(BaseModel):
    """Release a claimed/running lock back to released/failed."""

    worker_id: str
    claim_token: str
    reason: str = ""
    back_to_pending: bool = True


class TaskListResponse(BaseModel):
    """List of control-plane queued tasks."""

    tasks: list[TaskState] = Field(default_factory=list)


class FileOwnershipRecord(BaseModel):
    """Active ownership of a file path by a running task."""

    file_path: str
    owning_task: str
    claim_token: str | None = None
    claimed_at: str


class FileOwnershipResponse(BaseModel):
    """List active file ownership claims."""

    ownership: list[FileOwnershipRecord] = Field(default_factory=list)


class ClaimFileRequest(BaseModel):
    """Manual file claim for a running task."""

    task_id: str
    claim_token: str
    file_path: str


class ClaimFileResponse(BaseModel):
    """Response from file claim endpoint."""

    ok: bool
    owner_task_id: str | None = None


class AgentState(BaseModel):
    """Read model for who is working on what."""

    agent_id: str
    status: str
    gateway_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    active_task_id: str | None = None
    active_task_status: str | None = None
    active_task_action: str | None = None
    active_task_locked_at: str | None = None


class AgentListResponse(BaseModel):
    """List of agents with current assignments."""

    agents: list[AgentState] = Field(default_factory=list)


class TaskEventRecord(BaseModel):
    """Execution/event log row for task lifecycle."""

    id: int
    task_id: str
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    worker_id: str | None = None
    claim_token: str | None = None
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class EventListResponse(BaseModel):
    """List of task events."""

    events: list[TaskEventRecord] = Field(default_factory=list)
