"""Control-plane scheduler queue tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from skynet.ledger.schema import init_db
from skynet.ledger.task_queue import TaskQueueManager


@pytest.mark.asyncio
async def test_dependency_order_is_enforced() -> None:
    db = await init_db(":memory:")
    q = TaskQueueManager(db)

    t1 = await q.enqueue_task(task_id="t1", action="file_write")
    assert t1["id"] == "t1"
    t2 = await q.enqueue_task(task_id="t2", action="git_commit", dependencies=["t1"])
    assert t2["dependencies"] == ["t1"]

    c1 = await q.claim_next_ready_task(worker_id="w1")
    assert c1 is not None
    assert c1["id"] == "t1"
    assert c1["status"] == "claimed"

    # t2 is still blocked because dependency t1 isn't succeeded yet.
    c_blocked = await q.claim_next_ready_task(worker_id="w2")
    assert c_blocked is None

    started = await q.mark_task_running(
        task_id="t1",
        worker_id="w1",
        claim_token=c1["claim_token"],
    )
    assert started is True

    ok = await q.complete_task(
        task_id="t1",
        worker_id="w1",
        claim_token=c1["claim_token"],
        success=True,
        result={"done": True},
    )
    assert ok is True

    c2 = await q.claim_next_ready_task(worker_id="w2")
    assert c2 is not None
    assert c2["id"] == "t2"

    await db.close()


@pytest.mark.asyncio
async def test_file_ownership_blocks_conflicts() -> None:
    db = await init_db(":memory:")
    q = TaskQueueManager(db)

    await q.enqueue_task(task_id="a", action="file_write", required_files=["src/app.py"])
    await q.enqueue_task(task_id="b", action="file_write", required_files=["src/app.py"])

    c1 = await q.claim_next_ready_task(worker_id="w1")
    assert c1 is not None
    assert c1["id"] == "a"
    started = await q.mark_task_running(
        task_id="a",
        worker_id="w1",
        claim_token=c1["claim_token"],
    )
    assert started is True

    # b should not claim while file is owned by a.
    c2 = await q.claim_next_ready_task(worker_id="w2")
    assert c2 is None

    released = await q.complete_task(
        task_id="a",
        worker_id="w1",
        claim_token=c1["claim_token"],
        success=True,
    )
    assert released is True

    c3 = await q.claim_next_ready_task(worker_id="w2")
    assert c3 is not None
    assert c3["id"] == "b"

    await db.close()


@pytest.mark.asyncio
async def test_claim_is_exclusive() -> None:
    db = await init_db(":memory:")
    q = TaskQueueManager(db)
    await q.enqueue_task(task_id="only-task", action="echo")

    r1 = await q.claim_next_ready_task(worker_id="w1")
    r2 = await q.claim_next_ready_task(worker_id="w2")
    assert r1 is not None
    assert r1["id"] == "only-task"
    assert r1["status"] == "claimed"
    assert r2 is None

    await db.close()


@pytest.mark.asyncio
async def test_illegal_transition_complete_without_running_is_rejected() -> None:
    db = await init_db(":memory:")
    q = TaskQueueManager(db)
    await q.enqueue_task(task_id="t-illegal", action="echo")

    claim = await q.claim_next_ready_task(worker_id="w1")
    assert claim is not None
    ok = await q.complete_task(
        task_id="t-illegal",
        worker_id="w1",
        claim_token=claim["claim_token"],
        success=True,
    )
    assert ok is False

    await db.close()


@pytest.mark.asyncio
async def test_release_after_success_is_rejected() -> None:
    db = await init_db(":memory:")
    q = TaskQueueManager(db)
    await q.enqueue_task(task_id="t-release", action="echo")

    claim = await q.claim_next_ready_task(worker_id="w1")
    assert claim is not None
    started = await q.mark_task_running(
        task_id="t-release",
        worker_id="w1",
        claim_token=claim["claim_token"],
    )
    assert started is True
    done = await q.complete_task(
        task_id="t-release",
        worker_id="w1",
        claim_token=claim["claim_token"],
        success=True,
    )
    assert done is True

    released = await q.release_claim(
        task_id="t-release",
        worker_id="w1",
        claim_token=claim["claim_token"],
        reason="should not release terminal tasks",
        back_to_pending=True,
    )
    assert released is False

    await db.close()
