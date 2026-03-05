from __future__ import annotations

import pytest

from db.schema import init_db
from orchestration.memory import LoopMemory, TIER_DECISIONS, TIER_REPO_FACTS


@pytest.mark.asyncio
async def test_memory_upsert_and_get_by_tier():
    db = await init_db(":memory:")
    memory = LoopMemory(db, "proj_1")

    await memory.put(tier=TIER_REPO_FACTS, key="runtime", value={"python": "3.12"})
    await memory.put(tier=TIER_DECISIONS, key="auth", value={"mode": "jwt"})

    runtime = await memory.get(tier=TIER_REPO_FACTS, key="runtime")
    assert runtime is not None
    assert runtime["memory_value"]["python"] == "3.12"

    decisions = await memory.list(tier=TIER_DECISIONS)
    assert len(decisions) == 1
    assert decisions[0]["memory_value"]["mode"] == "jwt"

    await db.close()

