"""
SKYNET — Data Access Layer

Thin async functions over gateway schema tables.
Every function returns plain dicts (never aiosqlite.Row objects) so callers
don't need to know about the DB layer.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _dump_json(value: Any, default: str = "{}") -> str:
    if value is None:
        return default
    try:
        return json.dumps(value, ensure_ascii=True)
    except Exception:
        return default


# ── Users ─────────────────────────────────────────────────────────────────────

async def ensure_user(
    db: aiosqlite.Connection,
    *,
    telegram_user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> dict[str, Any]:
    """
    Upsert a user row by telegram_user_id and return the full user dict.
    Safe to call on every incoming update.
    """
    now = _now()
    await db.execute(
        """
        INSERT INTO users (telegram_user_id, username, first_name, last_name,
                           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name,
            last_name  = excluded.last_name,
            updated_at = excluded.updated_at
        """,
        (int(telegram_user_id), username, first_name, last_name, now, now),
    )
    await db.commit()
    return await get_user_by_telegram_id(db, telegram_user_id)


async def get_user_by_telegram_id(
    db: aiosqlite.Connection,
    telegram_user_id: int,
) -> dict[str, Any] | None:
    async with db.execute(
        "SELECT * FROM users WHERE telegram_user_id = ?",
        (int(telegram_user_id),),
    ) as cur:
        return _row(await cur.fetchone())


# ── Projects ──────────────────────────────────────────────────────────────────

async def create_project(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    name: str,
    project_type: str = "Other",
    description: str = "",
    coding_profile: str = "legacy",
    quality_profile: str = "legacy",
    control_loop_profile: str = "legacy",
) -> dict[str, Any]:
    """Create a new project and return its row."""
    project_id = _new_id()
    now = _now()
    _name = name.strip()
    await db.execute(
        """
        INSERT INTO projects (id, user_id, name, display_name, project_type, description,
                              coding_profile, quality_profile, control_loop_profile,
                              status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
        """,
        (project_id, int(user_id), _name, _name, project_type.strip(),
         description.strip(), coding_profile.strip() or "legacy",
         quality_profile.strip() or "legacy",
         control_loop_profile.strip() or "legacy",
         now, now),
    )
    await db.commit()
    return await get_project(db, project_id)


async def get_project(
    db: aiosqlite.Connection,
    project_id: str,
) -> dict[str, Any] | None:
    async with db.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ) as cur:
        return _row(await cur.fetchone())


async def list_projects(
    db: aiosqlite.Connection,
    *,
    user_id: int,
) -> list[dict[str, Any]]:
    """All projects for a user, newest first."""
    async with db.execute(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
        (int(user_id),),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def update_project_status(
    db: aiosqlite.Connection,
    project_id: str,
    status: str,
) -> None:
    """Advance a project through its lifecycle."""
    await db.execute(
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), project_id),
    )
    await db.commit()


# ── Tasks ─────────────────────────────────────────────────────────────────────

async def create_task(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    title: str,
    description: str = "",
) -> dict[str, Any]:
    now = _now()
    async with db.execute(
        """
        INSERT INTO tasks (project_id, title, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, title.strip(), description.strip(), now, now),
    ) as cur:
        task_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
        return _row(await cur.fetchone())


async def list_tasks(
    db: aiosqlite.Connection,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT * FROM tasks WHERE project_id = ? ORDER BY id ASC",
        (project_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def update_task_status(
    db: aiosqlite.Connection,
    task_id: int,
    *,
    status: str,
    result_summary: str = "",
    error_message: str = "",
) -> None:
    await db.execute(
        """
        UPDATE tasks
        SET status = ?, result_summary = ?, error_message = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, result_summary, error_message, _now(), task_id),
    )
    await db.commit()


# ── Provider usage ────────────────────────────────────────────────────────────

async def create_task_gate_result(
    db: aiosqlite.Connection,
    *,
    task_id: int,
    attempt: int,
    gate_name: str,
    status: str,
    command: str = "",
    summary: str = "",
) -> dict[str, Any]:
    async with db.execute(
        """
        INSERT INTO task_gate_results
            (task_id, attempt, gate_name, status, command, summary)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(task_id),
            int(attempt),
            gate_name.strip(),
            status.strip(),
            command.strip(),
            summary.strip(),
        ),
    ) as cur:
        gate_result_id = cur.lastrowid
    await db.commit()
    async with db.execute(
        "SELECT * FROM task_gate_results WHERE id = ?",
        (gate_result_id,),
    ) as cur:
        return _row(await cur.fetchone())


async def list_task_gate_results(
    db: aiosqlite.Connection,
    *,
    task_id: int,
) -> list[dict[str, Any]]:
    async with db.execute(
        """
        SELECT * FROM task_gate_results
        WHERE task_id = ?
        ORDER BY attempt ASC, id ASC
        """,
        (int(task_id),),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def delete_task_gate_results(
    db: aiosqlite.Connection,
    *,
    task_id: int,
) -> None:
    await db.execute(
        "DELETE FROM task_gate_results WHERE task_id = ?",
        (int(task_id),),
    )
    await db.commit()


async def create_task_orchestration_run(
    db: aiosqlite.Connection,
    *,
    task_id: int | None,
    phase: str,
    stage: str,
    session_id: str,
    runtime: str,
    queue_mode: str,
    status: str,
    summary: str = "",
) -> dict[str, Any]:
    async with db.execute(
        """
        INSERT INTO task_orchestration_runs
            (task_id, phase, stage, session_id, runtime, queue_mode, status, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(task_id) if isinstance(task_id, int) else None,
            phase.strip(),
            stage.strip(),
            session_id.strip(),
            runtime.strip(),
            queue_mode.strip(),
            status.strip(),
            summary.strip(),
        ),
    ) as cur:
        run_id = cur.lastrowid
    await db.commit()
    async with db.execute(
        "SELECT * FROM task_orchestration_runs WHERE id = ?",
        (run_id,),
    ) as cur:
        return _row(await cur.fetchone())


async def list_task_orchestration_runs(
    db: aiosqlite.Connection,
    *,
    task_id: int,
) -> list[dict[str, Any]]:
    async with db.execute(
        """
        SELECT * FROM task_orchestration_runs
        WHERE task_id = ?
        ORDER BY id ASC
        """,
        (int(task_id),),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ── Closed loop graph state ──────────────────────────────────────────────────

async def create_task_graph(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    goal: str,
    status: str = "active",
    planner_summary: str = "",
    max_iterations: int = 40,
    max_runtime_seconds: int = 3600,
    max_repairs: int = 1,
    max_tokens: int = 250000,
    success_contract: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    now = _now()
    async with db.execute(
        """
        INSERT INTO task_graphs (
            project_id, goal, status, planner_summary,
            iteration_count, repair_count,
            max_iterations, max_runtime_seconds, max_repairs, max_tokens, tokens_used,
            success_contract_json, started_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            project_id,
            goal.strip(),
            status.strip() or "active",
            planner_summary.strip(),
            int(max_iterations),
            int(max_runtime_seconds),
            int(max_repairs),
            int(max_tokens),
            _dump_json(success_contract or {}, "{}"),
            (started_at.strip() if isinstance(started_at, str) and started_at.strip() else now),
            now,
            now,
        ),
    ) as cur:
        graph_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM task_graphs WHERE id = ?", (graph_id,)) as cur:
        return _row(await cur.fetchone())


async def get_active_task_graph(
    db: aiosqlite.Connection,
    *,
    project_id: str,
) -> dict[str, Any] | None:
    async with db.execute(
        """
        SELECT * FROM task_graphs
        WHERE project_id = ? AND status IN ('active', 'paused')
        ORDER BY id DESC
        LIMIT 1
        """,
        (project_id,),
    ) as cur:
        return _row(await cur.fetchone())


async def update_task_graph_status(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    status: str,
    finished_at: str | None = None,
) -> None:
    now = _now()
    done_at = finished_at.strip() if isinstance(finished_at, str) and finished_at.strip() else None
    if done_at:
        await db.execute(
            "UPDATE task_graphs SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?",
            (status.strip(), done_at, now, int(graph_id)),
        )
    else:
        await db.execute(
            "UPDATE task_graphs SET status = ?, updated_at = ? WHERE id = ?",
            (status.strip(), now, int(graph_id)),
        )
    await db.commit()


async def update_task_graph_counters(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    iteration_delta: int = 0,
    repair_delta: int = 0,
    tokens_delta: int = 0,
) -> None:
    await db.execute(
        """
        UPDATE task_graphs
        SET iteration_count = MAX(0, iteration_count + ?),
            repair_count = MAX(0, repair_count + ?),
            tokens_used = MAX(0, tokens_used + ?),
            updated_at = ?
        WHERE id = ?
        """,
        (
            int(iteration_delta),
            int(repair_delta),
            int(tokens_delta),
            _now(),
            int(graph_id),
        ),
    )
    await db.commit()


async def create_task_node(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    node_key: str,
    title: str,
    node_type: str,
    owner: str = "",
    worker_id: str = "",
    deps: list[str] | None = None,
    inputs: dict[str, Any] | None = None,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    tools_required: list[str] | None = None,
    risk_level: str = "medium",
    acceptance: list[dict[str, Any]] | None = None,
    priority: int = 100,
    execution_lock: str = "repo-write",
    retry_budget: int = 0,
    attempt_count: int = 0,
    status: str = "queued",
    failure_type: str = "",
    result_summary: str = "",
    error_message: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    now = _now()
    async with db.execute(
        """
        INSERT INTO task_nodes (
            graph_id, node_key, title, node_type, owner, worker_id,
            deps_json, inputs_json, allowed_paths_json, forbidden_paths_json, tools_required_json, risk_level,
            acceptance_json, priority, execution_lock, retry_budget, attempt_count, status,
            failure_type, result_summary, error_message, started_at, finished_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(graph_id),
            node_key.strip(),
            title.strip(),
            node_type.strip(),
            owner.strip(),
            worker_id.strip(),
            _dump_json(deps or [], "[]"),
            _dump_json(inputs or {}, "{}"),
            _dump_json(allowed_paths or [], "[]"),
            _dump_json(forbidden_paths or [], "[]"),
            _dump_json(tools_required or [], "[]"),
            risk_level.strip().lower() or "medium",
            _dump_json(acceptance or [], "[]"),
            int(priority),
            execution_lock.strip() or "repo-write",
            int(retry_budget),
            int(attempt_count),
            status.strip() or "queued",
            failure_type.strip(),
            result_summary.strip(),
            error_message.strip(),
            (started_at.strip() if isinstance(started_at, str) and started_at.strip() else None),
            (finished_at.strip() if isinstance(finished_at, str) and finished_at.strip() else None),
            now,
            now,
        ),
    ) as cur:
        node_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM task_nodes WHERE id = ?", (node_id,)) as cur:
        return _row(await cur.fetchone())


async def list_graph_nodes(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT * FROM task_nodes WHERE graph_id = ? ORDER BY id ASC",
        (int(graph_id),),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def get_task_node_by_key(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    node_key: str,
) -> dict[str, Any] | None:
    async with db.execute(
        """
        SELECT * FROM task_nodes
        WHERE graph_id = ? AND node_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(graph_id), node_key.strip()),
    ) as cur:
        return _row(await cur.fetchone())


async def update_task_node_status(
    db: aiosqlite.Connection,
    *,
    node_id: int,
    status: str,
    result_summary: str = "",
    error_message: str = "",
    failure_type: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    now = _now()
    start_value = started_at.strip() if isinstance(started_at, str) and started_at.strip() else None
    done_value = finished_at.strip() if isinstance(finished_at, str) and finished_at.strip() else None
    await db.execute(
        """
        UPDATE task_nodes
        SET status = ?, result_summary = ?, error_message = ?, failure_type = ?,
            started_at = COALESCE(?, started_at),
            finished_at = COALESCE(?, finished_at),
            updated_at = ?
        WHERE id = ?
        """,
        (
            status.strip(),
            result_summary.strip(),
            error_message.strip(),
            failure_type.strip(),
            start_value,
            done_value,
            now,
            int(node_id),
        ),
    )
    await db.commit()


async def increment_task_node_attempt(
    db: aiosqlite.Connection,
    *,
    node_id: int,
) -> None:
    await db.execute(
        """
        UPDATE task_nodes
        SET attempt_count = attempt_count + 1, updated_at = ?
        WHERE id = ?
        """,
        (_now(), int(node_id)),
    )
    await db.commit()


async def update_task_node_deps(
    db: aiosqlite.Connection,
    *,
    node_id: int,
    deps: list[str],
) -> None:
    await db.execute(
        """
        UPDATE task_nodes
        SET deps_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (_dump_json(deps or [], "[]"), _now(), int(node_id)),
    )
    await db.commit()


async def create_critic_finding(
    db: aiosqlite.Connection,
    *,
    node_id: int,
    critic_name: str,
    severity: str,
    code: str = "",
    message: str,
    files: list[str] | None = None,
    suggested_fix: str = "",
) -> dict[str, Any]:
    async with db.execute(
        """
        INSERT INTO critic_findings
            (node_id, critic_name, severity, code, message, files_json, suggested_fix)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(node_id),
            critic_name.strip(),
            severity.strip(),
            code.strip(),
            message.strip(),
            _dump_json(files or [], "[]"),
            suggested_fix.strip(),
        ),
    ) as cur:
        finding_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM critic_findings WHERE id = ?", (finding_id,)) as cur:
        return _row(await cur.fetchone())


async def list_critic_findings(
    db: aiosqlite.Connection,
    *,
    node_id: int,
) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT * FROM critic_findings WHERE node_id = ? ORDER BY id ASC",
        (int(node_id),),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


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
            _dump_json(memory_value),
            int(source_node_id) if isinstance(source_node_id, int) else None,
            _now(),
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
        row = _row(await cur.fetchone())
    if not row:
        return None
    try:
        row["memory_value"] = json.loads(str(row.get("memory_value_json") or "{}"))
    except Exception:
        row["memory_value"] = {}
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
        try:
            row["memory_value"] = json.loads(str(row.get("memory_value_json") or "{}"))
        except Exception:
            row["memory_value"] = {}
    return rows


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
            _dump_json(details or {}, "{}"),
        ),
    ) as cur:
        event_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM task_node_events WHERE id = ?", (event_id,)) as cur:
        row = _row(await cur.fetchone())
    if row:
        try:
            row["details"] = json.loads(str(row.get("details_json") or "{}"))
        except Exception:
            row["details"] = {}
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
        try:
            row["details"] = json.loads(str(row.get("details_json") or "{}"))
        except Exception:
            row["details"] = {}
    return rows


async def create_runtime_trace_event(
    db: aiosqlite.Connection,
    *,
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(event_payload or {})
    ts = str(payload.get("ts") or _now()).strip() or _now()
    async with db.execute(
        """
        INSERT INTO runtime_trace_events
            (
                ts, level, event, status, trace_id, span_id, parent_span_id, flow,
                project_id, task_id, graph_id, node_key, node_type, phase, stage, gate,
                worker_id, transport, runtime_mode, error_type, error_code, error_message,
                telegram_chat_id, telegram_user_id, telegram_message_id, action_name,
                command_hash, working_dir, payload_json, created_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            str(payload.get("level") or "info").strip(),
            str(payload.get("event") or "").strip(),
            str(payload.get("status") or "").strip(),
            str(payload.get("trace_id") or "").strip(),
            str(payload.get("span_id") or "").strip(),
            str(payload.get("parent_span_id") or "").strip(),
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
            _dump_json(payload, "{}"),
            _now(),
        ),
    ) as cur:
        row_id = int(cur.lastrowid)
    await db.commit()
    async with db.execute("SELECT * FROM runtime_trace_events WHERE id = ?", (row_id,)) as cur:
        row = _row(await cur.fetchone())
    if row:
        try:
            row["payload"] = json.loads(str(row.get("payload_json") or "{}"))
        except Exception:
            row["payload"] = {}
    return row or {}


async def list_runtime_trace_events(
    db: aiosqlite.Connection,
    *,
    trace_id: str | None = None,
    project_id: str | None = None,
    graph_id: str | None = None,
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
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    query = (
        "SELECT * FROM runtime_trace_events"
        + where
        + " ORDER BY id DESC LIMIT ?"
    )
    params.append(max(1, int(limit)))
    async with db.execute(query, tuple(params)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        try:
            row["payload"] = json.loads(str(row.get("payload_json") or "{}"))
        except Exception:
            row["payload"] = {}
    return rows


async def upsert_code_index_file(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    path: str,
    language: str,
    sha1: str,
    size_bytes: int,
) -> None:
    await db.execute(
        """
        INSERT INTO code_index_files (project_id, path, language, sha1, size_bytes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, path) DO UPDATE SET
            language = excluded.language,
            sha1 = excluded.sha1,
            size_bytes = excluded.size_bytes,
            updated_at = excluded.updated_at
        """,
        (
            project_id,
            path.strip(),
            language.strip(),
            sha1.strip(),
            max(0, int(size_bytes)),
            _now(),
        ),
    )
    await db.commit()


async def replace_code_index_symbols(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    path: str,
    symbols: list[dict[str, Any]],
) -> None:
    clean_path = path.strip()
    await db.execute(
        "DELETE FROM code_index_symbols WHERE project_id = ? AND path = ?",
        (project_id, clean_path),
    )
    for sym in symbols:
        await db.execute(
            """
            INSERT INTO code_index_symbols (project_id, path, symbol, symbol_kind, line_no, signature)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                clean_path,
                str(sym.get("symbol") or "").strip(),
                str(sym.get("symbol_kind") or "").strip(),
                int(sym.get("line_no") or 0),
                str(sym.get("signature") or "").strip(),
            ),
        )
    await db.commit()


async def replace_code_index_refs(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    from_path: str,
    refs: list[dict[str, Any]],
) -> None:
    clean_from = from_path.strip()
    await db.execute(
        "DELETE FROM code_index_refs WHERE project_id = ? AND from_path = ?",
        (project_id, clean_from),
    )
    for ref in refs:
        await db.execute(
            """
            INSERT INTO code_index_refs (project_id, from_path, to_module, ref_kind)
            VALUES (?, ?, ?, ?)
            """,
            (
                project_id,
                clean_from,
                str(ref.get("to_module") or "").strip(),
                str(ref.get("ref_kind") or "import").strip(),
            ),
        )
    await db.commit()


async def query_code_index(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    terms: list[str],
    top_k: int = 12,
) -> list[dict[str, Any]]:
    clean_terms = [str(t).strip().lower() for t in terms if str(t).strip()]
    if not clean_terms:
        return []
    placeholders = " OR ".join(["LOWER(symbol) LIKE ?"] * len(clean_terms))
    like_params = tuple(f"%{term}%" for term in clean_terms)
    async with db.execute(
        f"""
        SELECT path, symbol, symbol_kind, line_no, signature
        FROM code_index_symbols
        WHERE project_id = ? AND ({placeholders})
        ORDER BY line_no ASC
        LIMIT ?
        """,
        (project_id, *like_params, max(1, int(top_k))),
    ) as cur:
        symbol_rows = [dict(r) for r in await cur.fetchall()]

    if symbol_rows:
        return symbol_rows

    file_placeholders = " OR ".join(["LOWER(path) LIKE ?"] * len(clean_terms))
    file_params = tuple(f"%{term}%" for term in clean_terms)
    async with db.execute(
        f"""
        SELECT path, language, sha1, size_bytes
        FROM code_index_files
        WHERE project_id = ? AND ({file_placeholders})
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (project_id, *file_params, max(1, int(top_k))),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def update_task_node_worker(
    db: aiosqlite.Connection,
    *,
    node_id: int,
    worker_id: str,
) -> None:
    await db.execute(
        """
        UPDATE task_nodes
        SET worker_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (worker_id.strip(), _now(), int(node_id)),
    )
    await db.commit()


async def create_architecture_state(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    version: int,
    status: str = "active",
    components: list[dict[str, Any]] | None = None,
    interfaces: list[dict[str, Any]] | None = None,
    boundaries: list[dict[str, Any]] | None = None,
    data_flows: list[dict[str, Any]] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    adr_summary: str = "",
    created_by: str = "architect",
) -> dict[str, Any]:
    async with db.execute(
        """
        INSERT INTO architecture_states (
            project_id, version, status, components_json, interfaces_json,
            boundaries_json, data_flows_json, constraints_json, adr_summary, created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            int(version),
            status.strip() or "active",
            _dump_json(components or [], "[]"),
            _dump_json(interfaces or [], "[]"),
            _dump_json(boundaries or [], "[]"),
            _dump_json(data_flows or [], "[]"),
            _dump_json(constraints or [], "[]"),
            adr_summary.strip(),
            created_by.strip() or "architect",
        ),
    ) as cur:
        state_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM architecture_states WHERE id = ?", (state_id,)) as cur:
        row = _row(await cur.fetchone())
    return _decode_architecture_state(row)


def _decode_architecture_state(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    for json_field, key in [
        ("components_json", "components"),
        ("interfaces_json", "interfaces"),
        ("boundaries_json", "boundaries"),
        ("data_flows_json", "data_flows"),
        ("constraints_json", "constraints"),
    ]:
        try:
            parsed = json.loads(str(out.get(json_field) or "[]"))
        except Exception:
            parsed = []
        out[key] = parsed if isinstance(parsed, list) else []
    return out


async def get_active_architecture_state(
    db: aiosqlite.Connection,
    *,
    project_id: str,
) -> dict[str, Any] | None:
    async with db.execute(
        """
        SELECT * FROM architecture_states
        WHERE project_id = ? AND status = 'active'
        ORDER BY version DESC
        LIMIT 1
        """,
        (project_id,),
    ) as cur:
        row = _row(await cur.fetchone())
    return _decode_architecture_state(row)


async def supersede_architecture_state(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    previous_version: int,
) -> None:
    await db.execute(
        """
        UPDATE architecture_states
        SET status = 'superseded'
        WHERE project_id = ? AND version = ? AND status = 'active'
        """,
        (project_id, int(previous_version)),
    )
    await db.commit()


async def create_task_strategy(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    parallel_lanes: list[dict[str, Any]] | None = None,
    risk_assessment: list[dict[str, Any]] | None = None,
    execution_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with db.execute(
        """
        INSERT INTO task_strategy (
            graph_id, parallel_lanes_json, risk_assessment_json, execution_strategy_json
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            int(graph_id),
            _dump_json(parallel_lanes or [], "[]"),
            _dump_json(risk_assessment or [], "[]"),
            _dump_json(execution_strategy or {}, "{}"),
        ),
    ) as cur:
        strategy_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM task_strategy WHERE id = ?", (strategy_id,)) as cur:
        row = _row(await cur.fetchone())
    return _decode_task_strategy(row)


def _decode_task_strategy(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    for json_field, key, default in [
        ("parallel_lanes_json", "parallel_lanes", []),
        ("risk_assessment_json", "risk_assessment", []),
        ("execution_strategy_json", "execution_strategy", {}),
    ]:
        try:
            parsed = json.loads(str(out.get(json_field) or _dump_json(default)))
        except Exception:
            parsed = default
        out[key] = parsed if isinstance(parsed, type(default)) else default
    return out


async def get_task_strategy(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
) -> dict[str, Any] | None:
    async with db.execute(
        """
        SELECT * FROM task_strategy
        WHERE graph_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(graph_id),),
    ) as cur:
        row = _row(await cur.fetchone())
    return _decode_task_strategy(row)


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
            _dump_json(endpoint or {}, "{}"),
            _dump_json(capabilities or [], "[]"),
            status.strip() or "active",
            int(priority),
            _now(),
        ),
    )
    await db.commit()
    async with db.execute("SELECT * FROM worker_registry WHERE id = ?", (worker_id.strip(),)) as cur:
        row = _row(await cur.fetchone())
    return _decode_worker_row(row) or {}


def _decode_worker_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    try:
        endpoint = json.loads(str(out.get("endpoint_json") or "{}"))
    except Exception:
        endpoint = {}
    try:
        capabilities = json.loads(str(out.get("capabilities_json") or "[]"))
    except Exception:
        capabilities = []
    out["endpoint"] = endpoint if isinstance(endpoint, dict) else {}
    out["capabilities"] = [str(item).strip() for item in capabilities if str(item).strip()]
    return out


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
        decoded = _decode_worker_row(row)
        if decoded:
            out.append(decoded)
    return out


async def create_node_worker_assignment(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    node_id: int,
    worker_id: str,
    assignment_reason: str = "",
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO node_worker_assignments (graph_id, node_id, worker_id, assignment_reason)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(graph_id, node_id) DO UPDATE SET
            worker_id = excluded.worker_id,
            assignment_reason = excluded.assignment_reason
        """,
        (
            int(graph_id),
            int(node_id),
            worker_id.strip(),
            assignment_reason.strip(),
        ),
    )
    await db.commit()
    async with db.execute(
        """
        SELECT * FROM node_worker_assignments
        WHERE graph_id = ? AND node_id = ?
        LIMIT 1
        """,
        (int(graph_id), int(node_id)),
    ) as cur:
        return _row(await cur.fetchone()) or {}


async def get_node_worker_assignment(
    db: aiosqlite.Connection,
    *,
    graph_id: int,
    node_id: int,
) -> dict[str, Any] | None:
    async with db.execute(
        """
        SELECT * FROM node_worker_assignments
        WHERE graph_id = ? AND node_id = ?
        LIMIT 1
        """,
        (int(graph_id), int(node_id)),
    ) as cur:
        return _row(await cur.fetchone())


async def create_learning_event(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    graph_id: int | None,
    node_id: int | None,
    failure_type: str,
    critic_code: str = "",
    pattern_key: str,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with db.execute(
        """
        INSERT INTO learning_events (
            project_id, graph_id, node_id, failure_type, critic_code, pattern_key, event_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            int(graph_id) if isinstance(graph_id, int) else None,
            int(node_id) if isinstance(node_id, int) else None,
            failure_type.strip(),
            critic_code.strip(),
            pattern_key.strip(),
            _dump_json(event or {}, "{}"),
        ),
    ) as cur:
        learning_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM learning_events WHERE id = ?", (learning_id,)) as cur:
        row = _row(await cur.fetchone())
    if row:
        try:
            row["event"] = json.loads(str(row.get("event_json") or "{}"))
        except Exception:
            row["event"] = {}
    return row or {}


async def list_learning_events(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    pattern_key: str | None = None,
    failure_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["project_id = ?"]
    params: list[Any] = [project_id]
    if pattern_key:
        clauses.append("pattern_key = ?")
        params.append(pattern_key.strip())
    if failure_type:
        clauses.append("failure_type = ?")
        params.append(failure_type.strip())
    where = " AND ".join(clauses)
    query = (
        f"SELECT * FROM learning_events WHERE {where} "
        "ORDER BY id DESC LIMIT ?"
    )
    params.append(max(1, int(limit)))
    async with db.execute(query, tuple(params)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        try:
            row["event"] = json.loads(str(row.get("event_json") or "{}"))
        except Exception:
            row["event"] = {}
    return rows


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
            _dump_json(policy or {}, "{}"),
            source.strip() or "learning",
            active_value,
        ),
    ) as cur:
        policy_id = cur.lastrowid
    await db.commit()
    async with db.execute("SELECT * FROM prompt_policies WHERE id = ?", (policy_id,)) as cur:
        row = _row(await cur.fetchone())
    if row:
        try:
            row["policy"] = json.loads(str(row.get("policy_json") or "{}"))
        except Exception:
            row["policy"] = {}
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
        row = _row(await cur.fetchone())
    if not row:
        return None
    try:
        row["policy"] = json.loads(str(row.get("policy_json") or "{}"))
    except Exception:
        row["policy"] = {}
    return row


async def record_provider_usage(
    db: aiosqlite.Connection,
    *,
    provider_name: str,
    requests: int = 1,
    tokens: int = 0,
    error: bool = False,
) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = _now()
    await db.execute(
        """
        INSERT INTO provider_usage
            (provider_name, date, requests_used, tokens_used, errors, last_request_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider_name, date) DO UPDATE SET
            requests_used   = requests_used   + excluded.requests_used,
            tokens_used     = tokens_used     + excluded.tokens_used,
            errors          = errors          + excluded.errors,
            last_request_at = excluded.last_request_at
        """,
        (provider_name, today, requests, tokens, int(error), now),
    )
    await db.commit()


async def get_provider_usage(
    db: aiosqlite.Connection,
    *,
    provider_name: str,
    date: str | None = None,
) -> dict[str, Any] | None:
    target_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with db.execute(
        "SELECT * FROM provider_usage WHERE provider_name = ? AND date = ?",
        (provider_name, target_date),
    ) as cur:
        return _row(await cur.fetchone())
