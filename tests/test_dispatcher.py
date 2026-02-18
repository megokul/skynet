"""Dispatcher component tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.core.dispatcher import Dispatcher
from skynet.shared.errors import PolicyViolationError


async def test_step_mapping_and_enqueue() -> None:
    calls: list[tuple[str, dict]] = []

    def fake_enqueue(job_id: str, spec: dict) -> str:
        calls.append((job_id, spec))
        return "task-123"

    dispatcher = Dispatcher(enqueue_fn=fake_enqueue, default_sandbox_root="E:/MyProjects/skynet")
    plan = {
        "project_id": "proj-1",
        "summary": "Check and build",
        "max_risk_level": "WRITE",
        "steps": [
            {"title": "Check git status", "description": "Run git status", "risk_level": "READ_ONLY"},
            {"title": "Run tests", "description": "Execute pytest", "risk_level": "READ_ONLY"},
            {"title": "Build app", "description": "Build the project", "risk_level": "WRITE"},
        ],
    }

    spec = await dispatcher.dispatch("job-1", plan)

    assert spec.steps[0].action == "git_status"
    assert spec.steps[1].action == "run_tests"
    assert spec.steps[2].action == "build_project"
    assert spec.metadata.get("queue_task_id") == "task-123"
    assert len(calls) == 1
    assert calls[0][0] == "job-1"


async def test_fallback_mapping() -> None:
    dispatcher = Dispatcher(enqueue_fn=lambda *_: "task-fallback")
    plan = {
        "project_id": "proj-2",
        "summary": "Unmapped",
        "steps": [
            {
                "title": "Do something unusual",
                "description": "Perform an uncommon workflow",
                "risk_level": "READ_ONLY",
            }
        ],
    }

    spec = await dispatcher.dispatch("job-2", plan)
    assert spec.steps[0].action == "list_directory"
    assert spec.metadata.get("unmapped_steps") == [0]


async def test_policy_blocking_on_risk_mismatch() -> None:
    dispatcher = Dispatcher(enqueue_fn=lambda *_: "task-should-not-run")
    plan = {
        "project_id": "proj-3",
        "summary": "Deploy service",
        "max_risk_level": "READ_ONLY",
        "steps": [
            {
                "title": "Deploy to production",
                "description": "Deploy latest release",
                "risk_level": "READ_ONLY",
            }
        ],
    }

    try:
        await dispatcher.dispatch("job-3", plan)
    except PolicyViolationError:
        return
    raise AssertionError("Expected PolicyViolationError for admin action under READ_ONLY risk")


async def main() -> None:
    await test_step_mapping_and_enqueue()
    await test_fallback_mapping()
    await test_policy_blocking_on_risk_mismatch()
    print("[SUCCESS] Dispatcher tests passed")


if __name__ == "__main__":
    asyncio.run(main())
