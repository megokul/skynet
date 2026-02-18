"""SKYNET API schemas for control-plane endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Service health response."""

    status: str = Field("ok", description="Service status")
    version: str = Field("1.0.0", description="API version")
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
