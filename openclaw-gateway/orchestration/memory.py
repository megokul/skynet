from __future__ import annotations

from typing import Any

import aiosqlite

from db.store import get_project_memory, list_project_memory, upsert_project_memory

TIER_REPO_FACTS = "repo_facts"
TIER_DECISIONS = "decisions"
TIER_TASK_STATE = "task_state"
TIER_RETRIEVAL_CACHE = "retrieval_cache"


class LoopMemory:
    def __init__(self, db: aiosqlite.Connection, project_id: str) -> None:
        self._db = db
        self._project_id = project_id

    async def put(
        self,
        *,
        tier: str,
        key: str,
        value: Any,
        source_node_id: int | None = None,
    ) -> None:
        await upsert_project_memory(
            self._db,
            project_id=self._project_id,
            tier=tier,
            memory_key=key,
            memory_value=value,
            source_node_id=source_node_id,
        )

    async def get(self, *, tier: str, key: str) -> dict[str, Any] | None:
        return await get_project_memory(
            self._db,
            project_id=self._project_id,
            tier=tier,
            memory_key=key,
        )

    async def list(self, *, tier: str | None = None) -> list[dict[str, Any]]:
        return await list_project_memory(
            self._db,
            project_id=self._project_id,
            tier=tier,
        )

    async def put_retrieval_summary(
        self,
        *,
        path: str,
        summary: str,
        source_node_id: int | None = None,
    ) -> None:
        await self.put(
            tier=TIER_RETRIEVAL_CACHE,
            key=path.strip(),
            value={"summary": (summary or "").strip()},
            source_node_id=source_node_id,
        )
