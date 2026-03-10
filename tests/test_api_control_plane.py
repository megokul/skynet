"""Control-plane contract API tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.api import schemas
from skynet.api.routes import (
    acquire_lease,
    app_state,
    get_lease_manager,
    list_agents,
    list_events,
    get_control_registry,
    get_gateway_client,
    get_system_state,
    get_next_task_preview,
    inspect_lease,
    register_gateway,
    register_worker,
    release_lease,
    renew_lease,
    route_task,
)
from skynet.control_plane import ControlPlaneRegistry
from skynet.ledger.job_locking import JobLockManager
from skynet.ledger.schema import init_db
from skynet.ledger.task_queue import TaskQueueManager


class StubGatewayClient:
    """
    StubGatewayClient.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `StubGatewayClient`.
    """

    async def get_gateway_status(self, host: str):  # noqa: ARG002
        """
        Get gateway status.
        
        Purpose:
        - Implement `get_gateway_status` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `host`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

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
        """
        Execute task.
        
        Purpose:
        - Implement `execute_task` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `host`: input used by this function to compute or route work.
        - `action`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        - `confirmed`: input used by this function to compute or route work.
        - `task_id`: input used by this function to compute or route work.
        - `idempotency_key`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        return {
            "status": "success",
            "action": action,
            "result": {"params": params or {}, "confirmed": confirmed},
        }


@pytest.mark.asyncio
async def test_control_registry_dependency_uninitialized() -> None:
    """
    Test scenario `test_control_registry_dependency_uninitialized`.
    
    Purpose:
    - Implement `test_control_registry_dependency_uninitialized` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    app_state.control_registry = None
    with pytest.raises(HTTPException) as exc_info:
        get_control_registry()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_gateway_client_dependency_uninitialized() -> None:
    """
    Test scenario `test_gateway_client_dependency_uninitialized`.
    
    Purpose:
    - Implement `test_gateway_client_dependency_uninitialized` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    app_state.gateway_client = None
    with pytest.raises(HTTPException) as exc_info:
        get_gateway_client()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_lease_manager_dependency_uninitialized() -> None:
    app_state.lease_manager = None
    with pytest.raises(HTTPException) as exc_info:
        get_lease_manager()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_register_gateway_and_route_task() -> None:
    """
    Test scenario `test_register_gateway_and_route_task`.
    
    Purpose:
    - Implement `test_register_gateway_and_route_task` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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
    """
    Test scenario `test_route_task_without_gateway_fails`.
    
    Purpose:
    - Implement `test_route_task_without_gateway_fails` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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
    """
    Test scenario `test_read_models_tasks_next_agents_and_events`.
    
    Purpose:
    - Implement `test_read_models_tasks_next_agents_and_events` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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


@pytest.mark.asyncio
async def test_control_plane_lease_api_flow() -> None:
    db = await init_db(":memory:")
    lease_manager = JobLockManager(db, lock_timeout_seconds=5)
    app_state.lease_manager = lease_manager

    acquired = await acquire_lease(
        lease_name="telegram-poller:test",
        request=schemas.LeaseAcquireRequest(owner_id="gw-1", ttl_seconds=30),
        lease_manager=lease_manager,
    )
    assert acquired.acquired is True
    assert acquired.owner_id == "gw-1"
    assert acquired.held is True

    held = await inspect_lease(
        lease_name="telegram-poller:test",
        lease_manager=lease_manager,
    )
    assert held.held is True
    assert held.owner_id == "gw-1"

    renewed = await renew_lease(
        lease_name="telegram-poller:test",
        request=schemas.LeaseRenewRequest(owner_id="gw-1", ttl_seconds=30),
        lease_manager=lease_manager,
    )
    assert renewed.renewed is True
    assert renewed.owner_id == "gw-1"

    released = await release_lease(
        lease_name="telegram-poller:test",
        request=schemas.LeaseReleaseRequest(owner_id="gw-1"),
        lease_manager=lease_manager,
    )
    assert released.ok is True

    held_after = await inspect_lease(
        lease_name="telegram-poller:test",
        lease_manager=lease_manager,
    )
    assert held_after.held is False

    await db.close()
