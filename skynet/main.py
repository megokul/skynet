"""
SKYNET — Main Entry Point

Wires all components together and provides startup sequence.
This is the foundation that will later integrate with Telegram, Celery, etc.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from dotenv import load_dotenv

from skynet.core.planner import Planner
from skynet.core.dispatcher import Dispatcher
from skynet.core.orchestrator import Orchestrator
from skynet.ledger.schema import init_db
from skynet.policy.engine import PolicyEngine

try:
    from skynet.scheduler import ProviderScheduler
except ImportError:
    ProviderScheduler = None  # type: ignore


# Set up logging
def setup_logging(level: str = "INFO") -> None:
    """Configure logging for SKYNET."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


logger = logging.getLogger("skynet.main")


# Component initialization functions
def init_policy_engine(auto_approve_read_only: bool = True) -> PolicyEngine:
    """Initialize the Policy Engine."""
    logger.info("Initializing Policy Engine...")
    policy_engine = PolicyEngine(auto_approve_read_only=auto_approve_read_only)
    logger.info(f"Policy Engine initialized (auto_approve_read_only={auto_approve_read_only})")
    return policy_engine


def init_planner(api_key: str, model: str = "gemini-2.5-flash") -> Planner:
    """Initialize the Planner with Gemini AI."""
    logger.info(f"Initializing Planner with model {model}...")
    planner = Planner(api_key=api_key, model=model)
    logger.info("Planner initialized")
    return planner


def init_dispatcher(policy_engine: PolicyEngine) -> Dispatcher:
    """Initialize the Dispatcher."""
    logger.info("Initializing Dispatcher...")

    # Mock queue function for now (will be replaced with real Celery in Phase 6)
    def mock_enqueue(job_id: str, exec_spec: dict[str, Any]) -> None:
        logger.info(f"[MOCK QUEUE] Enqueued job {job_id} with {len(exec_spec.get('actions', []))} actions")

    execution_provider = os.getenv("SKYNET_EXECUTION_PROVIDER", "local")
    scheduler = ProviderScheduler() if ProviderScheduler else None
    dispatcher = Dispatcher(
        policy_engine=policy_engine,
        enqueue_fn=mock_enqueue,
        default_provider=execution_provider,
        scheduler=scheduler,
    )
    logger.info(
        f"Dispatcher initialized (provider={execution_provider}, "
        f"scheduler={'enabled' if scheduler else 'disabled'})"
    )
    return dispatcher


def init_orchestrator(
    planner: Planner,
    dispatcher: Dispatcher,
    policy_engine: PolicyEngine,
    ledger_db=None,
) -> Orchestrator:
    """Initialize the Orchestrator."""
    logger.info("Initializing Orchestrator...")
    orchestrator = Orchestrator(
        planner=planner,
        dispatcher=dispatcher,
        policy_engine=policy_engine,
        ledger_db=ledger_db,
    )
    logger.info("Orchestrator initialized")
    return orchestrator


# Main application
class SkynetApp:
    """
    Main SKYNET application.

    Wires all components together and provides the core orchestration system.

    Example:
        app = await SkynetApp.create()
        job_id = await app.create_task("Check git status")
        plan = await app.generate_plan(job_id)
        await app.approve_plan(job_id)
    """

    def __init__(
        self,
        planner: Planner,
        dispatcher: Dispatcher,
        orchestrator: Orchestrator,
        policy_engine: PolicyEngine,
        ledger_db=None,
    ):
        """Initialize SKYNET app with all components."""
        self.planner = planner
        self.dispatcher = dispatcher
        self.orchestrator = orchestrator
        self.policy_engine = policy_engine
        self.ledger_db = ledger_db

        logger.info("SKYNET application initialized")

    @classmethod
    async def create(
        cls,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        auto_approve_read_only: bool = True,
    ) -> "SkynetApp":
        """
        Factory method to create and initialize SKYNET application.

        Args:
            api_key: Google AI API key (or uses GOOGLE_AI_API_KEY env var)
            model: Gemini model to use
            auto_approve_read_only: Auto-approve READ_ONLY tasks

        Returns:
            Initialized SkynetApp instance
        """
        logger.info("=" * 60)
        logger.info("SKYNET — Starting initialization")
        logger.info("=" * 60)

        # Load environment variables
        load_dotenv()

        # Get API key
        api_key = api_key or os.getenv("GOOGLE_AI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_AI_API_KEY not set. Set it in .env or pass as argument.")

        # Initialize components in order
        db_path = os.getenv("SKYNET_DB_PATH", "data/skynet.db")
        ledger_db = await init_db(db_path)
        policy_engine = init_policy_engine(auto_approve_read_only)
        planner = init_planner(api_key, model)
        dispatcher = init_dispatcher(policy_engine)
        orchestrator = init_orchestrator(
            planner,
            dispatcher,
            policy_engine,
            ledger_db=ledger_db,
        )

        logger.info("=" * 60)
        logger.info("SKYNET — Initialization complete")
        logger.info("=" * 60)

        return cls(
            planner,
            dispatcher,
            orchestrator,
            policy_engine,
            ledger_db=ledger_db,
        )

    # Public API - delegates to orchestrator
    async def create_task(self, user_intent: str, project_id: str = "default") -> str:
        """Create a new task. Returns job_id."""
        return await self.orchestrator.create_task(user_intent, project_id)

    async def generate_plan(self, job_id: str) -> dict[str, Any]:
        """Generate a plan for a job. Returns PlanSpec."""
        return await self.orchestrator.generate_plan(job_id)

    async def approve_plan(self, job_id: str) -> None:
        """Approve and queue a job for execution."""
        await self.orchestrator.approve_plan(job_id)

    async def deny_plan(self, job_id: str, reason: str = "User denied") -> None:
        """Deny a job plan."""
        await self.orchestrator.deny_plan(job_id, reason)

    async def cancel_job(self, job_id: str) -> None:
        """Cancel a job."""
        await self.orchestrator.cancel_job(job_id)

    async def get_status(self, job_id: str) -> dict[str, Any]:
        """Get job status."""
        return await self.orchestrator.get_status(job_id)

    async def list_jobs(
        self,
        project_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List jobs, optionally filtered."""
        from skynet.ledger.models import JobStatus

        status_enum = JobStatus(status) if status else None
        return await self.orchestrator.list_jobs(project_id, status_enum)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("SKYNET - Shutting down...")

        # Best-effort worker component cleanup when running in-process.
        try:
            from skynet.queue.worker import shutdown_reliability_components

            shutdown_reliability_components()
        except Exception as e:
            logger.debug(f"Worker cleanup skipped: {e}")

        if self.ledger_db:
            await self.ledger_db.close()
        logger.info("SKYNET - Shutdown complete")


# Demo/CLI interface
async def demo() -> None:
    """
    Simple demo of SKYNET functionality.

    This demonstrates the complete flow:
    1. Create task
    2. Generate plan
    3. Review plan
    4. Approve plan
    5. Check status
    """
    # Setup logging
    setup_logging("INFO")

    # Initialize SKYNET
    app = await SkynetApp.create()

    print("\n" + "=" * 60)
    print("SKYNET DEMO")
    print("=" * 60)

    # Create a task
    print("\n[1] Creating task...")
    user_intent = "Check git status and list all modified files"
    job_id = await app.create_task(user_intent, project_id="demo")
    print(f"[OK] Task created: {job_id}")
    print(f"  Intent: {user_intent}")

    # Generate plan
    print("\n[2] Generating plan...")
    plan = await app.generate_plan(job_id)
    print("[OK] Plan generated:")
    print(f"  Summary: {plan.get('summary', 'N/A')}")
    print(f"  Steps: {len(plan.get('steps', []))}")
    for i, step in enumerate(plan.get("steps", []), 1):
        print(f"    {i}. {step.get('title', 'N/A')}")

    # Check status
    print("\n[3] Checking status...")
    status = await app.get_status(job_id)
    print(f"[OK] Status: {status['status']}")
    print(f"  Risk Level: {status['risk_level']}")
    print(f"  Approval Required: {status['approval_required']}")

    # Approve plan
    print("\n[4] Approving plan...")
    await app.approve_plan(job_id)
    status = await app.get_status(job_id)
    print(f"[OK] Plan approved")
    print(f"  New Status: {status['status']}")

    # List jobs
    print("\n[5] Listing jobs...")
    jobs = await app.list_jobs(project_id="demo")
    print(f"[OK] Found {len(jobs)} job(s):")
    for job in jobs:
        print(f"  - {job['id']}: {job['status']}")

    print("\n" + "=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)

    # Shutdown
    await app.shutdown()


# Entry point
async def main() -> None:
    """Main entry point for SKYNET."""
    # For now, just run the demo
    # Later this will start Telegram bot, Celery workers, etc.
    await demo()


if __name__ == "__main__":
    asyncio.run(main())


