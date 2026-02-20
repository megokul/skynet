"""
SKYNET control-plane scheduler.

Authority for task assignment lives here (in `skynet/`):
- claims ready tasks from control-plane DB
- selects gateway
- dispatches execution to OpenClaw gateway
- marks task complete/failed/requeued
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from skynet.control_plane.gateway_client import GatewayClient
from skynet.control_plane.registry import ControlPlaneRegistry
from skynet.ledger.task_queue import TaskQueueManager

logger = logging.getLogger("skynet.control.scheduler")


class ControlPlaneScheduler:
    """Background scheduler loop for control-plane task queue."""

    def __init__(
        self,
        *,
        task_queue: TaskQueueManager,
        registry: ControlPlaneRegistry,
        gateway_client: GatewayClient,
        worker_id: str = "skynet-control-scheduler",
        poll_interval_seconds: float = 1.5,
        lock_timeout_seconds: int = 300,
    ) -> None:
        self.task_queue = task_queue
        self.registry = registry
        self.gateway_client = gateway_client
        self.worker_id = worker_id
        self.poll_interval_seconds = poll_interval_seconds
        self.lock_timeout_seconds = lock_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="skynet-control-scheduler")
        logger.info("Control-plane scheduler started (worker_id=%s).", self.worker_id)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Control-plane scheduler stopped.")

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                claimed = await self.task_queue.claim_next_ready_task(
                    worker_id=self.worker_id,
                    lock_timeout_seconds=self.lock_timeout_seconds,
                )
                if not claimed:
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                await self._execute_claimed_task(claimed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Control-plane scheduler loop error.")
                await asyncio.sleep(max(self.poll_interval_seconds, 1.0))

    async def _execute_claimed_task(self, task: dict[str, Any]) -> None:
        task_id = str(task["id"])
        claim_token = str(task.get("claim_token") or "")
        preferred_gateway_id = task.get("gateway_id")
        gateway = self.registry.select_gateway(preferred_gateway_id=preferred_gateway_id)

        if not gateway:
            await self.task_queue.release_claim(
                task_id=task_id,
                worker_id=self.worker_id,
                claim_token=claim_token,
                reason="No healthy gateway available; task re-queued by control plane.",
                back_to_pending=True,
            )
            return

        moved_to_running = await self.task_queue.mark_task_running(
            task_id=task_id,
            worker_id=self.worker_id,
            claim_token=claim_token,
        )
        if not moved_to_running:
            logger.warning(
                "Unable to transition claimed task to running (task_id=%s, worker_id=%s).",
                task_id,
                self.worker_id,
            )
            return

        gateway_host = str(gateway["host"])
        action = str(task["action"])
        params = dict(task.get("params") or {})

        try:
            result = await self.gateway_client.execute_task(
                host=gateway_host,
                action=action,
                params=params,
                confirmed=True,
                task_id=task_id,
                idempotency_key=claim_token,
            )
        except Exception as exc:
            await self.task_queue.release_claim(
                task_id=task_id,
                worker_id=self.worker_id,
                claim_token=claim_token,
                reason=f"Gateway execution failed ({gateway_host}): {exc}",
                back_to_pending=True,
            )
            self.registry.heartbeat_gateway(gateway["gateway_id"], status="degraded")
            return

        ok, error = self._evaluate_result(result)
        completed = await self.task_queue.complete_task(
            task_id=task_id,
            worker_id=self.worker_id,
            claim_token=claim_token,
            success=ok,
            result=result,
            error=error,
        )
        if not completed:
            logger.warning(
                "Failed to finalize task due to state transition mismatch (task_id=%s).",
                task_id,
            )
        self.registry.heartbeat_gateway(gateway["gateway_id"], status="online" if ok else "degraded")

    @staticmethod
    def _evaluate_result(result: dict[str, Any]) -> tuple[bool, str]:
        status = str(result.get("status", "")).lower()
        if status in {"ok", "success"}:
            inner = result.get("result") if isinstance(result.get("result"), dict) else {}
            rc = inner.get("returncode", 0) if isinstance(inner, dict) else 0
            if rc in (0, None):
                return True, ""
            return False, f"Command failed with exit code {rc}"

        err = result.get("error")
        if err:
            return False, str(err)
        return False, f"Gateway returned non-success status: {status or 'unknown'}"
