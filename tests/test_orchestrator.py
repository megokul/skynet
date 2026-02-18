"""Test the Orchestrator component."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add skynet to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from skynet.core.planner import Planner
from skynet.core.dispatcher import Dispatcher
from skynet.core.orchestrator import Orchestrator
from skynet.policy.engine import PolicyEngine
from skynet.ledger.models import JobStatus, RiskLevel

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

# Load .env
load_dotenv()


async def main():
    print("=" * 60)
    print("SKYNET Orchestrator Test")
    print("=" * 60)
    print()

    # Initialize components
    print("[1] Initializing components...")

    # Planner with Gemini
    api_key = os.getenv("GOOGLE_AI_API_KEY")
    if not api_key:
        print("[ERROR] GOOGLE_AI_API_KEY not set in .env")
        return

    planner = Planner(api_key=api_key, model="gemini-2.5-flash")

    # Policy Engine
    policy_engine = PolicyEngine()

    # Dispatcher (with mock queue)
    def mock_enqueue(job_id: str, exec_spec: dict):
        print(f"  [MOCK] Enqueued job {job_id} with {len(exec_spec.get('actions', []))} actions")

    dispatcher = Dispatcher(
        policy_engine=policy_engine,
        enqueue_fn=mock_enqueue,
    )

    # Orchestrator
    orchestrator = Orchestrator(
        planner=planner,
        dispatcher=dispatcher,
        policy_engine=policy_engine,
    )

    print("[SUCCESS] Components initialized")
    print()

    # Test 1: Create Task
    print("[2] Creating task...")
    user_intent = "Check git status and list modified files"
    job_id = await orchestrator.create_task(user_intent, project_id="test_proj")
    print(f"[SUCCESS] Task created: {job_id}")
    print(f"  Intent: {user_intent}")
    print()

    # Test 2: Generate Plan
    print("[3] Generating plan...")
    plan = await orchestrator.generate_plan(job_id)
    print("[SUCCESS] Plan generated:")
    print(f"  Summary: {plan.get('summary', 'N/A')}")
    print(f"  Steps: {len(plan.get('steps', []))}")
    for i, step in enumerate(plan.get("steps", []), 1):
        print(f"    {i}. {step.get('description', 'N/A')} [{step.get('risk', 'N/A')}]")
    print(f"  Risk Level: {plan.get('risk', 'N/A')}")
    print(f"  Artifacts: {plan.get('artifacts', [])}")
    print()

    # Test 3: Get Status
    print("[4] Checking job status...")
    status = await orchestrator.get_status(job_id)
    print(f"[SUCCESS] Job status: {status['status']}")
    print(f"  Risk Level: {status['risk_level']}")
    print(f"  Approval Required: {status['approval_required']}")
    print()

    # Test 4: Approve Plan
    print("[5] Approving plan...")
    await orchestrator.approve_plan(job_id)
    status = await orchestrator.get_status(job_id)
    print(f"[SUCCESS] Plan approved")
    print(f"  New Status: {status['status']}")
    print(f"  Queued At: {status['queued_at']}")
    print()

    # Test 5: List Jobs
    print("[6] Listing jobs...")
    jobs = await orchestrator.list_jobs(project_id="test_proj")
    print(f"[SUCCESS] Found {len(jobs)} job(s)")
    for job in jobs:
        print(f"  - {job['id']}: {job['status']} - {job['user_intent'][:30]}...")
    print()

    # Test 6: Cancel Job (create new job for this)
    print("[7] Testing job cancellation...")
    job_id_2 = await orchestrator.create_task(
        "Deploy bot to production",
        project_id="test_proj"
    )
    await orchestrator.generate_plan(job_id_2)
    await orchestrator.cancel_job(job_id_2)
    status_2 = await orchestrator.get_status(job_id_2)
    print(f"[SUCCESS] Job cancelled: {status_2['status']}")
    print()

    # Test 7: Deny Plan (create new job for this)
    print("[8] Testing plan denial...")
    job_id_3 = await orchestrator.create_task(
        "Delete all files in /tmp",
        project_id="test_proj"
    )
    await orchestrator.generate_plan(job_id_3)
    await orchestrator.deny_plan(job_id_3, reason="Too dangerous")
    status_3 = await orchestrator.get_status(job_id_3)
    print(f"[SUCCESS] Plan denied: {status_3['status']}")
    print(f"  Reason: {status_3['error_message']}")
    print()

    print("=" * 60)
    print("[SUCCESS] All Orchestrator tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
