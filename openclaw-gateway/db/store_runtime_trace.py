from __future__ import annotations

from typing import Any

import aiosqlite

from db.store_support import dump_json, load_json_dict, now_iso, row_to_dict


async def create_task_node_event(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    node_id: int | None,
    node_key: str,
    event_type: str,
    status: str = "",
    agent: str = "",
    stage: str = "",
    failure_type: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with db.execute(
        """
        INSERT INTO task_node_events
            (graph_id, node_id, node_key, event_type, status, agent, stage, failure_type, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(graph_id),
            int(node_id) if isinstance(node_id, int) else None,
            node_key.strip(),
            event_type.strip(),
            status.strip(),
            agent.strip(),
            stage.strip(),
            failure_type.strip(),
            dump_json(details or {}, "{}" , context="task_node_event.write"),
        ),
    ) as cur:
        event_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM task_node_events WHERE id = ?", (event_id,)) as cur:
        row = row_to_dict(await cur.fetchone())
    if row:
        row["details"] = load_json_dict(
            row.get("details_json"),
            context="task_node_event.read",
        )
    return row or {}


async def list_task_node_events(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    limit: int = 30,
    after_id: int | None = None,
) -> list[dict[str, Any]]:
    query = (
        "SELECT * FROM task_node_events WHERE graph_id = ? "
        + ("AND id > ? " if isinstance(after_id, int) else "")
        + "ORDER BY id DESC LIMIT ?"
    )
    params: tuple[Any, ...]
    if isinstance(after_id, int):
        params = (int(graph_id), int(after_id), max(1, int(limit)))
    else:
        params = (int(graph_id), max(1, int(limit)))
    async with db.execute(query, params) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        row["details"] = load_json_dict(
            row.get("details_json"),
            context="task_node_event.list",
        )
    return rows


async def create_runtime_trace_event(
    db: aiosqlite.Connection,
    *,
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(event_payload or {})
    ts = str(payload.get("ts") or now_iso()).strip() or now_iso()
    async with db.execute(
        """
        INSERT INTO runtime_trace_events
            (
                ts, level, event, status, event_id, trace_id, root_trace_id, span_id, parent_span_id, session_key, flow,
                project_id, task_id, graph_id, node_key, node_type, phase, stage, gate,
                worker_id, transport, runtime_mode, error_type, error_code, error_message,
                telegram_chat_id, telegram_user_id, telegram_message_id, action_name,
                command_hash, working_dir, remote_pid, artifact_count, payload_json, created_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            str(payload.get("level") or "info").strip(),
            str(payload.get("event") or "").strip(),
            str(payload.get("status") or "").strip(),
            str(payload.get("event_id") or "").strip(),
            str(payload.get("trace_id") or "").strip(),
            str(payload.get("root_trace_id") or "").strip(),
            str(payload.get("span_id") or "").strip(),
            str(payload.get("parent_span_id") or "").strip(),
            str(payload.get("session_key") or "").strip(),
            str(payload.get("flow") or "").strip(),
            str(payload.get("project_id") or "").strip(),
            str(payload.get("task_id") or "").strip(),
            str(payload.get("graph_id") or "").strip(),
            str(payload.get("node_key") or "").strip(),
            str(payload.get("node_type") or "").strip(),
            str(payload.get("phase") or "").strip(),
            str(payload.get("stage") or "").strip(),
            str(payload.get("gate") or "").strip(),
            str(payload.get("worker_id") or "").strip(),
            str(payload.get("transport") or "").strip(),
            str(payload.get("runtime_mode") or "").strip(),
            str(payload.get("error_type") or "").strip(),
            str(payload.get("error_code") or "").strip(),
            str(payload.get("error_message") or "").strip(),
            str(payload.get("telegram_chat_id") or "").strip(),
            str(payload.get("telegram_user_id") or "").strip(),
            str(payload.get("telegram_message_id") or "").strip(),
            str(payload.get("action_name") or "").strip(),
            str(payload.get("command_hash") or "").strip(),
            str(payload.get("working_dir") or "").strip(),
            str(payload.get("remote_pid") or "").strip(),
            max(0, int(payload.get("artifact_count") or 0)),
            dump_json(payload, "{}", context="runtime_trace.write"),
            now_iso(),
        ),
    ) as cur:
        row_id = int(cur.lastrowid)
    await db.commit()
    async with db.execute("SELECT * FROM runtime_trace_events WHERE id = ?", (row_id,)) as cur:
        row = row_to_dict(await cur.fetchone())
    if row:
        row["payload"] = load_json_dict(
            row.get("payload_json"),
            context="runtime_trace.read",
        )
    return row or {}


async def list_runtime_trace_events(
    db: aiosqlite.Connection,
    *,
    trace_id: str | None = None,
    project_id: str | None = None,
    graph_id: str | None = None,
    session_key: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if trace_id:
        clauses.append("trace_id = ?")
        params.append(str(trace_id).strip())
    if project_id:
        clauses.append("project_id = ?")
        params.append(str(project_id).strip())
    if graph_id:
        clauses.append("graph_id = ?")
        params.append(str(graph_id).strip())
    if session_key:
        clauses.append("session_key = ?")
        params.append(str(session_key).strip())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    query = "SELECT * FROM runtime_trace_events" + where + " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    async with db.execute(query, tuple(params)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        row["payload"] = load_json_dict(
            row.get("payload_json"),
            context="runtime_trace.list",
        )
    return rows
