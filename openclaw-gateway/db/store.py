"""
SKYNET — Data Access Layer

Async CRUD operations backed by SQLite.  Every public method takes
an ``aiosqlite.Connection`` and returns plain dicts / lists.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------
# Projects
# ------------------------------------------------------------------

async def create_project(
    db: aiosqlite.Connection,
    name: str,
    display_name: str,
    local_path: str,
) -> dict[str, Any]:
    project_id = _uuid()
    await db.execute(
        "INSERT INTO projects (id, name, display_name, local_path) VALUES (?, ?, ?, ?)",
        (project_id, name, display_name, local_path),
    )
    await db.commit()
    return await get_project(db, project_id)


async def get_project(db: aiosqlite.Connection, project_id: str) -> dict[str, Any] | None:
    async with db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_project_by_name(db: aiosqlite.Connection, name: str) -> dict[str, Any] | None:
    async with db.execute("SELECT * FROM projects WHERE name = ?", (name,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_projects(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    async with db.execute("SELECT * FROM projects ORDER BY created_at DESC") as cur:
        return [dict(row) for row in await cur.fetchall()]


async def update_project(
    db: aiosqlite.Connection,
    project_id: str,
    **fields: Any,
) -> None:
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [project_id]
    await db.execute(f"UPDATE projects SET {sets} WHERE id = ?", vals)
    await db.commit()


async def get_projects_by_status(
    db: aiosqlite.Connection,
    status: str,
) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC",
        (status,),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


# ------------------------------------------------------------------
# Ideas
# ------------------------------------------------------------------

async def add_idea(
    db: aiosqlite.Connection,
    project_id: str,
    message_text: str,
) -> int:
    async with db.execute(
        "INSERT INTO ideas (project_id, message_text) VALUES (?, ?)",
        (project_id, message_text),
    ) as cur:
        idea_id = cur.lastrowid
    await db.commit()
    return idea_id


async def get_ideas(db: aiosqlite.Connection, project_id: str) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT * FROM ideas WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


# ------------------------------------------------------------------
# Plans
# ------------------------------------------------------------------

async def create_plan(
    db: aiosqlite.Connection,
    project_id: str,
    summary: str,
    timeline: list[dict],
    milestones: list[dict],
) -> int:
    # Deactivate any previous active plans.
    await db.execute(
        "UPDATE plans SET is_active = 0 WHERE project_id = ? AND is_active = 1",
        (project_id,),
    )
    async with db.execute(
        "INSERT INTO plans (project_id, summary, timeline, milestones) VALUES (?, ?, ?, ?)",
        (project_id, summary, json.dumps(timeline), json.dumps(milestones)),
    ) as cur:
        plan_id = cur.lastrowid
    await db.commit()
    return plan_id


async def get_active_plan(db: aiosqlite.Connection, project_id: str) -> dict[str, Any] | None:
    async with db.execute(
        "SELECT * FROM plans WHERE project_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
        if not row:
            return None
        plan = dict(row)
        plan["timeline"] = json.loads(plan["timeline"])
        plan["milestones"] = json.loads(plan["milestones"])
        return plan


# ------------------------------------------------------------------
# Tasks
# ------------------------------------------------------------------

async def create_tasks(
    db: aiosqlite.Connection,
    project_id: str,
    plan_id: int,
    tasks: list[dict[str, str]],
) -> list[int]:
    ids = []
    for i, task in enumerate(tasks):
        async with db.execute(
            "INSERT INTO tasks (project_id, plan_id, milestone, title, description, order_index) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, plan_id, task.get("milestone", ""),
             task["title"], task.get("description", ""), i),
        ) as cur:
            ids.append(cur.lastrowid)
    await db.commit()
    return ids


async def get_tasks(
    db: aiosqlite.Connection,
    project_id: str,
    plan_id: int | None = None,
) -> list[dict[str, Any]]:
    if plan_id:
        sql = "SELECT * FROM tasks WHERE project_id = ? AND plan_id = ? ORDER BY order_index"
        params = (project_id, plan_id)
    else:
        sql = "SELECT * FROM tasks WHERE project_id = ? ORDER BY order_index"
        params = (project_id,)
    async with db.execute(sql, params) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def update_task(
    db: aiosqlite.Connection,
    task_id: int,
    **fields: Any,
) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [task_id]
    await db.execute(f"UPDATE tasks SET {sets} WHERE id = ?", vals)
    await db.commit()


# ------------------------------------------------------------------
# Conversations
# ------------------------------------------------------------------

async def add_conversation_message(
    db: aiosqlite.Connection,
    project_id: str,
    role: str,
    content: Any,
    token_count: int = 0,
    phase: str = "coding",
) -> int:
    content_json = json.dumps(content, default=str) if not isinstance(content, str) else content
    async with db.execute(
        "INSERT INTO conversations (project_id, role, content, token_count, phase) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, role, content_json, token_count, phase),
    ) as cur:
        msg_id = cur.lastrowid
    await db.commit()
    return msg_id


async def get_conversation(
    db: aiosqlite.Connection,
    project_id: str,
    phase: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if phase:
        sql = ("SELECT * FROM conversations WHERE project_id = ? AND phase = ? "
               "ORDER BY id DESC LIMIT ?")
        params = (project_id, phase, limit)
    else:
        sql = "SELECT * FROM conversations WHERE project_id = ? ORDER BY id DESC LIMIT ?"
        params = (project_id, limit)
    async with db.execute(sql, params) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    rows.reverse()  # oldest first
    for row in rows:
        try:
            row["content"] = json.loads(row["content"])
        except (json.JSONDecodeError, TypeError):
            pass
    return rows


# ------------------------------------------------------------------
# Provider Usage
# ------------------------------------------------------------------

async def record_provider_usage(
    db: aiosqlite.Connection,
    provider_name: str,
    requests: int = 1,
    tokens: int = 0,
    error: bool = False,
) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = _now()
    await db.execute(
        """INSERT INTO provider_usage (provider_name, date, requests_used, tokens_used, errors, last_request_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(provider_name, date) DO UPDATE SET
               requests_used = requests_used + ?,
               tokens_used = tokens_used + ?,
               errors = errors + ?,
               last_request_at = ?""",
        (provider_name, today, requests, tokens, int(error), now,
         requests, tokens, int(error), now),
    )
    await db.commit()


async def get_provider_usage(
    db: aiosqlite.Connection,
    provider_name: str,
    date: str | None = None,
) -> dict[str, Any] | None:
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with db.execute(
        "SELECT * FROM provider_usage WHERE provider_name = ? AND date = ?",
        (provider_name, date),
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_all_provider_usage_today(
    db: aiosqlite.Connection,
) -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with db.execute(
        "SELECT * FROM provider_usage WHERE date = ? ORDER BY provider_name",
        (today,),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


# ------------------------------------------------------------------
# Project Events
# ------------------------------------------------------------------

async def add_event(
    db: aiosqlite.Connection,
    project_id: str,
    event_type: str,
    summary: str,
    detail: str = "",
) -> int:
    async with db.execute(
        "INSERT INTO project_events (project_id, event_type, summary, detail) "
        "VALUES (?, ?, ?, ?)",
        (project_id, event_type, summary, detail),
    ) as cur:
        event_id = cur.lastrowid
    await db.commit()
    return event_id


async def get_events(
    db: aiosqlite.Connection,
    project_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT * FROM project_events WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
        (project_id, limit),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


# ------------------------------------------------------------------
# Agents (v3)
# ------------------------------------------------------------------

async def create_agent(
    db: aiosqlite.Connection,
    project_id: str,
    role: str,
) -> str:
    agent_id = _uuid()
    await db.execute(
        "INSERT INTO agents (id, project_id, role, created_at) VALUES (?, ?, ?, ?)",
        (agent_id, project_id, role, _now()),
    )
    await db.commit()
    return agent_id


async def get_agent(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any] | None:
    async with db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_agent_by_project_role(
    db: aiosqlite.Connection,
    project_id: str,
    role: str,
) -> dict[str, Any] | None:
    async with db.execute(
        "SELECT * FROM agents WHERE project_id = ? AND role = ?",
        (project_id, role),
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_agents(
    db: aiosqlite.Connection,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    if project_id:
        sql = "SELECT * FROM agents WHERE project_id = ? ORDER BY role"
        params: tuple = (project_id,)
    else:
        sql = "SELECT * FROM agents ORDER BY project_id, role"
        params = ()
    async with db.execute(sql, params) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def update_agent(
    db: aiosqlite.Connection,
    agent_id: str,
    *,
    status: str | None = None,
    tasks_completed_delta: int = 0,
    total_tokens_delta: int = 0,
    last_active_at: str | None = None,
) -> None:
    parts: list[str] = []
    vals: list[Any] = []
    if status is not None:
        parts.append("status = ?")
        vals.append(status)
    if tasks_completed_delta:
        parts.append("tasks_completed = tasks_completed + ?")
        vals.append(tasks_completed_delta)
    if total_tokens_delta:
        parts.append("total_tokens = total_tokens + ?")
        vals.append(total_tokens_delta)
    if last_active_at is not None:
        parts.append("last_active_at = ?")
        vals.append(last_active_at)
    if not parts:
        return
    vals.append(agent_id)
    await db.execute(f"UPDATE agents SET {', '.join(parts)} WHERE id = ?", vals)
    await db.commit()
