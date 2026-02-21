"""
SKYNET API Routes - control-plane endpoint handlers.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from skynet import __version__
from skynet.api import schemas
from skynet.control_plane import ControlPlaneRegistry, GatewayClient
from skynet.ledger.task_queue import TaskQueueManager

logger = logging.getLogger("skynet.api")

router = APIRouter(prefix="/v1", tags=["skynet"])


@dataclass
class AppState:
    """Application state container."""

    control_registry: ControlPlaneRegistry | None = None
    gateway_client: GatewayClient | None = None
    ledger_db: Any | None = None
    worker_registry: Any | None = None
    task_queue: TaskQueueManager | None = None
    control_scheduler: Any | None = None
    stale_lock_reaper: Any | None = None


app_state = AppState()
_rate_limit_buckets: dict[str, tuple[float, int]] = {}


def get_control_registry() -> ControlPlaneRegistry:
    """Dependency: Get shared control-plane registry."""
    if app_state.control_registry is None:
        raise HTTPException(status_code=503, detail="Control-plane registry not initialized")
    return app_state.control_registry


def get_gateway_client() -> GatewayClient:
    """Dependency: Get shared OpenClaw gateway client."""
    if app_state.gateway_client is None:
        raise HTTPException(status_code=503, detail="Gateway client not initialized")
    return app_state.gateway_client


def get_task_queue() -> TaskQueueManager:
    """Dependency: Get control-plane task queue manager."""
    if app_state.task_queue is None:
        raise HTTPException(status_code=503, detail="Task queue not initialized")
    return app_state.task_queue


def _is_auth_required() -> bool:
    return os.getenv("SKYNET_PROTECT_DIAGNOSTICS", "true").lower() == "true"


def _resolve_api_key() -> str:
    return os.getenv("SKYNET_API_KEY", "").strip()


def _extract_token(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()

    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    return None


def _enforce_rate_limit(request: Request) -> None:
    limit = int(os.getenv("SKYNET_DIAGNOSTIC_RATE_LIMIT_PER_MIN", "120"))
    if limit <= 0:
        return

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    # Evict stale buckets to prevent unbounded memory growth.
    stale = [ip for ip, (ws, _) in _rate_limit_buckets.items() if now - ws >= 60]
    for ip in stale:
        del _rate_limit_buckets[ip]

    window_start, count = _rate_limit_buckets.get(client_ip, (now, 0))

    if now - window_start >= 60:
        window_start, count = now, 0

    count += 1
    _rate_limit_buckets[client_ip] = (window_start, count)

    if count > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def require_protected_route_access(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    """
    Guard for control/diagnostic endpoints.

    - Applies lightweight in-memory rate limiting.
    - Enforces API key auth when SKYNET_API_KEY is configured.
    """
    _enforce_rate_limit(request)

    if not _is_auth_required():
        return False

    configured_key = _resolve_api_key()
    if not configured_key:
        return False

    token = _extract_token(authorization, x_api_key)
    if token != configured_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return True


@router.post("/register-gateway", response_model=schemas.RegisterGatewayResponse)
async def register_gateway(
    request: schemas.RegisterGatewayRequest,
    registry: ControlPlaneRegistry = Depends(get_control_registry),
    gateway_client: GatewayClient = Depends(get_gateway_client),
    _authorized=Depends(require_protected_route_access),
) -> schemas.RegisterGatewayResponse:
    """Register an OpenClaw gateway in SKYNET's control-plane registry."""
    resolved_status = request.status
    try:
        status_data = await gateway_client.get_gateway_status(request.host)
        if not status_data.get("agent_connected", False):
            resolved_status = "degraded"
    except Exception as exc:
        logger.warning(f"Gateway status probe failed for {request.gateway_id}: {exc}")
        resolved_status = "offline"

    record = registry.register_gateway(
        gateway_id=request.gateway_id,
        host=request.host,
        capabilities=request.capabilities,
        status=resolved_status,
        metadata=request.metadata,
    )
    return schemas.RegisterGatewayResponse(**record)


@router.post("/register-worker", response_model=schemas.RegisterWorkerResponse)
async def register_worker(
    request: schemas.RegisterWorkerRequest,
    registry: ControlPlaneRegistry = Depends(get_control_registry),
    _authorized=Depends(require_protected_route_access),
) -> schemas.RegisterWorkerResponse:
    """
    Register worker metadata in SKYNET.

    This stores infrastructure-level worker metadata only.
    """
    record = registry.register_worker(
        worker_id=request.worker_id,
        gateway_id=request.gateway_id,
        capabilities=request.capabilities,
        status=request.status,
        capacity=request.capacity,
        metadata=request.metadata,
    )

    if app_state.worker_registry is not None:
        metadata = dict(request.metadata)
        metadata["gateway_id"] = request.gateway_id
        metadata["capacity"] = request.capacity
        try:
            await app_state.worker_registry.register_worker(
                worker_id=request.worker_id,
                provider_name="openclaw",
                capabilities=request.capabilities,
                metadata=metadata,
            )
        except Exception as exc:
            logger.warning(f"Failed to mirror worker registration to ledger: {exc}")

    return schemas.RegisterWorkerResponse(**record)


@router.post("/route-task", response_model=schemas.RouteTaskResponse)
async def route_task(
    request: schemas.RouteTaskRequest,
    registry: ControlPlaneRegistry = Depends(get_control_registry),
    gateway_client: GatewayClient = Depends(get_gateway_client),
    _authorized=Depends(require_protected_route_access),
) -> schemas.RouteTaskResponse:
    """
    Route a task action to a selected OpenClaw gateway.

    SKYNET does not execute the action directly.
    """
    gateway = registry.select_gateway(preferred_gateway_id=request.gateway_id)
    if gateway is None:
        raise HTTPException(status_code=503, detail="No healthy gateway available")

    gateway_id = gateway["gateway_id"]
    gateway_host = gateway["host"]

    try:
        status_data = await gateway_client.get_gateway_status(gateway_host)
    except Exception as exc:
        registry.heartbeat_gateway(gateway_id, status="offline")
        raise HTTPException(
            status_code=503,
            detail=f"Gateway {gateway_id} unreachable: {exc}",
        ) from exc

    if not status_data.get("agent_connected", False):
        registry.heartbeat_gateway(gateway_id, status="degraded")
        raise HTTPException(
            status_code=503,
            detail=f"Gateway {gateway_id} is online but has no connected agent",
        )

    try:
        result = await gateway_client.execute_task(
            host=gateway_host,
            action=request.action,
            params=request.params,
            confirmed=request.confirmed,
        )
        registry.heartbeat_gateway(gateway_id, status="online")
    except Exception as exc:
        registry.heartbeat_gateway(gateway_id, status="degraded")
        raise HTTPException(
            status_code=502,
            detail=f"Gateway {gateway_id} execution failed: {exc}",
        ) from exc

    task_id = request.task_id or f"task-{uuid4().hex[:12]}"
    route_status = result.get("status", "unknown")
    return schemas.RouteTaskResponse(
        task_id=task_id,
        gateway_id=gateway_id,
        gateway_host=gateway_host,
        status=route_status,
        result=result,
    )


@router.get("/system-state", response_model=schemas.SystemStateResponse)
async def get_system_state(
    registry: ControlPlaneRegistry = Depends(get_control_registry),
    _authorized=Depends(require_protected_route_access),
) -> schemas.SystemStateResponse:
    """Return current topology state (gateways + workers)."""
    state = registry.get_system_state()
    return schemas.SystemStateResponse(**state)


@router.post("/tasks/enqueue", response_model=schemas.QueueTaskResponse)
async def enqueue_task(
    request: schemas.QueueTaskRequest,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.QueueTaskResponse:
    """Enqueue a control-plane task for scheduler dispatch."""
    try:
        task = await task_queue.enqueue_task(
            action=request.action,
            params=request.params,
            task_id=request.task_id,
            priority=request.priority,
            dependencies=request.dependencies,
            required_files=request.required_files,
            gateway_id=request.gateway_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.QueueTaskResponse(task=schemas.TaskState(**task))


@router.get("/tasks", response_model=schemas.TaskListResponse)
async def list_tasks(
    status: str | None = None,
    limit: int = 200,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.TaskListResponse:
    """List queued tasks from control-plane authoritative scheduler DB."""
    tasks = await task_queue.list_tasks(status=status, limit=limit)
    return schemas.TaskListResponse(tasks=[schemas.TaskState(**t) for t in tasks])


@router.get("/tasks/next", response_model=schemas.NextTaskResponse)
async def get_next_task_preview(
    agent_id: str,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.NextTaskResponse:
    """Dry-run next-task eligibility for an agent without acquiring a lock."""
    task = await task_queue.peek_next_ready_task(worker_id=agent_id)
    if not task:
        return schemas.NextTaskResponse(eligible=False, agent_id=agent_id, task=None)
    return schemas.NextTaskResponse(eligible=True, agent_id=agent_id, task=schemas.TaskState(**task))


@router.get("/tasks/{task_id}", response_model=schemas.TaskState)
async def get_task(
    task_id: str,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.TaskState:
    """Get one queued task."""
    task = await task_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return schemas.TaskState(**task)


@router.post("/tasks/claim", response_model=schemas.ClaimTaskResponse)
async def claim_task(
    request: schemas.ClaimTaskRequest,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.ClaimTaskResponse:
    """
    Explicit claim endpoint.

    Primary scheduler authority remains the internal control-plane scheduler,
    but this endpoint enables pull-based workers/tests.
    """
    task = await task_queue.claim_next_ready_task(
        worker_id=request.worker_id,
    )
    if not task:
        return schemas.ClaimTaskResponse(claimed=False, task=None)
    return schemas.ClaimTaskResponse(claimed=True, task=schemas.TaskState(**task))


@router.post("/tasks/{task_id}/start", response_model=schemas.TaskMutationResponse)
async def start_task(
    task_id: str,
    request: schemas.StartTaskRequest,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.TaskMutationResponse:
    """Move a claimed task into the running state."""
    ok = await task_queue.mark_task_running(
        task_id=task_id,
        worker_id=request.worker_id,
        claim_token=request.claim_token,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Illegal transition or task claim mismatch")
    return schemas.TaskMutationResponse(ok=True)


@router.post("/tasks/{task_id}/complete", response_model=schemas.TaskMutationResponse)
async def complete_task(
    task_id: str,
    request: schemas.CompleteTaskRequest,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.TaskMutationResponse:
    """Complete (or fail) a claimed task."""
    ok = await task_queue.complete_task(
        task_id=task_id,
        worker_id=request.worker_id,
        claim_token=request.claim_token,
        success=request.success,
        result=request.result,
        error=request.error,
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Task transition rejected (expected running state with matching claim)",
        )
    return schemas.TaskMutationResponse(ok=True)


@router.post("/tasks/{task_id}/release", response_model=schemas.TaskMutationResponse)
async def release_task(
    task_id: str,
    request: schemas.ReleaseTaskRequest,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.TaskMutationResponse:
    """Release a task lock and optionally re-queue it."""
    ok = await task_queue.release_claim(
        task_id=task_id,
        worker_id=request.worker_id,
        claim_token=request.claim_token,
        reason=request.reason,
        back_to_pending=request.back_to_pending,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Task transition rejected or claim mismatch")
    return schemas.TaskMutationResponse(ok=True)


@router.get("/files/ownership", response_model=schemas.FileOwnershipResponse)
async def list_file_ownership(
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.FileOwnershipResponse:
    """List active file ownership claims to debug write conflicts."""
    records = await task_queue.list_file_ownership()
    return schemas.FileOwnershipResponse(
        ownership=[schemas.FileOwnershipRecord(**r) for r in records]
    )


@router.post("/files/claim", response_model=schemas.ClaimFileResponse)
async def claim_file(
    request: schemas.ClaimFileRequest,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.ClaimFileResponse:
    """Claim a file path for a running task."""
    ok, owner = await task_queue.claim_file(
        task_id=request.task_id,
        claim_token=request.claim_token,
        file_path=request.file_path,
    )
    return schemas.ClaimFileResponse(ok=ok, owner_task_id=owner)


@router.get("/agents", response_model=schemas.AgentListResponse)
async def list_agents(
    registry: ControlPlaneRegistry = Depends(get_control_registry),
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.AgentListResponse:
    """Read model: list agents and active task assignment (if any)."""
    assignments = await task_queue.list_active_assignments()
    assignment_map = {str(a["agent_id"]): a for a in assignments if a.get("agent_id")}

    agents: list[schemas.AgentState] = []
    for worker in registry.list_workers():
        agent_id = str(worker.get("worker_id"))
        active = assignment_map.pop(agent_id, None)
        agents.append(
            schemas.AgentState(
                agent_id=agent_id,
                status=str(worker.get("status", "unknown")),
                gateway_id=worker.get("gateway_id"),
                metadata=dict(worker.get("metadata") or {}),
                active_task_id=active.get("task_id") if active else None,
                active_task_status=active.get("status") if active else None,
                active_task_action=active.get("action") if active else None,
                active_task_locked_at=active.get("locked_at") if active else None,
            )
        )

    # Include internal scheduler workers or ad-hoc agents not present in registry.
    for agent_id, active in assignment_map.items():
        agents.append(
            schemas.AgentState(
                agent_id=agent_id,
                status="unknown",
                gateway_id=active.get("gateway_id"),
                metadata={},
                active_task_id=active.get("task_id"),
                active_task_status=active.get("status"),
                active_task_action=active.get("action"),
                active_task_locked_at=active.get("locked_at"),
            )
        )

    agents.sort(key=lambda x: (x.active_task_locked_at or "", x.agent_id), reverse=True)
    return schemas.AgentListResponse(agents=agents)


@router.get("/events", response_model=schemas.EventListResponse)
async def list_events(
    task_id: str | None = None,
    since: str | None = None,
    limit: int = 200,
    task_queue: TaskQueueManager = Depends(get_task_queue),
    _authorized=Depends(require_protected_route_access),
) -> schemas.EventListResponse:
    """Execution log stream for task lifecycle transitions."""
    events = await task_queue.list_task_events(task_id=task_id, since=since, limit=limit)
    return schemas.EventListResponse(events=[schemas.TaskEventRecord(**e) for e in events])


@router.get("/health", response_model=schemas.HealthResponse)
async def health_check() -> schemas.HealthResponse:
    """Service health check."""
    components = {
        "control_registry": "ok" if app_state.control_registry else "not_initialized",
        "gateway_client": "ok" if app_state.gateway_client else "not_initialized",
        "task_queue": "ok" if app_state.task_queue else "not_initialized",
        "control_scheduler": (
            "ok"
            if app_state.control_scheduler and getattr(app_state.control_scheduler, "running", False)
            else "not_running"
        ),
        "stale_lock_reaper": (
            "ok"
            if app_state.stale_lock_reaper and getattr(app_state.stale_lock_reaper, "running", False)
            else "not_running"
        ),
    }

    status = "ok" if all(v == "ok" for v in components.values()) else "degraded"
    return schemas.HealthResponse(
        status=status,
        version=__version__,
        components=components,
    )
