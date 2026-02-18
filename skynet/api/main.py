"""
SKYNET FastAPI Service - Main Application.

Control Plane API for SKYNET orchestration.
Provides planning, policy validation, and progress tracking.

Usage:
    # Development
    uvicorn skynet.api.main:app --reload --host 0.0.0.0 --port 8000

    # Production
    uvicorn skynet.api.main:app --host 0.0.0.0 --port 8000 --workers 4

Endpoints:
    POST /v1/plan - Generate execution plan
    POST /v1/report - Receive progress updates
    POST /v1/policy/check - Policy validation
    GET /v1/health - Health check
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from skynet.api.routes import router, app_state
from skynet.policy.engine import PolicyEngine
from skynet.events import EventEngine
from skynet.execution import ExecutionRouter
from skynet.scheduler import ProviderScheduler
from skynet.sentinel.provider_monitor import ProviderMonitor
from skynet.chathan.providers.local_provider import LocalProvider
from skynet.chathan.providers.mock_provider import MockProvider
from skynet.ledger.schema import init_db
from skynet.ledger.worker_registry import WorkerRegistry

if TYPE_CHECKING:
    from skynet.core.planner import Planner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger("skynet.api")


def _build_providers_from_env() -> dict[str, object]:
    """
    Build provider instances from environment configuration.

    Supported names:
    - local
    - mock
    - docker
    - ssh
    - chathan
    """
    configured = os.getenv("SKYNET_MONITORED_PROVIDERS", "local,mock")
    provider_names = [name.strip().lower() for name in configured.split(",") if name.strip()]

    allowed_paths_env = os.getenv("SKYNET_ALLOWED_PATHS", os.getcwd())
    allowed_paths = [p.strip() for p in allowed_paths_env.split(os.pathsep) if p.strip()]

    providers: dict[str, object] = {}

    for name in provider_names:
        try:
            if name == "local":
                providers["local"] = LocalProvider(allowed_paths=allowed_paths)
            elif name == "mock":
                providers["mock"] = MockProvider()
            elif name == "docker":
                from skynet.chathan.providers.docker_provider import DockerProvider

                providers["docker"] = DockerProvider(
                    docker_image=os.getenv("SKYNET_DOCKER_IMAGE", "ubuntu:22.04")
                )
            elif name == "ssh":
                from skynet.chathan.providers.ssh_provider import SSHProvider

                providers["ssh"] = SSHProvider(
                    host=os.getenv("SKYNET_SSH_HOST", "localhost"),
                    port=int(os.getenv("SKYNET_SSH_PORT", "22")),
                    username=os.getenv("SKYNET_SSH_USERNAME", "ubuntu"),
                    key_path=os.getenv("SKYNET_SSH_KEY_PATH") or None,
                )
            elif name in ("chathan", "openclaw"):
                from skynet.chathan.providers.chathan_provider import ChathanProvider

                providers["chathan"] = ChathanProvider(
                    gateway_api_url=os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:8766")
                )
            else:
                logger.warning(f"Unknown monitored provider in SKYNET_MONITORED_PROVIDERS: {name}")
        except Exception as e:
            logger.warning(f"Failed to initialize provider '{name}': {e}")

    # Always keep at least one provider available.
    if not providers:
        logger.warning("No providers initialized from env, falling back to local")
        providers["local"] = LocalProvider(allowed_paths=allowed_paths)

    return providers


# ============================================================================
# Lifespan Management
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown of components.
    """
    # ---- Startup ----
    logger.info("SKYNET API starting...")

    # Initialize Planner
    try:
        api_key = os.getenv("GOOGLE_AI_API_KEY")
        if not api_key:
            logger.warning("GOOGLE_AI_API_KEY not set - Planner will fail")
        else:
            from skynet.core.planner import Planner
            app_state.planner = Planner(api_key=api_key)
            logger.info("Planner initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Planner: {e}")

    # Initialize Policy Engine
    try:
        app_state.policy_engine = PolicyEngine(auto_approve_read_only=True)
        logger.info("PolicyEngine initialized")
    except Exception as e:
        logger.error(f"Failed to initialize PolicyEngine: {e}")

    # Initialize Memory System (SKYNET 2.0)
    try:
        from skynet.memory import MemoryManager
        from skynet.memory.vector_index import create_vector_indexer

        # Create vector indexer (use Gemini if available, otherwise mock)
        embedding_provider = os.getenv("EMBEDDING_PROVIDER", "gemini")
        try:
            app_state.vector_indexer = create_vector_indexer(
                provider=embedding_provider,
                api_key=os.getenv("GOOGLE_AI_API_KEY") if embedding_provider == "gemini" else None,
            )
            logger.info(f"Vector indexer initialized: {embedding_provider}")
        except Exception as e:
            logger.warning(f"Failed to initialize vector indexer: {e}, using mock")
            app_state.vector_indexer = create_vector_indexer(provider="mock")

        # Create memory manager
        app_state.memory_manager = MemoryManager(
            vector_indexer=app_state.vector_indexer
        )
        await app_state.memory_manager.initialize()
        logger.info("MemoryManager initialized")

        # Update Planner with memory manager
        if app_state.planner:
            app_state.planner.memory_manager = app_state.memory_manager
            logger.info("Planner enhanced with memory system")

    except Exception as e:
        logger.warning(f"Failed to initialize Memory system: {e}")
        logger.warning("Continuing without memory features")

    # Initialize Event Engine (SKYNET 2.0 - Phase 2)
    try:
        app_state.event_engine = EventEngine(
            planner=app_state.planner,
            orchestrator=None,  # Orchestrator not yet available in lifespan
            memory_manager=app_state.memory_manager,
        )
        await app_state.event_engine.start()
        logger.info("EventEngine initialized and started")

    except Exception as e:
        logger.warning(f"Failed to initialize Event Engine: {e}")
        logger.warning("Continuing without event features")

    # Initialize Provider Monitor + Scheduler + Execution Router
    try:
        db_path = os.getenv("SKYNET_DB_PATH", "data/skynet.db")
        app_state.ledger_db = await init_db(db_path)
        app_state.worker_registry = WorkerRegistry(app_state.ledger_db)
        providers = _build_providers_from_env()

        app_state.provider_monitor = ProviderMonitor(
            providers=providers,
            check_interval=int(os.getenv("SKYNET_PROVIDER_CHECK_INTERVAL", "60")),
        )
        await app_state.provider_monitor.check_all_providers()
        app_state.provider_monitor.start()

        app_state.scheduler = ProviderScheduler(
            provider_monitor=app_state.provider_monitor,
            worker_registry=app_state.worker_registry,
            memory_manager=app_state.memory_manager,
            available_providers=list(providers.keys()),
        )

        app_state.execution_router = ExecutionRouter(
            scheduler=app_state.scheduler,
            default_provider=os.getenv("SKYNET_EXECUTION_PROVIDER", next(iter(providers.keys()), "local")),
        )
        logger.info("ExecutionRouter initialized with shared scheduler + provider monitor")
    except Exception as e:
        logger.warning(f"Failed to initialize execution routing stack: {e}")
        logger.warning("Continuing without direct execution endpoint")

    logger.info(
        """
  ____  _  ____   ___   _ _____ _____
 / ___|| |/ /\\ \\ / / \\ | | ____|_   _|
 \\___ \\| ' /  \\ V /|  \\| |  _|   | |
  ___) | . \\   | | | |\\  | |___  | |
 |____/|_|\\_\\  |_| |_| \\_|_____| |_|
      Control Plane API v2.0

  Status    : ONLINE
  Version   : 2.0.0 (Cognitive OS with Memory & Events)
  Endpoints : /v1/plan, /v1/report, /v1/policy/check
  Memory    : /v1/memory/store, /v1/memory/search, /v1/memory/similar
  Events    : Reactive Intelligence Active
  Docs      : http://localhost:8000/docs
"""
    )

    yield

    # ---- Shutdown ----
    logger.info("SKYNET API shutting down...")

    # Stop event engine
    if app_state.event_engine:
        try:
            await app_state.event_engine.stop()
            logger.info("Event engine stopped")
        except Exception as e:
            logger.error(f"Error stopping event engine: {e}")

    # Stop provider monitor
    if app_state.provider_monitor:
        try:
            await app_state.provider_monitor.stop()
            logger.info("Provider monitor stopped")
        except Exception as e:
            logger.error(f"Error stopping provider monitor: {e}")

    # Close memory system
    if app_state.memory_manager:
        try:
            await app_state.memory_manager.close()
            logger.info("Memory system closed")
        except Exception as e:
            logger.error(f"Error closing memory system: {e}")

    app_state.planner = None
    app_state.policy_engine = None
    app_state.memory_manager = None
    app_state.vector_indexer = None
    app_state.event_engine = None
    app_state.provider_monitor = None
    app_state.scheduler = None
    app_state.execution_router = None
    app_state.worker_registry = None
    if app_state.ledger_db:
        try:
            await app_state.ledger_db.close()
        except Exception as e:
            logger.error(f"Error closing ledger DB: {e}")
    app_state.ledger_db = None
    logger.info("Shutdown complete")


# ============================================================================
# FastAPI Application
# ============================================================================


app = FastAPI(
    title="SKYNET Control Plane API",
    description=(
        "SKYNET orchestration control plane. "
        "Provides AI-powered planning, policy validation, and execution governance."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware (for browser-based clients)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)


# ============================================================================
# Root Endpoint
# ============================================================================


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "SKYNET Control Plane API",
        "version": "1.0.0",
        "status": "online",
        "docs": "/docs",
        "endpoints": {
            "plan": "POST /v1/plan",
            "report": "POST /v1/report",
            "policy_check": "POST /v1/policy/check",
            "health": "GET /v1/health",
        },
    }


# ============================================================================
# Run (for development)
# ============================================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "skynet.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
