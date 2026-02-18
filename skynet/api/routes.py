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

from skynet.api import schemas
from skynet.control_plane import ControlPlaneRegistry, GatewayClient

logger = logging.getLogger("skynet.api")

router = APIRouter(prefix="/v1", tags=["skynet"])


@dataclass
class AppState:
    """Application state container."""

    control_registry: ControlPlaneRegistry | None = None
    gateway_client: GatewayClient | None = None
    ledger_db: Any | None = None
    worker_registry: Any | None = None


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


@router.get("/health", response_model=schemas.HealthResponse)
async def health_check() -> schemas.HealthResponse:
    """Service health check."""
    components = {
        "control_registry": "ok" if app_state.control_registry else "not_initialized",
        "gateway_client": "ok" if app_state.gateway_client else "not_initialized",
    }

    status = "ok" if all(v == "ok" for v in components.values()) else "degraded"
    return schemas.HealthResponse(
        status=status,
        version="1.0.0",
        components=components,
    )
