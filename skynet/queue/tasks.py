"""
SKYNET Queue - Tasks

Celery tasks for job execution and maintenance.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any

from celery import Task
from celery.exceptions import MaxRetriesExceededError

from skynet.ledger.schema import init_db
from skynet.ledger.worker_registry import WorkerRegistry
from skynet.queue.celery_app import app
from skynet.shared.utils import utcnow

logger = logging.getLogger("skynet.queue.tasks")

_WORKER_ID = os.environ.get("SKYNET_WORKER_ID", f"worker-{socket.gethostname()}")
_DB_PATH = os.environ.get("SKYNET_DB_PATH", "data/skynet.db")


def _build_providers() -> dict[str, Any]:
    """Build default provider registry used by queue tasks."""
    from skynet.chathan.providers.chathan_provider import ChathanProvider
    from skynet.chathan.providers.docker_provider import DockerProvider
    from skynet.chathan.providers.local_provider import LocalProvider
    from skynet.chathan.providers.mock_provider import MockProvider
    from skynet.chathan.providers.ssh_provider import SSHProvider

    allowed_paths = os.environ.get("SKYNET_ALLOWED_PATHS", os.getcwd()).split(os.pathsep)
    gateway_url = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:8766")
    docker_image = os.environ.get("SKYNET_DOCKER_IMAGE", "ubuntu:22.04")

    providers: dict[str, Any] = {
        "mock": MockProvider(),
        "local": LocalProvider(allowed_paths=allowed_paths),
        "chathan": ChathanProvider(gateway_api_url=gateway_url),
        "openclaw": ChathanProvider(gateway_api_url=gateway_url),
        "docker": DockerProvider(docker_image=docker_image),
    }

    try:
        providers["ssh"] = SSHProvider(
            host=os.environ.get("SKYNET_SSH_HOST", "localhost"),
            port=int(os.environ.get("SKYNET_SSH_PORT", "22")),
            username=os.environ.get("SKYNET_SSH_USERNAME", "ubuntu"),
            key_path=os.environ.get("SKYNET_SSH_KEY_PATH") or None,
        )
    except Exception as e:
        logger.warning("SSH provider init failed: %s", e)

    return providers


def _extract_actions(execution_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize execution spec into action list."""
    actions = execution_spec.get("actions")
    if isinstance(actions, list):
        return actions

    steps = execution_spec.get("steps")
    if isinstance(steps, list):
        normalized: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            normalized.append(
                {
                    "action": step.get("action", "unknown"),
                    "params": step.get("params", {}),
                }
            )
        return normalized

    return []


async def _update_job_status(job_id: str, status: str, error_message: str | None = None) -> None:
    """Persist job status transition in ledger DB."""
    db = await init_db(_DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            """
            UPDATE jobs
            SET status = ?, updated_at = ?, error_message = COALESCE(?, error_message)
            WHERE id = ?
            """,
            (status, now, error_message, job_id),
        )
        await db.commit()
    finally:
        await db.close()


@app.task(
    bind=True,
    name="skynet.queue.tasks.execute_job",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def execute_job(self: Task, job_id: str, execution_spec: dict) -> dict:
    """
    Execute a job via configured execution provider.

    Args:
        job_id: Unique job identifier
        execution_spec: ExecutionSpec as dictionary

    Returns:
        Execution result dictionary
    """
    logger.info("[%s] Starting job execution", job_id)

    try:
        providers = _build_providers()
        provider_name = execution_spec.get("provider", "mock")
        provider = providers.get(provider_name)
        actions = _extract_actions(execution_spec)

        if provider is None:
            raise ValueError(f"Provider '{provider_name}' not registered")

        asyncio.run(_update_job_status(job_id, "running"))

        results: list[dict[str, Any]] = []
        for action in actions:
            action_type = action.get("action", "unknown")
            params = action.get("params", {})
            step_result = provider.execute(action=action_type, params=params)
            results.append(
                {
                    "action": action_type,
                    "status": step_result.get("status", "unknown"),
                    "output": step_result.get("output", ""),
                }
            )

        all_success = all(item.get("status") == "success" for item in results)
        final_status = "success" if all_success else "partial_failure"
        asyncio.run(_update_job_status(job_id, "succeeded" if all_success else "failed"))

        logger.info("[%s] Job execution completed: %s", job_id, final_status)
        return {
            "job_id": job_id,
            "status": final_status,
            "provider": provider_name,
            "results": results,
            "message": f"Executed {len(actions)} actions",
        }

    except Exception as exc:
        logger.exception("[%s] Job execution failed: %s", job_id, exc)

        try:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)
        except MaxRetriesExceededError:
            asyncio.run(_update_job_status(job_id, "failed", str(exc)))
            logger.error("[%s] Job failed after max retries", job_id)
            return {
                "job_id": job_id,
                "status": "failed",
                "error": f"Job failed after {self.max_retries} retries: {exc}",
            }


@app.task(
    bind=True,
    name="skynet.queue.tasks.cleanup_job",
)
def cleanup_job(self, job_id: str) -> dict:
    """
    Cleanup resources after job completion.

    Args:
        job_id: Job ID to clean up

    Returns:
        Cleanup result
    """
    logger.info("[%s] Cleaning up job resources", job_id)

    cleanup_notes = [
        "released in-memory task references",
        "retained logs/artifacts per retention policy",
    ]

    return {
        "job_id": job_id,
        "status": "cleaned",
        "notes": cleanup_notes,
        "timestamp": utcnow(),
    }


@app.task(
    bind=True,
    name="skynet.queue.tasks.health_check",
)
def health_check(self) -> dict:
    """
    Periodic health check task.

    Returns:
        Health status dict
    """
    logger.debug("Running health check")

    try:
        providers = _build_providers()
        provider_health: dict[str, Any] = {}
        for name, provider in providers.items():
            try:
                provider_health[name] = provider.health_check()
            except Exception as e:
                provider_health[name] = {"status": "unhealthy", "error": str(e)}

        return {
            "status": "healthy",
            "timestamp": utcnow(),
            "providers": provider_health,
        }
    except Exception as exc:
        logger.error(f"Health check failed: {exc}")
        return {
            "status": "unhealthy",
            "timestamp": utcnow(),
            "error": str(exc),
        }


@app.task(
    name="skynet.queue.tasks.cleanup_stale_jobs",
)
def cleanup_stale_jobs() -> dict:
    """
    Periodically clean up stale/old jobs from the queue.

    Returns:
        Cleanup result
    """
    logger.info("Running stale job cleanup")

    async def _cleanup() -> dict[str, int]:
        db = await init_db(_DB_PATH)
        try:
            now = datetime.now(timezone.utc)
            cutoff = (now - timedelta(hours=2)).isoformat()
            cur = await db.execute(
                """
                UPDATE jobs
                SET status = 'timeout', updated_at = ?, error_message =
                    COALESCE(error_message, 'Marked stale by cleanup task')
                WHERE status = 'running' AND started_at IS NOT NULL AND started_at < ?
                """,
                (now.isoformat(), cutoff),
            )
            await db.commit()
            stale_marked = cur.rowcount

            cur2 = await db.execute(
                """
                DELETE FROM job_locks
                WHERE expires_at IS NOT NULL AND expires_at < ?
                """,
                (now.isoformat(),),
            )
            await db.commit()
            locks_cleaned = cur2.rowcount
            return {"stale_jobs_marked": stale_marked, "locks_cleaned": locks_cleaned}
        finally:
            await db.close()

    from datetime import timedelta

    result = asyncio.run(_cleanup())
    return {
        "status": "completed",
        "timestamp": utcnow(),
        **result,
    }


@app.task(
    name="skynet.queue.tasks.update_worker_status",
)
def update_worker_status() -> dict:
    """
    Periodically update worker status in ledger.

    Returns:
        Update result
    """
    logger.debug("Updating worker status")

    async def _update() -> dict[str, int]:
        db = await init_db(_DB_PATH)
        try:
            registry = WorkerRegistry(db)
            stale = await registry.cleanup_stale_workers()

            worker = await registry.get_worker(_WORKER_ID)
            if worker:
                await registry.heartbeat(_WORKER_ID)

            online = await registry.get_online_workers()
            return {"stale_workers_offlined": stale, "online_workers": len(online)}
        finally:
            await db.close()

    result = asyncio.run(_update())
    return {
        "status": "completed",
        "timestamp": utcnow(),
        **result,
    }