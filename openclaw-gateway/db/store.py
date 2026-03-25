"""
SKYNET — Data Access Layer

Thin async functions over gateway schema tables.
Every function returns plain dicts (never aiosqlite.Row objects) so callers
don't need to know about the DB layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from db.store_memory import (
    get_project_memory,
    list_project_memory,
    upsert_project_memory,
)
from db.store_runtime_trace import (
    create_runtime_trace_event,
    create_task_node_event,
    list_runtime_trace_events,
    list_task_node_events,
)
from db.store_support import (
    dump_json as _dump_json,
    load_json_dict,
    load_json_list,
    new_id as _new_id,
    now_iso as _now,
    row_to_dict as _row,
)
from db.store_worker_policy import (
    decode_worker_row as _decode_worker_row,
    get_active_prompt_policy,
    list_active_workers,
    upsert_prompt_policy,
    upsert_worker_registry,
)


# ── Helpers ───────────────────────────────────────────────────────────────────



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


async def delete_critic_findings_for_node(
    db: aiosqlite.Connection,
    *,
    node_id: int,
) -> int:
    async with db.execute(
        "DELETE FROM critic_findings WHERE node_id = ?",
        (int(node_id),),
    ) as cur:
        count = cur.rowcount
    await db.commit()
    return count


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
        out[key] = load_json_list(
            out.get(json_field),
            context=f"architecture_state.{json_field}",
        )
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
        if isinstance(default, list):
            out[key] = load_json_list(
                out.get(json_field),
                context=f"task_strategy.{json_field}",
            )
        else:
            out[key] = load_json_dict(
                out.get(json_field),
                context=f"task_strategy.{json_field}",
            )
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
        row["event"] = load_json_dict(
            row.get("event_json"),
            context="learning_event.read",
        )
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
        row["event"] = load_json_dict(
            row.get("event_json"),
            context="learning_event.list",
        )
    return rows


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
