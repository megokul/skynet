"""Tests for SKYNET job lock manager."""

from __future__ import annotations

import asyncio

import pytest

from skynet.ledger.job_locking import JobLockManager
from skynet.ledger.schema import init_db


@pytest.mark.asyncio
async def test_job_lock_manager_flow() -> None:
    """
    Test scenario `test_job_lock_manager_flow`.
    
    Purpose:
    - Implement `test_job_lock_manager_flow` within this module's workflow.
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
    locks = JobLockManager(db, lock_timeout_seconds=1)

    acquired = await locks.acquire_lock("job-1", "worker-a")
    assert acquired is True
    acquired_again = await locks.acquire_lock("job-1", "worker-b")
    assert acquired_again is False

    owner = await locks.get_lock_owner("job-1")
    assert owner == "worker-a"
    assert await locks.is_locked("job-1") is True

    extended = await locks.extend_lock("job-1", "worker-a", additional_seconds=2)
    assert extended is True
    refreshed = await locks.refresh_lock("job-1", "worker-a", timeout_seconds=3)
    assert refreshed is True
    record = await locks.get_lock("job-1")
    assert record is not None
    assert record["worker_id"] == "worker-a"
    assert record["expires_at"]

    released_wrong = await locks.release_lock("job-1", "worker-b")
    assert released_wrong is False
    released = await locks.release_lock("job-1", "worker-a")
    assert released is True
    assert await locks.is_locked("job-1") is False

    await locks.acquire_lock("job-2", "worker-x", timeout_seconds=1)
    await asyncio.sleep(1.2)
    cleaned = await locks.cleanup_expired_locks()
    assert cleaned >= 1
    assert await locks.is_locked("job-2") is False

    await db.close()
