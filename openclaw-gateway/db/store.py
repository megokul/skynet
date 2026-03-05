"""
SKYNET — Data Access Layer

Thin async functions over the 4 schema tables.
Every function returns plain dicts (never aiosqlite.Row objects) so callers
don't need to know about the DB layer.
"""
from __future__ import annotations

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
) -> dict[str, Any]:
    """Create a new project and return its row."""
    project_id = _new_id()
    now = _now()
    _name = name.strip()
    await db.execute(
        """
        INSERT INTO projects (id, user_id, name, display_name, project_type, description,
                              coding_profile, quality_profile, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
        """,
        (project_id, int(user_id), _name, _name, project_type.strip(),
         description.strip(), coding_profile.strip() or "legacy",
         quality_profile.strip() or "legacy", now, now),
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
