"""
SKYNET FastAPI Service - Main Application.

Control-plane API for distributed OpenClaw orchestration.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from skynet.api.routes import app_state, router
from skynet.control_plane import (
    ControlPlaneRegistry,
    ControlPlaneScheduler,
    GatewayClient,
    StaleLockReaper,
)
from skynet.ledger.schema import init_db
from skynet.ledger.task_queue import TaskQueueManager
from skynet.ledger.worker_registry import WorkerRegistry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger("skynet.api")


def _get_gateway_urls_from_env() -> list[str]:
    """
    Resolve OpenClaw gateway URLs from environment variables.

    Supported env vars:
    - OPENCLAW_GATEWAY_URLS (comma-separated URLs)
    - OPENCLAW_GATEWAY_URL (single URL fallback)
    """
    configured_urls = os.getenv("OPENCLAW_GATEWAY_URLS", "").strip()
    gateway_urls = [url.strip() for url in configured_urls.split(",") if url.strip()]
    if not gateway_urls:
        gateway_urls = [os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:8766")]
    return gateway_urls


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown of control-plane components.
    """
    logger.info("SKYNET API starting...")

    app_state.control_registry = ControlPlaneRegistry()
    app_state.gateway_client = GatewayClient()
    logger.info("Control-plane registry initialized")

    try:
        db_path = os.getenv("SKYNET_DB_PATH", "data/skynet.db")
        app_state.ledger_db = await init_db(db_path)
        app_state.worker_registry = WorkerRegistry(app_state.ledger_db)
        app_state.task_queue = TaskQueueManager(app_state.ledger_db)
    except Exception as e:
        logger.warning(f"Failed to initialize ledger worker registry: {e}")

    # Seed gateway registry from configured OpenClaw gateways.
    gateway_capabilities = [
        "execute_task",
        "get_gateway_status",
        "get_worker_status",
        "list_sessions",
    ]
    for idx, gateway_url in enumerate(_get_gateway_urls_from_env()):
        gateway_id = "openclaw" if idx == 0 else f"openclaw_{idx + 1}"
        status = "online"
        try:
            status_data = await app_state.gateway_client.get_gateway_status(gateway_url)
            if not status_data.get("agent_connected", False):
                status = "degraded"
        except Exception:
            status = "offline"

        app_state.control_registry.register_gateway(
            gateway_id=gateway_id,
            host=gateway_url,
            capabilities=gateway_capabilities,
            status=status,
            metadata={"source": "startup"},
        )

    # Start control-plane scheduler authority (disabled only if explicitly configured).
    scheduler_enabled = os.getenv("SKYNET_CONTROL_SCHEDULER_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if scheduler_enabled and app_state.task_queue is not None:
        task_lock_timeout = int(os.getenv("SKYNET_CONTROL_TASK_LOCK_TIMEOUT", "300"))
        app_state.control_scheduler = ControlPlaneScheduler(
            task_queue=app_state.task_queue,
            registry=app_state.control_registry,
            gateway_client=app_state.gateway_client,
            worker_id=os.getenv("SKYNET_CONTROL_SCHEDULER_WORKER_ID", "skynet-control-scheduler"),
            poll_interval_seconds=float(os.getenv("SKYNET_CONTROL_SCHEDULER_POLL_SECS", "1.5")),
            lock_timeout_seconds=task_lock_timeout,
        )
        await app_state.control_scheduler.start()

        reaper_enabled = os.getenv("SKYNET_STALE_LOCK_REAPER_ENABLED", "1").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if reaper_enabled:
            app_state.stale_lock_reaper = StaleLockReaper(
                task_queue=app_state.task_queue,
                registry=app_state.control_registry,
                gateway_client=app_state.gateway_client,
                ttl_seconds=task_lock_timeout,
                poll_interval_seconds=float(os.getenv("SKYNET_STALE_LOCK_REAPER_POLL_SECS", "15")),
            )
            await app_state.stale_lock_reaper.start()
        else:
            app_state.stale_lock_reaper = None
    else:
        app_state.control_scheduler = None
        app_state.stale_lock_reaper = None

    logger.info(
        """
  ____  _  ____   ___   _ _____ _____
 / ___|| |/ /\\ \\ / / \\ | | ____|_   _|
 \\___ \\| ' /  \\ V /|  \\| |  _|   | |
  ___) | . \\   | | | |\\  | |___  | |
 |____/|_|\\_\\  |_| |_| \\_|_____| |_|
      Control Plane API v2.2

  Status    : ONLINE
  Version   : 2.3.0 (Control-Plane Scheduler Authority)
  Endpoints : /v1/register-gateway, /v1/register-worker, /v1/route-task, /v1/tasks/*
  Docs      : http://localhost:8000/docs
"""
    )

    yield

    logger.info("SKYNET API shutting down...")

    app_state.control_registry = None
    app_state.gateway_client = None
    app_state.worker_registry = None
    if app_state.stale_lock_reaper is not None:
        try:
            await app_state.stale_lock_reaper.stop()
        except Exception as e:
            logger.error(f"Error stopping stale lock reaper: {e}")
    app_state.stale_lock_reaper = None
    if app_state.control_scheduler is not None:
        try:
            await app_state.control_scheduler.stop()
        except Exception as e:
            logger.error(f"Error stopping control scheduler: {e}")
    app_state.control_scheduler = None
    app_state.task_queue = None
    if app_state.ledger_db:
        try:
            await app_state.ledger_db.close()
        except Exception as e:
            logger.error(f"Error closing ledger DB: {e}")
    app_state.ledger_db = None
    logger.info("Shutdown complete")


app = FastAPI(
    title="SKYNET Control Plane API",
    description=(
        "SKYNET orchestration control plane. "
        "Routes task actions to OpenClaw gateways and tracks global topology."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "SKYNET Control Plane API",
        "version": "2.3.0",
        "status": "online",
        "docs": "/docs",
        "endpoints": {
            "register_gateway": "POST /v1/register-gateway",
            "register_worker": "POST /v1/register-worker",
            "route_task": "POST /v1/route-task",
            "task_queue": "POST /v1/tasks/enqueue",
            "task_list": "GET /v1/tasks",
            "task_next": "GET /v1/tasks/next?agent_id=...",
            "system_state": "GET /v1/system-state",
            "agents": "GET /v1/agents",
            "events": "GET /v1/events",
            "health": "GET /v1/health",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "skynet.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
