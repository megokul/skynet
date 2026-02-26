"""Tests for SKYNET worker registry."""

from __future__ import annotations

import asyncio

import pytest

from skynet.ledger.schema import init_db
from skynet.ledger.worker_registry import WorkerRegistry


@pytest.mark.asyncio
async def test_worker_registry_flow() -> None:
    """
    Test scenario `test_worker_registry_flow`.
    
    Purpose:
    - Implement `test_worker_registry_flow` within this module's workflow.
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

    db = await init_db(":memory:")
    registry = WorkerRegistry(db, heartbeat_timeout_seconds=1)

    worker = await registry.register_worker(
        worker_id="worker-1",
        provider_name="openclaw",
        capabilities=["route-task"],
        metadata={"host": "gateway-a"},
    )
    assert worker is not None
    assert worker["status"] == "online"

    online = await registry.get_online_workers()
    assert len(online) == 1
    assert online[0]["id"] == "worker-1"

    ok = await registry.mark_offline("worker-1")
    assert ok is True
    online = await registry.get_online_workers()
    assert len(online) == 0

    await registry.register_worker("worker-2", "openclaw")
    await asyncio.sleep(1.2)
    cleaned = await registry.cleanup_stale_workers()
    assert cleaned >= 1
    online = await registry.get_online_workers()
    assert len(online) == 0

    await db.close()
