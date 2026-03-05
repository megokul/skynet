from __future__ import annotations

import pytest

from db.schema import init_db
from db.store import (
    create_architecture_state,
    create_learning_event,
    create_node_worker_assignment,
    create_project,
    create_task_graph,
    create_task_node,
    create_task_strategy,
    ensure_user,
    get_active_architecture_state,
    get_active_prompt_policy,
    list_active_workers,
    list_learning_events,
    supersede_architecture_state,
    upsert_prompt_policy,
    upsert_worker_registry,
)
from orchestration.architect import parse_architecture_state
from orchestration.director import parse_director_contract
from orchestration.learning import build_conservative_prompt_policy
from orchestration.worker_pool import select_worker


def test_parse_director_contract_json() -> None:
    payload = parse_director_contract(
        '{"objective":"Ship feature","scope":"coding","success_metrics":["tests pass"],'
        '"risk_budget":{"max_repairs":1,"max_runtime_seconds":1800},"constraints":["strict gates"]}'
    )
    assert payload["objective"] == "Ship feature"
    assert payload["risk_budget"]["max_repairs"] == 1


def test_parse_architecture_state_json() -> None:
    payload = parse_architecture_state(
        '{"components":[{"name":"api"}],"interfaces":[{"name":"cli"}],'
        '"boundaries":[{"from":"api","to":"db","allowed":true}],'
        '"data_flows":[],"constraints":["no cross-layer imports"],"adr_summary":"ok"}'
    )
    assert payload["components"][0]["name"] == "api"
    assert payload["adr_summary"] == "ok"


def test_worker_pool_selects_by_capability_then_priority() -> None:
    worker_id, reason = select_worker(
        workers=[
            {"id": "worker-a", "priority": 100, "capabilities": ["code"]},
            {"id": "worker-b", "priority": 200, "capabilities": ["code", "docker"]},
        ],
        required_capabilities=["docker"],
        default_worker_id="worker-primary",
    )
    assert worker_id == "worker-b"
    assert reason.startswith("capability-match")


def test_learning_policy_requires_min_samples() -> None:
    policy_none = build_conservative_prompt_policy(
        events=[{"failure_type": "TEST_FAILED", "pattern_key": "TEST_FAILED:work"}],
        min_samples=2,
        apply_mode="conservative",
    )
    assert policy_none is None

    policy = build_conservative_prompt_policy(
        events=[
            {"failure_type": "TEST_FAILED", "pattern_key": "TEST_FAILED:work"},
            {"failure_type": "TEST_FAILED", "pattern_key": "TEST_FAILED:work"},
            {"failure_type": "CONTRACT_FAILED", "pattern_key": "CONTRACT_FAILED:work"},
        ],
        min_samples=3,
        apply_mode="conservative",
    )
    assert isinstance(policy, dict)
    assert policy["mode"] == "conservative"
    assert policy["sample_count"] == 3


@pytest.mark.asyncio
async def test_store_loop_v2_tables_roundtrip() -> None:
    db = await init_db(":memory:")
    try:
        user = await ensure_user(db, telegram_user_id=99, username="loopv2")
        project = await create_project(
            db,
            user_id=int(user["id"]),
            name="loop-v2",
            project_type="Other",
            control_loop_profile="loop_v2",
        )
        graph = await create_task_graph(
            db,
            project_id=str(project["id"]),
            goal="ship",
            status="active",
            planner_summary="plan",
        )
        node = await create_task_node(
            db,
            graph_id=int(graph["id"]),
            node_key="work_1",
            title="Work",
            node_type="work",
            owner="codex",
            tools_required=["code", "test"],
            risk_level="medium",
        )

        state = await create_architecture_state(
            db,
            project_id=str(project["id"]),
            version=1,
            status="active",
            components=[{"name": "app"}],
            interfaces=[{"name": "cli"}],
            boundaries=[{"from": "app", "to": "infra", "allowed": True}],
            data_flows=[],
            constraints=["strict"],
            adr_summary="v1",
        )
        assert int(state["version"]) == 1
        active_state = await get_active_architecture_state(db, project_id=str(project["id"]))
        assert active_state is not None
        assert int(active_state["version"]) == 1
        await supersede_architecture_state(db, project_id=str(project["id"]), previous_version=1)
        state2 = await create_architecture_state(
            db,
            project_id=str(project["id"]),
            version=2,
            status="active",
            components=[{"name": "app2"}],
            interfaces=[],
            boundaries=[],
            data_flows=[],
            constraints=[],
            adr_summary="v2",
        )
        assert int(state2["version"]) == 2

        strategy = await create_task_strategy(
            db,
            graph_id=int(graph["id"]),
            parallel_lanes=[{"lane": "core"}],
            risk_assessment=[{"node": "work_1", "risk": "medium"}],
            execution_strategy={"mode": "adaptive_parallel_x2"},
        )
        assert strategy is not None

        worker = await upsert_worker_registry(
            db,
            worker_id="worker-primary",
            label="Primary",
            capabilities=["code", "test"],
            priority=200,
        )
        assert worker["id"] == "worker-primary"
        workers = await list_active_workers(db)
        assert workers
        assign = await create_node_worker_assignment(
            db,
            graph_id=int(graph["id"]),
            node_id=int(node["id"]),
            worker_id="worker-primary",
            assignment_reason="capability-match:2",
        )
        assert assign["worker_id"] == "worker-primary"

        await create_learning_event(
            db,
            project_id=str(project["id"]),
            graph_id=int(graph["id"]),
            node_id=int(node["id"]),
            failure_type="TEST_FAILED",
            critic_code="REV-1",
            pattern_key="TEST_FAILED:REV-1:work",
            event={"summary": "pytest failed"},
        )
        learning_rows = await list_learning_events(db, project_id=str(project["id"]))
        assert len(learning_rows) == 1

        await upsert_prompt_policy(
            db,
            scope="project",
            project_id=str(project["id"]),
            policy_kind="repair",
            policy={"hints": ["write tests first"]},
            source="learning",
            active=True,
        )
        policy = await get_active_prompt_policy(
            db,
            scope="project",
            project_id=str(project["id"]),
            policy_kind="repair",
        )
        assert policy is not None
        assert policy["policy"]["hints"][0] == "write tests first"
    finally:
        await db.close()
