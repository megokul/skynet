"""End-to-end workflow tests (planner -> orchestrator -> dispatcher -> worker)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.core.dispatcher import Dispatcher
from skynet.core.orchestrator import Orchestrator
from skynet.ledger.models import JobStatus
from skynet.policy.engine import PolicyEngine
from skynet.queue.worker import execute_job, shutdown_reliability_components


class FakePlanner:
    """Deterministic planner for E2E tests."""

    async def generate_plan(self, job_id: str, user_intent: str, context: dict | None = None) -> dict:
        text = user_intent.lower()
        if "deploy" in text:
            return {
                "summary": "Deploy workflow",
                "steps": [
                    {
                        "title": "Deploy to production",
                        "description": "Deploy application to production",
                        "risk_level": "ADMIN",
                        "estimated_minutes": 10,
                    }
                ],
            }
        if "create" in text or "write" in text:
            return {
                "summary": "Write workflow",
                "steps": [
                    {
                        "title": "Create file",
                        "description": "Create output file",
                        "risk_level": "WRITE",
                        "estimated_minutes": 3,
                    }
                ],
            }
        if "multi" in text:
            return {
                "summary": "Multi-step workflow",
                "steps": [
                    {
                        "title": "Check git status",
                        "description": "Run git status",
                        "risk_level": "READ_ONLY",
                        "estimated_minutes": 1,
                    },
                    {
                        "title": "Run tests",
                        "description": "Run test suite",
                        "risk_level": "READ_ONLY",
                        "estimated_minutes": 3,
                    },
                ],
            }
        return {
            "summary": "Read-only workflow",
            "steps": [
                {
                    "title": "Check git status",
                    "description": "Run git status",
                    "risk_level": "READ_ONLY",
                    "estimated_minutes": 1,
                }
            ],
        }


async def main() -> None:
    os.environ["SKYNET_DB_PATH"] = str(Path.cwd() / "data" / "e2e_test.db")
    os.environ["SKYNET_ALLOWED_PATHS"] = str(Path.cwd())
    os.environ["SKYNET_WORKER_ID"] = "worker-e2e"

    enqueued: dict[str, dict] = {}

    def fake_enqueue(job_id: str, execution_spec: dict) -> str:
        enqueued[job_id] = execution_spec
        return f"task-{job_id}"

    policy = PolicyEngine(auto_approve_read_only=True)
    planner = FakePlanner()
    dispatcher = Dispatcher(
        policy_engine=policy,
        enqueue_fn=fake_enqueue,
        default_provider="local",
        default_sandbox_root=str(Path.cwd()),
    )
    orchestrator = Orchestrator(
        planner=planner,
        dispatcher=dispatcher,
        policy_engine=policy,
    )

    # 1) READ_ONLY flow
    read_job = await orchestrator.create_task("Check repo status", project_id="e2e")
    await orchestrator.generate_plan(read_job)
    status = await orchestrator.get_status(read_job)
    assert status["status"] == JobStatus.PLANNED.value
    assert status["approval_required"] is False
    await orchestrator.approve_plan(read_job)
    read_result = await asyncio.to_thread(execute_job, read_job, enqueued[read_job])
    assert read_result["status"] in {"success", "partial_failure"}

    # 2) WRITE flow (approval required)
    write_job = await orchestrator.create_task("Create output file", project_id="e2e")
    await orchestrator.generate_plan(write_job)
    status = await orchestrator.get_status(write_job)
    assert status["approval_required"] is True
    await orchestrator.approve_plan(write_job)
    write_result = await asyncio.to_thread(execute_job, write_job, enqueued[write_job])
    assert write_result["status"] in {"success", "partial_failure"}

    # 3) ADMIN flow (approval required)
    admin_job = await orchestrator.create_task("Deploy production release", project_id="e2e")
    await orchestrator.generate_plan(admin_job)
    status = await orchestrator.get_status(admin_job)
    assert status["approval_required"] is True
    await orchestrator.approve_plan(admin_job)
    admin_result = await asyncio.to_thread(execute_job, admin_job, enqueued[admin_job])
    assert admin_result["status"] in {"success", "partial_failure"}

    # 4) Cancellation flow
    cancel_job = await orchestrator.create_task("Create cancel test file", project_id="e2e")
    await orchestrator.generate_plan(cancel_job)
    await orchestrator.cancel_job(cancel_job)
    status = await orchestrator.get_status(cancel_job)
    assert status["status"] == JobStatus.CANCELLED.value

    # 5) Error handling (bad provider)
    bad_spec = {
        "job_id": "job-bad-provider",
        "provider": "nonexistent",
        "actions": [{"action": "git_status", "params": {"working_dir": str(Path.cwd())}}],
    }
    bad_result = await asyncio.to_thread(execute_job, "job-bad-provider", bad_spec)
    assert bad_result["status"] == "partial_failure"

    # 6) Multi-step flow
    multi_job = await orchestrator.create_task("Run multi workflow", project_id="e2e")
    await orchestrator.generate_plan(multi_job)
    await orchestrator.approve_plan(multi_job)
    multi_result = await asyncio.to_thread(execute_job, multi_job, enqueued[multi_job])
    assert len(multi_result["results"]) >= 2

    await asyncio.to_thread(shutdown_reliability_components)
    print("[SUCCESS] E2E workflow tests passed")


if __name__ == "__main__":
    asyncio.run(main())
