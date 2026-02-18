"""Tests for SKYNET job lock manager."""

from __future__ import annotations

import asyncio

from skynet.ledger.job_locking import JobLockManager
from skynet.ledger.schema import init_db


async def main() -> None:
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
    print("[SUCCESS] JobLockManager tests passed")


if __name__ == "__main__":
    asyncio.run(main())
