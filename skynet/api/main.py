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
from skynet.control_plane import ControlPlaneRegistry, GatewayClient
from skynet.ledger.schema import init_db
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

    logger.info(
        """
  ____  _  ____   ___   _ _____ _____
 / ___|| |/ /\\ \\ / / \\ | | ____|_   _|
 \\___ \\| ' /  \\ V /|  \\| |  _|   | |
  ___) | . \\   | | | |\\  | |___  | |
 |____/|_|\\_\\  |_| |_| \\_|_____| |_|
      Control Plane API v2.2

  Status    : ONLINE
  Version   : 2.2.0 (Gateway-Orchestrated)
  Endpoints : /v1/register-gateway, /v1/register-worker, /v1/route-task, /v1/system-state
  Docs      : http://localhost:8000/docs
"""
    )

    yield

    logger.info("SKYNET API shutting down...")

    app_state.control_registry = None
    app_state.gateway_client = None
    app_state.worker_registry = None
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
        "version": "2.2.0",
        "status": "online",
        "docs": "/docs",
        "endpoints": {
            "register_gateway": "POST /v1/register-gateway",
            "register_worker": "POST /v1/register-worker",
            "route_task": "POST /v1/route-task",
            "system_state": "GET /v1/system-state",
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
