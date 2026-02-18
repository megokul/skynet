"""
SKYNET control-plane registry.

Tracks OpenClaw gateways and worker metadata for orchestration/routing.
This module does not execute workloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GatewayRecord:
    gateway_id: str
    host: str
    capabilities: list[str] = field(default_factory=list)
    status: str = "online"
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_at: str = field(default_factory=_utc_now)
    last_heartbeat: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gateway_id": self.gateway_id,
            "host": self.host,
            "capabilities": self.capabilities,
            "status": self.status,
            "metadata": self.metadata,
            "registered_at": self.registered_at,
            "last_heartbeat": self.last_heartbeat,
        }


@dataclass
class WorkerRecord:
    worker_id: str
    gateway_id: str | None = None
    capabilities: list[str] = field(default_factory=list)
    status: str = "online"
    capacity: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_at: str = field(default_factory=_utc_now)
    last_heartbeat: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "gateway_id": self.gateway_id,
            "capabilities": self.capabilities,
            "status": self.status,
            "capacity": self.capacity,
            "metadata": self.metadata,
            "registered_at": self.registered_at,
            "last_heartbeat": self.last_heartbeat,
        }


class ControlPlaneRegistry:
    """In-memory registry for gateway/worker orchestration metadata."""

    def __init__(self) -> None:
        self._gateways: dict[str, GatewayRecord] = {}
        self._workers: dict[str, WorkerRecord] = {}
        self._lock = RLock()

    def register_gateway(
        self,
        gateway_id: str,
        host: str,
        capabilities: list[str] | None = None,
        status: str = "online",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock:
            existing = self._gateways.get(gateway_id)
            if existing:
                existing.host = host
                existing.capabilities = list(capabilities or existing.capabilities)
                existing.status = status
                existing.metadata = dict(metadata or existing.metadata)
                existing.last_heartbeat = now
                return existing.to_dict()

            record = GatewayRecord(
                gateway_id=gateway_id,
                host=host,
                capabilities=list(capabilities or []),
                status=status,
                metadata=dict(metadata or {}),
                registered_at=now,
                last_heartbeat=now,
            )
            self._gateways[gateway_id] = record
            return record.to_dict()

    def heartbeat_gateway(self, gateway_id: str, status: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            record = self._gateways.get(gateway_id)
            if record is None:
                return None
            record.last_heartbeat = _utc_now()
            if status:
                record.status = status
            return record.to_dict()

    def register_worker(
        self,
        worker_id: str,
        gateway_id: str | None = None,
        capabilities: list[str] | None = None,
        status: str = "online",
        capacity: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock:
            existing = self._workers.get(worker_id)
            if existing:
                existing.gateway_id = gateway_id
                existing.capabilities = list(capabilities or existing.capabilities)
                existing.status = status
                existing.capacity = dict(capacity or existing.capacity)
                existing.metadata = dict(metadata or existing.metadata)
                existing.last_heartbeat = now
                return existing.to_dict()

            record = WorkerRecord(
                worker_id=worker_id,
                gateway_id=gateway_id,
                capabilities=list(capabilities or []),
                status=status,
                capacity=dict(capacity or {}),
                metadata=dict(metadata or {}),
                registered_at=now,
                last_heartbeat=now,
            )
            self._workers[worker_id] = record
            return record.to_dict()

    def list_gateways(self) -> list[dict[str, Any]]:
        with self._lock:
            return [gateway.to_dict() for gateway in self._gateways.values()]

    def list_workers(self) -> list[dict[str, Any]]:
        with self._lock:
            return [worker.to_dict() for worker in self._workers.values()]

    def select_gateway(self, preferred_gateway_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if preferred_gateway_id:
                preferred = self._gateways.get(preferred_gateway_id)
                if preferred and preferred.status in {"online", "healthy"}:
                    return preferred.to_dict()

            candidates = [
                gateway
                for gateway in self._gateways.values()
                if gateway.status in {"online", "healthy"}
            ]
            if not candidates:
                return None

            candidates.sort(key=lambda gateway: gateway.last_heartbeat, reverse=True)
            return candidates[0].to_dict()

    def get_system_state(self) -> dict[str, Any]:
        with self._lock:
            gateways = [gateway.to_dict() for gateway in self._gateways.values()]
            workers = [worker.to_dict() for worker in self._workers.values()]
            return {
                "gateway_count": len(gateways),
                "worker_count": len(workers),
                "gateways": gateways,
                "workers": workers,
                "generated_at": _utc_now(),
            }
