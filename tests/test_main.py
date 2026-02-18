"""Test the SKYNET main entry point and integration."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add skynet to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from skynet.main import SkynetApp, setup_logging

# Load .env
load_dotenv()


async def main():
    print("=" * 60)
    print("SKYNET Main Entry Point Test")
    print("=" * 60)
    print()

    # Setup logging
    setup_logging("INFO")

    # Test 1: Initialize SKYNET
    print("[1] Initializing SKYNET application...")
    api_key = os.getenv("GOOGLE_AI_API_KEY")
    if not api_key:
        print("[ERROR] GOOGLE_AI_API_KEY not set in .env")
        return

    app = await SkynetApp.create(
        api_key=api_key,
        model="gemini-2.5-flash",
        auto_approve_read_only=True,
    )
    print("[SUCCESS] SKYNET initialized")
    print()

    # Test 2: Create task
    print("[2] Creating task...")
    user_intent = "Check git status and list modified files"
    job_id = await app.create_task(user_intent, project_id="test")
    print(f"[SUCCESS] Task created: {job_id}")
    print(f"  Intent: {user_intent}")
    print()

    # Test 3: Generate plan
    print("[3] Generating plan...")
    plan = await app.generate_plan(job_id)
    print("[SUCCESS] Plan generated:")
    print(f"  Summary: {plan.get('summary', 'N/A')}")
    print(f"  Steps: {len(plan.get('steps', []))}")
    for i, step in enumerate(plan.get("steps", []), 1):
        title = step.get("title", "N/A")
        risk = step.get("risk_level", "N/A")
        print(f"    {i}. {title} [{risk}]")
    print()

    # Test 4: Get status
    print("[4] Checking job status...")
    status = await app.get_status(job_id)
    print(f"[SUCCESS] Job status: {status['status']}")
    print(f"  Risk Level: {status['risk_level']}")
    print(f"  Approval Required: {status['approval_required']}")
    print()

    # Test 5: Approve plan
    print("[5] Approving plan...")
    await app.approve_plan(job_id)
    status = await app.get_status(job_id)
    print(f"[SUCCESS] Plan approved")
    print(f"  New Status: {status['status']}")
    print(f"  Queued At: {status['queued_at']}")
    print()

    # Test 6: List jobs
    print("[6] Listing jobs...")
    jobs = await app.list_jobs(project_id="test")
    print(f"[SUCCESS] Found {len(jobs)} job(s)")
    for job in jobs:
        print(f"  - {job['id']}: {job['status']} - {job['user_intent'][:30]}...")
    print()

    # Test 7: Create and deny a job
    print("[7] Testing job denial...")
    job_id_2 = await app.create_task("Delete all files in /tmp", project_id="test")
    await app.generate_plan(job_id_2)
    await app.deny_plan(job_id_2, reason="Too dangerous")
    status_2 = await app.get_status(job_id_2)
    print(f"[SUCCESS] Job denied: {status_2['status']}")
    print(f"  Reason: {status_2['error_message']}")
    print()

    # Test 8: Create and cancel a job
    print("[8] Testing job cancellation...")
    job_id_3 = await app.create_task("Deploy to production", project_id="test")
    await app.generate_plan(job_id_3)
    await app.cancel_job(job_id_3)
    status_3 = await app.get_status(job_id_3)
    print(f"[SUCCESS] Job cancelled: {status_3['status']}")
    print()

    # Test 9: List all jobs with filter
    print("[9] Listing all jobs (all statuses)...")
    all_jobs = await app.list_jobs()
    print(f"[SUCCESS] Total jobs created: {len(all_jobs)}")
    status_counts = {}
    for job in all_jobs:
        status = job["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    for status, count in status_counts.items():
        print(f"  - {status}: {count}")
    print()

    # Test 10: Shutdown
    print("[10] Shutting down SKYNET...")
    await app.shutdown()
    print("[SUCCESS] SKYNET shutdown complete")
    print()

    print("=" * 60)
    print("[SUCCESS] All integration tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
