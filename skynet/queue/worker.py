"""
SKYNET — Celery Worker

Executes jobs from the queue using execution providers.

This worker:
1. Picks up jobs from Celery queue
2. Executes actions via execution providers
3. Updates job status in ledger
4. Returns results

Usage:
    celery -A skynet.queue.worker worker --loglevel=info
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any

from skynet.queue.celery_app import celery_app
from skynet.chathan.providers.mock_provider import MockProvider
from skynet.chathan.providers.local_provider import LocalProvider
from skynet.chathan.providers.chathan_provider import ChathanProvider
from skynet.chathan.providers.docker_provider import DockerProvider
from skynet.chathan.providers.ssh_provider import SSHProvider
from skynet.ledger.schema import init_db
from skynet.ledger.job_locking import JobLockManager
from skynet.ledger.worker_registry import WorkerRegistry
from skynet.events import EventEngine, Event, EventType

logger = logging.getLogger("skynet.queue.worker")


# Initialize providers (simple dict for now)
# LocalProvider allowed paths - can be configured via environment variable
allowed_paths = os.environ.get("SKYNET_ALLOWED_PATHS", os.getcwd()).split(os.pathsep)

# OpenClaw Gateway URL - can be configured via environment variable
gateway_url = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:8766")

# Docker image - can be configured via environment variable
docker_image = os.environ.get("SKYNET_DOCKER_IMAGE", "ubuntu:22.04")

# SSH configuration - can be configured via environment variables
ssh_host = os.environ.get("SKYNET_SSH_HOST", "localhost")
ssh_port = int(os.environ.get("SKYNET_SSH_PORT", "22"))
ssh_username = os.environ.get("SKYNET_SSH_USERNAME", "ubuntu")
ssh_key_path = os.environ.get("SKYNET_SSH_KEY_PATH")  # Optional

providers = {
    "mock": MockProvider(),
    "local": LocalProvider(allowed_paths=allowed_paths),
    "chathan": ChathanProvider(gateway_api_url=gateway_url),
    "openclaw": ChathanProvider(gateway_api_url=gateway_url),  # Alias for consistency
    "docker": DockerProvider(docker_image=docker_image),
    "ssh": SSHProvider(
        host=ssh_host,
        port=ssh_port,
        username=ssh_username,
        key_path=ssh_key_path,
    ),
}

# Ledger-backed reliability primitives (lazy init).
_LEDGER_DB = None
_LOCK_MANAGER: JobLockManager | None = None
_WORKER_REGISTRY: WorkerRegistry | None = None
_WORKER_ID = os.environ.get("SKYNET_WORKER_ID", f"worker-{socket.gethostname()}")
_DB_PATH = os.environ.get("SKYNET_DB_PATH", "data/skynet.db")

# Event system (optional, lazy init)
_EVENT_ENGINE: EventEngine | None = None
_EVENTS_ENABLED = os.environ.get("SKYNET_EVENTS_ENABLED", "true").lower() == "true"


def _ensure_reliability_components() -> tuple[JobLockManager, WorkerRegistry]:
    """Initialize DB-backed lock/registry components on first use."""
    global _LEDGER_DB, _LOCK_MANAGER, _WORKER_REGISTRY
    if _LOCK_MANAGER and _WORKER_REGISTRY:
        return _LOCK_MANAGER, _WORKER_REGISTRY

    import asyncio

    async def _init() -> tuple[JobLockManager, WorkerRegistry]:
        db = await init_db(_DB_PATH)
        registry = WorkerRegistry(db=db)
        await registry.register_worker(
            worker_id=_WORKER_ID,
            provider_name="local",
            capabilities=list(providers.keys()),
            metadata={"component": "celery_worker"},
        )
        lock_manager = JobLockManager(db=db)
        return db, lock_manager, registry

    _LEDGER_DB, _LOCK_MANAGER, _WORKER_REGISTRY = asyncio.run(_init())
    return _LOCK_MANAGER, _WORKER_REGISTRY


def shutdown_reliability_components() -> None:
    """Close DB connection used by reliability primitives (for tests/shutdown)."""
    global _LEDGER_DB, _LOCK_MANAGER, _WORKER_REGISTRY, _EVENT_ENGINE
    if _LEDGER_DB is None:
        return

    import asyncio

    async def _close() -> None:
        await _LEDGER_DB.close()
        # Also stop event engine if running
        if _EVENT_ENGINE and _EVENT_ENGINE.is_running:
            await _EVENT_ENGINE.stop()

    asyncio.run(_close())
    _LEDGER_DB = None
    _LOCK_MANAGER = None
    _WORKER_REGISTRY = None
    _EVENT_ENGINE = None


def _get_event_engine() -> EventEngine | None:
    """
    Get or initialize EventEngine (optional component).

    Returns None if events are disabled.
    """
    global _EVENT_ENGINE

    if not _EVENTS_ENABLED:
        return None

    if _EVENT_ENGINE is not None:
        return _EVENT_ENGINE

    # Initialize event engine
    import asyncio

    async def _init_events() -> EventEngine:
        engine = EventEngine(register_defaults=False)  # Don't register default handlers in worker
        await engine.start()
        return engine

    try:
        _EVENT_ENGINE = asyncio.run(_init_events())
        logger.info("EventEngine initialized in worker")
        return _EVENT_ENGINE
    except Exception as e:
        logger.warning(f"Failed to initialize EventEngine: {e}, events disabled")
        return None


def _extract_actions(execution_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Normalize execution spec into worker action list.

    Supports both:
    - legacy shape: {"actions": [{"action": "...", "params": {...}}]}
    - dispatcher shape: {"steps": [{"action": "...", "params": {...}}]}
    """
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


@celery_app.task(name="skynet.execute_job", bind=True)
def execute_job(self, job_id: str, execution_spec: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a job from the queue.

    Args:
        job_id: Job identifier
        execution_spec: Execution specification with actions

    Returns:
        Execution result dictionary
    """
    logger.info(f"Worker picked up job {job_id}")
    lock_manager, registry = _ensure_reliability_components()
    event_engine = _get_event_engine()

    import asyncio

    # Keep liveness fresh on task pickup.
    asyncio.run(registry.heartbeat(_WORKER_ID))

    # Publish TASK_STARTED event
    if event_engine:
        try:
            asyncio.run(
                event_engine.publish(
                    Event(
                        type=EventType.TASK_STARTED,
                        payload={
                            "job_id": job_id,
                            "worker_id": _WORKER_ID,
                            "provider": execution_spec.get("provider", "mock"),
                        },
                        source="worker",
                    )
                )
            )
        except Exception as e:
            logger.warning(f"Failed to publish TASK_STARTED event: {e}")

    # Prevent duplicate execution for the same job_id.
    lock_acquired = asyncio.run(lock_manager.acquire_lock(job_id, _WORKER_ID))
    if not lock_acquired:
        owner = asyncio.run(lock_manager.get_lock_owner(job_id))
        logger.warning(f"Job {job_id} already locked by {owner}")
        return {
            "job_id": job_id,
            "status": "skipped",
            "message": f"Job already locked by {owner}",
        }

    try:
        asyncio.run(registry.set_runtime_state(_WORKER_ID, status="busy", current_job_id=job_id))

        # Get actions from execution spec (supports legacy and dispatcher formats).
        actions = _extract_actions(execution_spec)
        provider = execution_spec.get("provider", "mock")

        logger.info(f"Executing {len(actions)} actions with provider '{provider}'")

        # Execute each action
        results = []
        for i, action in enumerate(actions, 1):
            action_type = action.get("action", "unknown")
            params = action.get("params", {})

            logger.info(f"  [{i}/{len(actions)}] Executing {action_type}...")

            # Get provider
            provider_instance = providers.get(provider)
            if not provider_instance:
                logger.error(f"Provider '{provider}' not found")
                result = {"status": "error", "output": f"Provider '{provider}' not found"}
            else:
                # Execute via provider
                result = provider_instance.execute(action=action_type, params=params)

            results.append({
                "action": action_type,
                "status": result.get("status", "unknown"),
                "output": result.get("output", ""),
            })

            logger.info(f"  [{i}/{len(actions)}] {action_type} → {result.get('status', 'unknown')}")

        # Return overall result
        all_success = all(r["status"] == "success" for r in results)

        logger.info(f"Job {job_id} {'succeeded' if all_success else 'completed with errors'}")

        result_dict = {
            "job_id": job_id,
            "status": "success" if all_success else "partial_failure",
            "results": results,
            "message": f"Executed {len(actions)} actions",
        }

        # Publish TASK_COMPLETED event
        if event_engine:
            try:
                asyncio.run(
                    event_engine.publish(
                        Event(
                            type=EventType.TASK_COMPLETED,
                            payload={
                                "job_id": job_id,
                                "worker_id": _WORKER_ID,
                                "result": result_dict,
                                "all_success": all_success,
                            },
                            source="worker",
                        )
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to publish TASK_COMPLETED event: {e}")

        return result_dict

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")

        error_dict = {
            "job_id": job_id,
            "status": "error",
            "error": str(e),
            "message": f"Job execution failed: {e}",
        }

        # Publish TASK_FAILED event
        if event_engine:
            try:
                asyncio.run(
                    event_engine.publish(
                        Event(
                            type=EventType.TASK_FAILED,
                            payload={
                                "job_id": job_id,
                                "worker_id": _WORKER_ID,
                                "error": str(e),
                                "execution_spec": execution_spec,
                            },
                            source="worker",
                        )
                    )
                )
            except Exception as event_error:
                logger.warning(f"Failed to publish TASK_FAILED event: {event_error}")

        return error_dict
    finally:
        asyncio.run(lock_manager.release_lock(job_id, _WORKER_ID))
        asyncio.run(registry.set_runtime_state(_WORKER_ID, status="online", current_job_id=None))
        asyncio.run(registry.heartbeat(_WORKER_ID))


@celery_app.task(name="skynet.health_check")
def health_check() -> dict[str, Any]:
    """Health check task for worker monitoring."""
    _, registry = _ensure_reliability_components()
    import asyncio
    asyncio.run(registry.heartbeat(_WORKER_ID))

    return {
        "status": "healthy",
        "worker": "skynet.queue.worker",
        "worker_id": _WORKER_ID,
        "providers": list(providers.keys()),
    }
