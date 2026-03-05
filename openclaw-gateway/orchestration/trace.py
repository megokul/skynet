from __future__ import annotations

from typing import Any

import aiosqlite

from db.store import create_task_node_event, list_task_node_events


async def emit_trace_event(
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
    return await create_task_node_event(
        db,
        graph_id=graph_id,
        node_id=node_id,
        node_key=node_key,
        event_type=event_type,
        status=status,
        agent=agent,
        stage=stage,
        failure_type=failure_type,
        details=details or {},
    )


async def load_trace_timeline(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    limit: int = 30,
    after_id: int | None = None,
) -> list[dict[str, Any]]:
    return await list_task_node_events(
        db,
        graph_id=graph_id,
        limit=limit,
        after_id=after_id,
    )


def format_timeline_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        node = str(row.get("node_key") or "").strip() or "-"
        event = str(row.get("event_type") or "").strip() or "event"
        status = str(row.get("status") or "").strip()
        failure = str(row.get("failure_type") or "").strip()
        line = f"[{row.get('id')}] {node} {event}"
        if status:
            line += f" ({status})"
        if failure:
            line += f" [{failure}]"
        lines.append(line)
    return lines
