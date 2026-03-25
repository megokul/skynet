from __future__ import annotations

from typing import Any

import aiosqlite

from db.store_support import dump_json, load_json_dict, now_iso, row_to_dict


async def upsert_project_memory(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    tier: str,
    memory_key: str,
    memory_value: Any,
    source_node_id: int | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO project_memory (project_id, tier, memory_key, memory_value_json, source_node_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, tier, memory_key) DO UPDATE SET
            memory_value_json = excluded.memory_value_json,
            source_node_id = excluded.source_node_id,
            updated_at = excluded.updated_at
        """,
        (
            project_id,
            tier.strip(),
            memory_key.strip(),
            dump_json(memory_value, context="project_memory.write"),
            int(source_node_id) if isinstance(source_node_id, int) else None,
            now_iso(),
        ),
    )
    await db.commit()


async def get_project_memory(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    tier: str,
    memory_key: str,
) -> dict[str, Any] | None:
    async with db.execute(
        """
        SELECT * FROM project_memory
        WHERE project_id = ? AND tier = ? AND memory_key = ?
        LIMIT 1
        """,
        (project_id, tier.strip(), memory_key.strip()),
    ) as cur:
        row = row_to_dict(await cur.fetchone())
    if not row:
        return None
    row["memory_value"] = load_json_dict(
        row.get("memory_value_json"),
        context="project_memory.read",
        warn=False,
    )
    return row


async def list_project_memory(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    tier: str | None = None,
) -> list[dict[str, Any]]:
    if tier:
        query = (
            "SELECT * FROM project_memory WHERE project_id = ? AND tier = ? "
            "ORDER BY updated_at DESC, id DESC"
        )
        params: tuple[Any, ...] = (project_id, tier.strip())
    else:
        query = (
            "SELECT * FROM project_memory WHERE project_id = ? "
            "ORDER BY updated_at DESC, id DESC"
        )
        params = (project_id,)
    async with db.execute(query, params) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["memory_value"] = load_json_dict(
            row.get("memory_value_json"),
            context="project_memory.list",
            warn=False,
        )
    return rows
