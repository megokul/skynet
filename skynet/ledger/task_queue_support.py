from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite


logger = logging.getLogger("skynet.ledger.task_queue")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as exc:
        logger.warning("task_queue.parse_iso_fallback value=%s error=%s", str(value)[:120], str(exc)[:220])
        return None


def load_json_list(value: Any, *, context: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        data = json.loads(str(value))
    except Exception as exc:
        logger.warning("task_queue.json_list_fallback context=%s error=%s", context, str(exc)[:220])
        return []
    if isinstance(data, list):
        return data
    logger.warning("task_queue.json_list_type_mismatch context=%s type=%s", context, type(data).__name__)
    return []


def load_json_dict(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        data = json.loads(str(value))
    except Exception as exc:
        logger.warning("task_queue.json_dict_fallback context=%s error=%s", context, str(exc)[:220])
        return {}
    if isinstance(data, dict):
        return data
    logger.warning("task_queue.json_dict_type_mismatch context=%s type=%s", context, type(data).__name__)
    return {}


def uniq_nonempty(items: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in items or []:
        value = str(item).strip()
        if value and value not in out:
            out.append(value)
    return out


def normalize_status(status: str | None, *, aliases: dict[str, str]) -> str:
    value = str(status or "").strip().lower()
    return aliases.get(value, value)


def graph_has_cycle(rows: list[dict[str, Any]], *, dependency_loader: Callable[[Any], list[Any]]) -> bool:
    graph: dict[str, list[str]] = {}
    for row in rows:
        graph[str(row["id"])] = [str(item) for item in dependency_loader(row.get("dependencies"))]

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                return True
            if dfs(nxt):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    for node in list(graph.keys()):
        if dfs(node):
            return True
    return False


async def append_task_event(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    worker_id: str | None,
    claim_token: str | None,
    message: str,
    payload: dict[str, Any] | None,
    now_iso: Callable[[], str],
) -> None:
    await db.execute(
        """
        INSERT INTO control_task_events (
            task_id, event_type, from_status, to_status,
            worker_id, claim_token, message, payload, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            event_type,
            from_status,
            to_status,
            worker_id,
            claim_token,
            message[:2000],
            json.dumps(payload or {}),
            now_iso(),
        ),
    )


def row_to_task(row: dict[str, Any], *, status_normalizer: Callable[[str | None], str]) -> dict[str, Any]:
    out = dict(row)
    out["status"] = status_normalizer(out.get("status"))
    out["params"] = load_json_dict(out.get("params"), context="control_tasks.params")
    out["dependencies"] = load_json_list(out.get("dependencies"), context="control_tasks.dependencies")
    out["dependents"] = load_json_list(out.get("dependents"), context="control_tasks.dependents")
    out["required_files"] = load_json_list(out.get("required_files"), context="control_tasks.required_files")
    out["result"] = load_json_dict(out.get("result"), context="control_tasks.result")
    return out
