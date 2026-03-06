from __future__ import annotations

import pytest

from db.schema import init_db
from db.store import create_project, ensure_user, get_active_task_graph, list_graph_nodes
from orchestration.graph import LoopNode
from orchestration.loop_controller import ClosedLoopController


@pytest.mark.asyncio
async def test_closed_loop_creates_repair_node_and_completes():
    db = await init_db(":memory:")
    user = await ensure_user(db, telegram_user_id=1, username="u")
    project = await create_project(
        db,
        user_id=int(user["id"]),
        name="loop-test",
        project_type="Python App",
        control_loop_profile="loop_v1",
    )

    controller = ClosedLoopController(
        db=db,
        project_id=str(project["id"]),
        goal="Implement one milestone",
        milestones=["Build feature"],
        repair_retries=1,
        max_parallel_nodes=1,
        memory_enabled=True,
    )
    graph_id = await controller.bootstrap(planner_summary="summary")

    critic_calls = {"count": 0}

    async def _work_exec(node: LoopNode) -> dict:
        return {"ok": True, "summary": f"{node.node_key} done"}

    async def _critic_exec(node: LoopNode) -> dict:
        critic_calls["count"] += 1
        if critic_calls["count"] == 1:
            return {
                "ok": False,
                "blocking": True,
                "summary": "blocking finding",
                "findings": [
                    {
                        "severity": "high",
                        "code": "REV-1",
                        "message": "Fix me",
                        "files": ["main.py"],
                        "suggested_fix": "apply patch",
                    }
                ],
                "critic_name": "review",
            }
        return {"ok": True, "blocking": False, "summary": "critic passed", "findings": [], "critic_name": "review"}

    async def _gate_exec(node: LoopNode) -> dict:
        return {"ok": True, "summary": "gate passed"}

    result = await controller.run(
        execute_work=_work_exec,
        execute_critic=_critic_exec,
        execute_gate=_gate_exec,
    )
    assert result["status"] == "completed"
    rows = await list_graph_nodes(db, graph_id=graph_id)
    assert any(str(row.get("node_type")) == "repair" for row in rows)
    assert all(str(row.get("status")) == "done" for row in rows)
    await db.close()


@pytest.mark.asyncio
async def test_closed_loop_executor_exception_fails_graph_without_crash():
    db = await init_db(":memory:")
    user = await ensure_user(db, telegram_user_id=2, username="u2")
    project = await create_project(
        db,
        user_id=int(user["id"]),
        name="loop-exception-test",
        project_type="Python App",
        control_loop_profile="loop_v1",
    )

    controller = ClosedLoopController(
        db=db,
        project_id=str(project["id"]),
        goal="Trigger work executor exception",
        milestones=["Run work that crashes"],
        repair_retries=0,
        max_parallel_nodes=1,
        memory_enabled=True,
    )
    graph_id = await controller.bootstrap(planner_summary="summary")

    async def _work_exec(node: LoopNode) -> dict:
        raise RuntimeError(f"WAIT_TIMEOUT: {node.node_key} exceeded 900s")

    async def _critic_exec(node: LoopNode) -> dict:
        return {"ok": True, "blocking": False, "summary": "critic passed", "findings": []}

    async def _gate_exec(node: LoopNode) -> dict:
        return {"ok": True, "summary": "gate passed"}

    result = await controller.run(
        execute_work=_work_exec,
        execute_critic=_critic_exec,
        execute_gate=_gate_exec,
    )
    assert result["status"] == "failed"
    assert result["failure_type"] == "ENVIRONMENT_FAILED"

    rows = await list_graph_nodes(db, graph_id=graph_id)
    work_nodes = [row for row in rows if str(row.get("node_type")) == "work"]
    assert work_nodes
    assert str(work_nodes[0].get("status")) in {"failed", "failed_infra"}

    active_graph = await get_active_task_graph(db, project_id=str(project["id"]))
    assert active_graph is None
    await db.close()
