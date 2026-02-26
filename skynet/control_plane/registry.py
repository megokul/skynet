"""
SKYNET control-plane registry.

Tracks OpenClaw gateways and worker metadata for orchestration/routing.
This module does not execute workloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from skynet.utils import iso_now as _utc_now


@dataclass
class GatewayRecord:
    """
    GatewayRecord.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `GatewayRecord`.
    """

    gateway_id: str
    host: str
    capabilities: list[str] = field(default_factory=list)
    status: str = "online"
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_at: str = field(default_factory=_utc_now)
    last_heartbeat: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        """
        To dict.
        
        Purpose:
        - Implement `to_dict` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

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
    """
    WorkerRecord.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `WorkerRecord`.
    """

    worker_id: str
    gateway_id: str | None = None
    capabilities: list[str] = field(default_factory=list)
    status: str = "online"
    capacity: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_at: str = field(default_factory=_utc_now)
    last_heartbeat: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        """
        To dict.
        
        Purpose:
        - Implement `to_dict` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

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
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

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
        """
        Register gateway.
        
        Purpose:
        - Implement `register_gateway` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `gateway_id`: input used by this function to compute or route work.
        - `host`: input used by this function to compute or route work.
        - `capabilities`: input used by this function to compute or route work.
        - `status`: input used by this function to compute or route work.
        - `metadata`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

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
        """
        Heartbeat gateway.
        
        Purpose:
        - Implement `heartbeat_gateway` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `gateway_id`: input used by this function to compute or route work.
        - `status`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
        """

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
        """
        Register worker.
        
        Purpose:
        - Implement `register_worker` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `worker_id`: input used by this function to compute or route work.
        - `gateway_id`: input used by this function to compute or route work.
        - `capabilities`: input used by this function to compute or route work.
        - `status`: input used by this function to compute or route work.
        - `capacity`: input used by this function to compute or route work.
        - `metadata`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

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
        """
        List gateways.
        
        Purpose:
        - Implement `list_gateways` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
        """

        with self._lock:
            return [gateway.to_dict() for gateway in self._gateways.values()]

    def list_workers(self) -> list[dict[str, Any]]:
        """
        List workers.
        
        Purpose:
        - Implement `list_workers` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
        """

        with self._lock:
            return [worker.to_dict() for worker in self._workers.values()]

    def select_gateway(self, preferred_gateway_id: str | None = None) -> dict[str, Any] | None:
        """
        Select gateway.
        
        Purpose:
        - Implement `select_gateway` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `preferred_gateway_id`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
        """

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
        """
        Get system state.
        
        Purpose:
        - Implement `get_system_state` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

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
