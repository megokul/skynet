"""Control-plane contract API tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api import schemas
from skynet.api.routes import (
    app_state,
    list_agents,
    list_events,
    get_control_registry,
    get_gateway_client,
    get_system_state,
    get_next_task_preview,
    register_gateway,
    register_worker,
    route_task,
)
from skynet.control_plane import ControlPlaneRegistry
from skynet.ledger.schema import init_db
from skynet.ledger.task_queue import TaskQueueManager


class StubGatewayClient:
    async def get_gateway_status(self, host: str):  # noqa: ARG002
        return {"agent_connected": True}

    async def execute_task(  # noqa: ARG002
        self,
        host: str,
        action: str,
        params=None,
        confirmed=True,
        task_id=None,
        idempotency_key=None,
    ):
        return {
            "status": "success",
            "action": action,
            "result": {"params": params or {}, "confirmed": confirmed},
        }


@pytest.mark.asyncio
async def test_control_registry_dependency_uninitialized() -> None:
    app_state.control_registry = None
    with pytest.raises(HTTPException) as exc_info:
        get_control_registry()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_gateway_client_dependency_uninitialized() -> None:
    app_state.gateway_client = None
    with pytest.raises(HTTPException) as exc_info:
        get_gateway_client()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_register_gateway_and_route_task() -> None:
    registry = ControlPlaneRegistry()
    client = StubGatewayClient()
    app_state.control_registry = registry
    app_state.gateway_client = client
    app_state.worker_registry = None

    register_req = schemas.RegisterGatewayRequest(
        gateway_id="gw-1",
        host="http://127.0.0.1:8766",
        capabilities=["execute_task"],
    )
    register_resp = await register_gateway(
        request=register_req,
        registry=registry,
        gateway_client=client,
    )
    assert register_resp.gateway_id == "gw-1"
    assert register_resp.status in {"online", "degraded"}

    worker_req = schemas.RegisterWorkerRequest(
        worker_id="worker-1",
        gateway_id="gw-1",
        capabilities=["shell"],
    )
    worker_resp = await register_worker(request=worker_req, registry=registry)
    assert worker_resp.worker_id == "worker-1"
    assert worker_resp.gateway_id == "gw-1"

    route_req = schemas.RouteTaskRequest(
        action="git_status",
        params={"working_dir": "."},
        gateway_id="gw-1",
    )
    route_resp = await route_task(
        request=route_req,
        registry=registry,
        gateway_client=client,
    )
    assert route_resp.gateway_id == "gw-1"
    assert route_resp.status == "success"
    assert route_resp.result["action"] == "git_status"

    state_resp = await get_system_state(registry=registry)
    assert state_resp.gateway_count == 1
    assert state_resp.worker_count == 1


@pytest.mark.asyncio
async def test_route_task_without_gateway_fails() -> None:
    registry = ControlPlaneRegistry()
    client = StubGatewayClient()
    app_state.control_registry = registry
    app_state.gateway_client = client

    request = schemas.RouteTaskRequest(action="git_status", params={})
    with pytest.raises(HTTPException) as exc_info:
        await route_task(request=request, registry=registry, gateway_client=client)
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_read_models_tasks_next_agents_and_events() -> None:
    registry = ControlPlaneRegistry()
    db = await init_db(":memory:")
    q = TaskQueueManager(db)

    app_state.control_registry = registry
    app_state.task_queue = q

    registry.register_worker(worker_id="worker-1", gateway_id="gw-1", status="online")
    await q.enqueue_task(task_id="task-read-1", action="git_status")

    next_resp = await get_next_task_preview(agent_id="worker-1", task_queue=q)
    assert next_resp.eligible is True
    assert next_resp.task is not None
    assert next_resp.task.id == "task-read-1"

    claim = await q.claim_next_ready_task(worker_id="worker-1")
    assert claim is not None
    started = await q.mark_task_running(
        task_id="task-read-1",
        worker_id="worker-1",
        claim_token=claim["claim_token"],
    )
    assert started is True

    agents_resp = await list_agents(registry=registry, task_queue=q)
    assert len(agents_resp.agents) == 1
    assert agents_resp.agents[0].agent_id == "worker-1"
    assert agents_resp.agents[0].active_task_id == "task-read-1"

    events_resp = await list_events(task_queue=q, limit=20)
    assert any(e.task_id == "task-read-1" and e.event_type == "claimed" for e in events_resp.events)

    await db.close()
