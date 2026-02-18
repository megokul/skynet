"""Orchestrator DB persistence tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.chathan.protocol.execution_spec import ExecutionSpec, ExecutionStep
from skynet.core.orchestrator import Orchestrator
from skynet.ledger.schema import init_db
from skynet.policy.engine import PolicyEngine


class FakePlanner:
    async def generate_plan(self, job_id: str, user_intent: str, context: dict | None = None) -> dict:
        return {
            "summary": f"Plan for {user_intent}",
            "steps": [
                {
                    "title": "Check repository state",
                    "description": "Run git status",
                    "risk_level": "READ_ONLY",
                    "estimated_minutes": 2,
                }
            ],
            "artifacts": ["status.txt"],
            "total_estimated_minutes": 2,
        }


class FakeDispatcher:
    async def dispatch(self, job_id: str, plan_spec: dict) -> ExecutionSpec:
        return ExecutionSpec(
            job_id=job_id,
            project_id=plan_spec.get("project_id", "default"),
            provider="mock",
            risk_level="READ_ONLY",
            steps=[
                ExecutionStep(
                    action="git_status",
                    params={"working_dir": "."},
                    timeout_sec=60,
                    description="Run git status",
                )
            ],
        )


async def main() -> None:
    db = await init_db(":memory:")
    policy = PolicyEngine(auto_approve_read_only=True)
    planner = FakePlanner()
    dispatcher = FakeDispatcher()

    orch1 = Orchestrator(
        planner=planner,
        dispatcher=dispatcher,
        policy_engine=policy,
        ledger_db=db,
    )

    job_id = await orch1.create_task("Check git status", project_id="persist_proj")
    await orch1.generate_plan(job_id)
    await orch1.approve_plan(job_id)

    # New orchestrator instance over same DB to prove persistence beyond memory cache.
    orch2 = Orchestrator(
        planner=planner,
        dispatcher=dispatcher,
        policy_engine=policy,
        ledger_db=db,
    )

    status = await orch2.get_status(job_id)
    assert status["status"] == "queued"
    assert status["execution_spec"]
    assert status["project_id"] == "persist_proj"

    jobs = await orch2.list_jobs(project_id="persist_proj")
    assert any(j["id"] == job_id for j in jobs)

    await db.close()
    print("[SUCCESS] Orchestrator persistence tests passed")


if __name__ == "__main__":
    asyncio.run(main())
