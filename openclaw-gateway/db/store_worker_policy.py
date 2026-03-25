from __future__ import annotations

from typing import Any

import aiosqlite

from db.store_support import dump_json, load_json_dict, load_json_list, now_iso, row_to_dict


def decode_worker_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    endpoint = load_json_dict(
        out.get("endpoint_json"),
        context="worker_registry.endpoint",
    )
    capabilities = load_json_list(
        out.get("capabilities_json"),
        context="worker_registry.capabilities",
    )
    out["endpoint"] = endpoint
    out["capabilities"] = [str(item).strip() for item in capabilities if str(item).strip()]
    return out


async def upsert_worker_registry(
    db: aiosqlite.Connection,
    *,
    worker_id: str,
    label: str,
    transport: str = "ssh",
    endpoint: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    status: str = "active",
    priority: int = 100,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO worker_registry (
            id, label, transport, endpoint_json, capabilities_json, status, priority, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            label = excluded.label,
            transport = excluded.transport,
            endpoint_json = excluded.endpoint_json,
            capabilities_json = excluded.capabilities_json,
            status = excluded.status,
            priority = excluded.priority,
            last_seen_at = excluded.last_seen_at
        """,
        (
            worker_id.strip(),
            label.strip() or worker_id.strip(),
            transport.strip() or "ssh",
            dump_json(endpoint or {}, "{}", context="worker_registry.endpoint_write"),
            dump_json(capabilities or [], "[]", context="worker_registry.capabilities_write"),
            status.strip() or "active",
            int(priority),
            now_iso(),
        ),
    )
    await db.commit()
    async with db.execute("SELECT * FROM worker_registry WHERE id = ?", (worker_id.strip(),)) as cur:
        row = row_to_dict(await cur.fetchone())
    return decode_worker_row(row) or {}


async def list_active_workers(
    db: aiosqlite.Connection,
) -> list[dict[str, Any]]:
    async with db.execute(
        """
        SELECT * FROM worker_registry
        WHERE status = 'active'
        ORDER BY priority DESC, id ASC
        """
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    out: list[dict[str, Any]] = []
    for row in rows:
        decoded = decode_worker_row(row)
        if decoded:
            out.append(decoded)
    return out


async def upsert_prompt_policy(
    db: aiosqlite.Connection,
    *,
    scope: str,
    project_id: str,
    policy_kind: str,
    policy: dict[str, Any],
    source: str = "learning",
    active: bool = True,
) -> dict[str, Any]:
    scope_value = scope.strip() or "project"
    project_value = project_id.strip() if scope_value == "project" else ""
    kind_value = policy_kind.strip()
    active_value = 1 if active else 0

    if active_value == 1:
        await db.execute(
            """
            UPDATE prompt_policies
            SET active = 0
            WHERE scope = ? AND project_id = ? AND policy_kind = ? AND active = 1
            """,
            (scope_value, project_value, kind_value),
        )

    async with db.execute(
        """
        INSERT INTO prompt_policies (
            scope, project_id, policy_kind, policy_json, source, active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            scope_value,
            project_value,
            kind_value,
            dump_json(policy or {}, "{}", context="prompt_policy.write"),
            source.strip() or "learning",
            active_value,
        ),
    ) as cur:
        policy_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM prompt_policies WHERE id = ?", (policy_id,)) as cur:
        row = row_to_dict(await cur.fetchone())
    if row:
        row["policy"] = load_json_dict(
            row.get("policy_json"),
            context="prompt_policy.read",
        )
    return row or {}


async def get_active_prompt_policy(
    db: aiosqlite.Connection,
    *,
    scope: str,
    project_id: str,
    policy_kind: str,
) -> dict[str, Any] | None:
    scope_value = scope.strip() or "project"
    project_value = project_id.strip() if scope_value == "project" else ""
    async with db.execute(
        """
        SELECT * FROM prompt_policies
        WHERE scope = ? AND project_id = ? AND policy_kind = ? AND active = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (scope_value, project_value, policy_kind.strip()),
    ) as cur:
        row = row_to_dict(await cur.fetchone())
    if not row:
        return None
    row["policy"] = load_json_dict(
        row.get("policy_json"),
        context="prompt_policy.active",
    )
    return row
