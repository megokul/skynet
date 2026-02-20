"""
SKYNET stale-lock reaper.

Background task that scans claimed/running tasks and handles stale locks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from skynet.control_plane.gateway_client import GatewayClient
from skynet.control_plane.registry import ControlPlaneRegistry
from skynet.ledger.task_queue import TaskQueueManager

logger = logging.getLogger("skynet.control.reaper")


class StaleLockReaper:
    """Background stale-lock monitor for control-plane task queue."""

    def __init__(
        self,
        *,
        task_queue: TaskQueueManager,
        registry: ControlPlaneRegistry,
        gateway_client: GatewayClient,
        ttl_seconds: int = 300,
        poll_interval_seconds: float = 15.0,
    ) -> None:
        self.task_queue = task_queue
        self.registry = registry
        self.gateway_client = gateway_client
        self.ttl_seconds = int(ttl_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="skynet-stale-lock-reaper")
        logger.info(
            "Stale-lock reaper started (ttl=%ss poll=%ss).",
            self.ttl_seconds,
            self.poll_interval_seconds,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Stale-lock reaper stopped.")

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.reap_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stale-lock reaper loop error.")
            await asyncio.sleep(self.poll_interval_seconds)

    async def reap_once(self) -> None:
        stale = await self.task_queue.list_stale_locked_tasks(ttl_seconds=self.ttl_seconds)
        for task in stale:
            await self._handle_stale_task(task)

    async def _handle_stale_task(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("id") or "")
        worker_id = str(task.get("locked_by") or "")
        claim_token = str(task.get("claim_token") or "")
        gateway_id = str(task.get("gateway_id") or "")
        locked_at = str(task.get("locked_at") or "")

        if not task_id or not worker_id or not claim_token:
            return

        worker_healthy = self._is_worker_healthy(worker_id)
        gateway_healthy = await self._is_gateway_healthy(gateway_id)

        reason = (
            f"Stale lock detected by reaper (locked_at={locked_at}, "
            f"worker_healthy={worker_healthy}, gateway_healthy={gateway_healthy})."
        )
        if worker_healthy and gateway_healthy:
            released = await self.task_queue.release_claim(
                task_id=task_id,
                worker_id=worker_id,
                claim_token=claim_token,
                reason=reason,
                back_to_pending=True,
            )
            if released:
                logger.warning("Released stale task back to queue (task_id=%s).", task_id)
            return

        timed_out = await self.task_queue.mark_failed_timeout(
            task_id=task_id,
            worker_id=worker_id,
            claim_token=claim_token,
            reason=f"failed_timeout: {reason}",
        )
        if timed_out:
            logger.warning("Marked stale task failed_timeout (task_id=%s).", task_id)

    def _is_worker_healthy(self, worker_id: str) -> bool:
        worker_id_lower = worker_id.lower()
        if worker_id_lower.startswith("skynet-control-scheduler"):
            return True

        for worker in self.registry.list_workers():
            if str(worker.get("worker_id")) != worker_id:
                continue
            status = str(worker.get("status") or "").lower()
            return status in {"online", "healthy", "running", "busy"}
        return False

    async def _is_gateway_healthy(self, gateway_id: str) -> bool:
        gateway = None
        if gateway_id:
            for item in self.registry.list_gateways():
                if str(item.get("gateway_id")) == gateway_id:
                    gateway = item
                    break
        else:
            gateway = self.registry.select_gateway()

        if not gateway:
            return False

        status = str(gateway.get("status") or "").lower()
        if status not in {"online", "healthy"}:
            return False

        host = str(gateway.get("host") or "").strip()
        if not host:
            return False

        try:
            data = await self.gateway_client.get_gateway_status(host)
        except Exception:
            return False
        return bool(data.get("agent_connected"))
