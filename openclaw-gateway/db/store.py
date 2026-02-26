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
    """
    Now.
    
    Purpose:
    - Implement `_now` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _uuid() -> str:
    """
    Uuid.
    
    Purpose:
    - Implement `_uuid` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

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
    """
    Create project.
    
    Purpose:
    - Implement `create_project` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `name`: input used by this function to compute or route work.
    - `display_name`: input used by this function to compute or route work.
    - `local_path`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    project_id = _uuid()
    await db.execute(
        "INSERT INTO projects (id, name, display_name, local_path) VALUES (?, ?, ?, ?)",
        (project_id, name, display_name, local_path),
    )
    await db.commit()
    return await get_project(db, project_id)


async def get_project(db: aiosqlite.Connection, project_id: str) -> dict[str, Any] | None:
    """
    Get project.
    
    Purpose:
    - Implement `get_project` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    async with db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_project_by_name(db: aiosqlite.Connection, name: str) -> dict[str, Any] | None:
    """
    Get project by name.
    
    Purpose:
    - Implement `get_project_by_name` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `name`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    async with db.execute("SELECT * FROM projects WHERE name = ?", (name,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_projects(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """
    List projects.
    
    Purpose:
    - Implement `list_projects` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    async with db.execute("SELECT * FROM projects ORDER BY created_at DESC") as cur:
        return [dict(row) for row in await cur.fetchall()]


_PROJECTS_UPDATABLE_COLUMNS: frozenset[str] = frozenset({
    "name",
    "display_name",
    "description",
    "status",
    "tech_stack",
    "github_repo",
    "local_path",
    "updated_at",
    "approved_at",
    "completed_at",
})


async def update_project(
    db: aiosqlite.Connection,
    project_id: str,
    **fields: Any,
) -> None:
    """
    Update project.
    
    Purpose:
    - Implement `update_project` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `**fields`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    invalid = set(fields) - _PROJECTS_UPDATABLE_COLUMNS
    if invalid:
        raise ValueError(f"update_project: unknown column(s): {sorted(invalid)}")
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [project_id]
    await db.execute(f"UPDATE projects SET {sets} WHERE id = ?", vals)
    await db.commit()


async def remove_project_cascade(
    db: aiosqlite.Connection,
    project_id: str,
) -> bool:
    """
    Permanently remove a project and all project-scoped records.

    Returns True when a project row was deleted.
    """
    tables = (
        "ideas",
        "tasks",
        "plans",
        "agents",
        "project_conversations",
        "project_events",
        "agent_runs",
        "task_artifacts",
    )
    for table in tables:
        await db.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
    cur = await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    await db.commit()
    return int(cur.rowcount or 0) > 0


async def get_projects_by_status(
    db: aiosqlite.Connection,
    status: str,
) -> list[dict[str, Any]]:
    """
    Get projects by status.
    
    Purpose:
    - Implement `get_projects_by_status` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `status`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

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
    """
    Add idea.
    
    Purpose:
    - Implement `add_idea` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `message_text`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    async with db.execute(
        "INSERT INTO ideas (project_id, message_text) VALUES (?, ?)",
        (project_id, message_text),
    ) as cur:
        idea_id = cur.lastrowid
    await db.commit()
    return idea_id


async def get_ideas(db: aiosqlite.Connection, project_id: str) -> list[dict[str, Any]]:
    """
    Get ideas.
    
    Purpose:
    - Implement `get_ideas` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

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
    """
    Create plan.
    
    Purpose:
    - Implement `create_plan` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `summary`: input used by this function to compute or route work.
    - `timeline`: input used by this function to compute or route work.
    - `milestones`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

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
    """
    Get active plan.
    
    Purpose:
    - Implement `get_active_plan` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

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
    """
    Create tasks.
    
    Purpose:
    - Implement `create_tasks` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `plan_id`: input used by this function to compute or route work.
    - `tasks`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[int]` when available; otherwise side effects only.
    """

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
    """
    Get tasks.
    
    Purpose:
    - Implement `get_tasks` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `plan_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

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
    """
    Update task.
    
    Purpose:
    - Implement `update_task` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `task_id`: input used by this function to compute or route work.
    - `**fields`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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
    """
    Add conversation message.
    
    Purpose:
    - Implement `add_conversation_message` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `role`: input used by this function to compute or route work.
    - `content`: input used by this function to compute or route work.
    - `token_count`: input used by this function to compute or route work.
    - `phase`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    content_json = json.dumps(content, default=str) if not isinstance(content, str) else content
    async with db.execute(
        "INSERT INTO project_conversations (project_id, role, content, token_count, phase) "
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
    """
    Get conversation.
    
    Purpose:
    - Implement `get_conversation` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `phase`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    if phase:
        sql = ("SELECT * FROM project_conversations WHERE project_id = ? AND phase = ? "
               "ORDER BY id DESC LIMIT ?")
        params = (project_id, phase, limit)
    else:
        sql = "SELECT * FROM project_conversations WHERE project_id = ? ORDER BY id DESC LIMIT ?"
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
# Explicit Conversation Sessions
# ------------------------------------------------------------------

def _conversation_id() -> str:
    """
    Conversation id.
    
    Purpose:
    - Implement `_conversation_id` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    return f"conv_{uuid.uuid4().hex[:16]}"


async def create_conversation_session(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    title: str,
    active_role: str = "igris",
) -> dict[str, Any]:
    """
    Create conversation session.
    
    Purpose:
    - Implement `create_conversation_session` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `title`: input used by this function to compute or route work.
    - `active_role`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    conversation_id = _conversation_id()
    now = _now()
    await db.execute(
        """
        INSERT INTO conversations (
            conversation_id, user_id, title, active_role, active_project_id,
            pending_question, pending_action, created_at, updated_at
        ) VALUES (?, ?, ?, ?, NULL, '{}', '{}', ?, ?)
        """,
        (
            conversation_id,
            int(user_id),
            title.strip() or "Conversation",
            (active_role or "igris").strip() or "igris",
            now,
            now,
        ),
    )
    await db.commit()
    row = await get_conversation_session(db, conversation_id=conversation_id)
    if not row:
        raise ValueError("Failed to load created conversation.")
    return row


async def list_conversation_sessions(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List conversation sessions.
    
    Purpose:
    - Implement `list_conversation_sessions` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        SELECT *
        FROM conversations
        WHERE user_id = ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (int(user_id), int(limit)),
    ) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["pending_question"] = _safe_json_loads(row.get("pending_question", "{}"))
        row["pending_action"] = _safe_json_loads(row.get("pending_action", "{}"))
    return rows


async def get_conversation_session(
    db: aiosqlite.Connection,
    *,
    conversation_id: str,
) -> dict[str, Any] | None:
    """
    Get conversation session.
    
    Purpose:
    - Implement `get_conversation_session` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `conversation_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    async with db.execute(
        "SELECT * FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    data = dict(row)
    data["pending_question"] = _safe_json_loads(data.get("pending_question", "{}"))
    data["pending_action"] = _safe_json_loads(data.get("pending_action", "{}"))
    return data


async def update_conversation_session(
    db: aiosqlite.Connection,
    *,
    conversation_id: str,
    **fields: Any,
) -> None:
    """
    Update conversation session.
    
    Purpose:
    - Implement `update_conversation_session` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `conversation_id`: input used by this function to compute or route work.
    - `**fields`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not fields:
        return
    update_fields = dict(fields)
    update_fields["updated_at"] = _now()
    if "pending_question" in update_fields:
        update_fields["pending_question"] = json.dumps(update_fields["pending_question"] or {})
    if "pending_action" in update_fields:
        update_fields["pending_action"] = json.dumps(update_fields["pending_action"] or {})
    sets = ", ".join(f"{key} = ?" for key in update_fields)
    vals = list(update_fields.values()) + [conversation_id]
    await db.execute(f"UPDATE conversations SET {sets} WHERE conversation_id = ?", vals)
    await db.commit()


async def get_conversation_by_user_active_pointer(
    db: aiosqlite.Connection,
    *,
    user_id: int,
) -> dict[str, Any] | None:
    """
    Get conversation by user active pointer.
    
    Purpose:
    - Implement `get_conversation_by_user_active_pointer` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    active_id = await get_user_active_conversation(db, user_id=user_id)
    if not active_id:
        return None
    return await get_conversation_session(db, conversation_id=active_id)


async def add_session_message(
    db: aiosqlite.Connection,
    *,
    conversation_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> int:
    """
    Add session message.
    
    Purpose:
    - Implement `add_session_message` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `conversation_id`: input used by this function to compute or route work.
    - `role`: input used by this function to compute or route work.
    - `content`: input used by this function to compute or route work.
    - `metadata`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        INSERT INTO messages (conversation_id, role, content, metadata, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, role, content, json.dumps(metadata or {}), _now()),
    ) as cur:
        message_id = int(cur.lastrowid)
    await db.execute(
        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
        (_now(), conversation_id),
    )
    await db.commit()
    return message_id


async def list_session_messages(
    db: aiosqlite.Connection,
    *,
    conversation_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    List session messages.
    
    Purpose:
    - Implement `list_session_messages` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `conversation_id`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        SELECT *
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (conversation_id, int(limit)),
    ) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        row["metadata"] = _safe_json_loads(row.get("metadata", "{}"))
    return rows


async def set_user_active_conversation(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    conversation_id: str,
) -> None:
    """
    Set user active conversation.
    
    Purpose:
    - Implement `set_user_active_conversation` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `conversation_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    await upsert_user_preference(
        db,
        user_id=int(user_id),
        pref_key="active_conversation_id",
        pref_value=conversation_id,
        source="conversation_manager",
    )


async def get_user_active_conversation(
    db: aiosqlite.Connection,
    *,
    user_id: int,
) -> str | None:
    """
    Get user active conversation.
    
    Purpose:
    - Implement `get_user_active_conversation` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str | None` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        SELECT pref_value
        FROM user_preferences
        WHERE user_id = ? AND pref_key = 'active_conversation_id'
        LIMIT 1
        """,
        (int(user_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    value = str(row[0] or "").strip()
    return value or None


def _safe_json_loads(value: Any) -> dict[str, Any]:
    """
    Safe json loads.
    
    Purpose:
    - Implement `_safe_json_loads` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `value`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------
# Reminders
# ------------------------------------------------------------------

async def create_reminder(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    title: str,
    due_at: str | None = None,
    notes: str = "",
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create reminder.
    
    Purpose:
    - Implement `create_reminder` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `title`: input used by this function to compute or route work.
    - `due_at`: input used by this function to compute or route work.
    - `notes`: input used by this function to compute or route work.
    - `conversation_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    now = _now()
    async with db.execute(
        """
        INSERT INTO reminders (
            user_id, conversation_id, title, due_at, notes, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            int(user_id),
            conversation_id,
            title.strip(),
            due_at,
            notes.strip(),
            now,
            now,
        ),
    ) as cur:
        reminder_id = int(cur.lastrowid)
    await db.commit()
    row = await get_reminder(db, reminder_id=reminder_id)
    if not row:
        raise ValueError("Failed to load created reminder.")
    return row


async def get_reminder(
    db: aiosqlite.Connection,
    *,
    reminder_id: int,
) -> dict[str, Any] | None:
    """
    Get reminder.
    
    Purpose:
    - Implement `get_reminder` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `reminder_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    async with db.execute(
        "SELECT * FROM reminders WHERE id = ?",
        (int(reminder_id),),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def list_reminders(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    List reminders.
    
    Purpose:
    - Implement `list_reminders` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `status`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    if status:
        sql = (
            "SELECT * FROM reminders WHERE user_id = ? AND status = ? "
            "ORDER BY COALESCE(due_at, created_at) ASC LIMIT ?"
        )
        params = (int(user_id), status, int(limit))
    else:
        sql = (
            "SELECT * FROM reminders WHERE user_id = ? "
            "ORDER BY COALESCE(due_at, created_at) ASC LIMIT ?"
        )
        params = (int(user_id), int(limit))
    async with db.execute(sql, params) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def update_reminder(
    db: aiosqlite.Connection,
    *,
    reminder_id: int,
    **fields: Any,
) -> None:
    """
    Update reminder.
    
    Purpose:
    - Implement `update_reminder` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `reminder_id`: input used by this function to compute or route work.
    - `**fields`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [int(reminder_id)]
    await db.execute(f"UPDATE reminders SET {sets} WHERE id = ?", vals)
    await db.commit()


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
    """
    Record provider usage.
    
    Purpose:
    - Implement `record_provider_usage` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `provider_name`: input used by this function to compute or route work.
    - `requests`: input used by this function to compute or route work.
    - `tokens`: input used by this function to compute or route work.
    - `error`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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
    """
    Get provider usage.
    
    Purpose:
    - Implement `get_provider_usage` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `provider_name`: input used by this function to compute or route work.
    - `date`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

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
    """
    Get all provider usage today.
    
    Purpose:
    - Implement `get_all_provider_usage_today` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

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
    """
    Add event.
    
    Purpose:
    - Implement `add_event` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `event_type`: input used by this function to compute or route work.
    - `summary`: input used by this function to compute or route work.
    - `detail`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

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
    """
    Get events.
    
    Purpose:
    - Implement `get_events` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

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
    """
    Create agent.
    
    Purpose:
    - Implement `create_agent` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `role`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    agent_id = _uuid()
    await db.execute(
        "INSERT INTO agents (id, project_id, role, created_at) VALUES (?, ?, ?, ?)",
        (agent_id, project_id, role, _now()),
    )
    await db.commit()
    return agent_id


async def get_agent(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any] | None:
    """
    Get agent.
    
    Purpose:
    - Implement `get_agent` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `agent_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    async with db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_agent_by_project_role(
    db: aiosqlite.Connection,
    project_id: str,
    role: str,
) -> dict[str, Any] | None:
    """
    Get agent by project role.
    
    Purpose:
    - Implement `get_agent_by_project_role` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `role`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

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
    """
    List agents.
    
    Purpose:
    - Implement `list_agents` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

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
    """
    Update agent.
    
    Purpose:
    - Implement `update_agent` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `agent_id`: input used by this function to compute or route work.
    - `status`: input used by this function to compute or route work.
    - `tasks_completed_delta`: input used by this function to compute or route work.
    - `total_tokens_delta`: input used by this function to compute or route work.
    - `last_active_at`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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


# ------------------------------------------------------------------
# Agent Runs + Task Artifacts
# ------------------------------------------------------------------

async def create_agent_run(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    task_id: int | None,
    agent_id: str,
    agent_role: str,
    metadata: dict[str, Any] | None = None,
) -> int:
    """
    Create agent run.
    
    Purpose:
    - Implement `create_agent_run` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `task_id`: input used by this function to compute or route work.
    - `agent_id`: input used by this function to compute or route work.
    - `agent_role`: input used by this function to compute or route work.
    - `metadata`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    now = _now()
    async with db.execute(
        """
        INSERT INTO agent_runs (
            project_id, task_id, agent_id, agent_role, status,
            started_at, heartbeat_at, metadata
        ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?)
        """,
        (
            project_id,
            task_id,
            agent_id,
            agent_role,
            now,
            now,
            json.dumps(metadata or {}),
        ),
    ) as cur:
        run_id = int(cur.lastrowid)
    await db.commit()
    return run_id


async def heartbeat_agent_run(
    db: aiosqlite.Connection,
    *,
    run_id: int,
    metadata_patch: dict[str, Any] | None = None,
) -> None:
    """
    Heartbeat agent run.
    
    Purpose:
    - Implement `heartbeat_agent_run` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `run_id`: input used by this function to compute or route work.
    - `metadata_patch`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if metadata_patch:
        async with db.execute(
            "SELECT metadata FROM agent_runs WHERE id = ?",
            (int(run_id),),
        ) as cur:
            row = await cur.fetchone()
        existing = {}
        if row and row[0]:
            try:
                existing = json.loads(row[0])
            except Exception:
                existing = {}
        existing.update(metadata_patch)
        await db.execute(
            "UPDATE agent_runs SET heartbeat_at = ?, metadata = ? WHERE id = ?",
            (_now(), json.dumps(existing), int(run_id)),
        )
    else:
        await db.execute(
            "UPDATE agent_runs SET heartbeat_at = ? WHERE id = ?",
            (_now(), int(run_id)),
        )
    await db.commit()


async def finish_agent_run(
    db: aiosqlite.Connection,
    *,
    run_id: int,
    status: str,
    error_message: str = "",
    metadata_patch: dict[str, Any] | None = None,
) -> None:
    """
    Finish agent run.
    
    Purpose:
    - Implement `finish_agent_run` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `run_id`: input used by this function to compute or route work.
    - `status`: input used by this function to compute or route work.
    - `error_message`: input used by this function to compute or route work.
    - `metadata_patch`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    status_norm = (status or "").strip().lower() or "unknown"
    now = _now()
    if metadata_patch:
        async with db.execute(
            "SELECT metadata FROM agent_runs WHERE id = ?",
            (int(run_id),),
        ) as cur:
            row = await cur.fetchone()
        existing = {}
        if row and row[0]:
            try:
                existing = json.loads(row[0])
            except Exception:
                existing = {}
        existing.update(metadata_patch)
        await db.execute(
            """
            UPDATE agent_runs
            SET status = ?, finished_at = ?, heartbeat_at = ?, error_message = ?, metadata = ?
            WHERE id = ?
            """,
            (
                status_norm,
                now,
                now,
                (error_message or "")[:2000],
                json.dumps(existing),
                int(run_id),
            ),
        )
    else:
        await db.execute(
            """
            UPDATE agent_runs
            SET status = ?, finished_at = ?, heartbeat_at = ?, error_message = ?
            WHERE id = ?
            """,
            (status_norm, now, now, (error_message or "")[:2000], int(run_id)),
        )
    await db.commit()


async def list_agent_runs(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    List agent runs.
    
    Purpose:
    - Implement `list_agent_runs` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        SELECT *
        FROM agent_runs
        WHERE project_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (project_id, int(limit)),
    ) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        try:
            row["metadata"] = json.loads(row.get("metadata", "{}"))
        except Exception:
            row["metadata"] = {}
    return rows


async def add_task_artifact(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    task_id: int | None,
    artifact_type: str,
    title: str,
    content: str = "",
    file_path: str = "",
    url: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    """
    Add task artifact.
    
    Purpose:
    - Implement `add_task_artifact` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `task_id`: input used by this function to compute or route work.
    - `artifact_type`: input used by this function to compute or route work.
    - `title`: input used by this function to compute or route work.
    - `content`: input used by this function to compute or route work.
    - `file_path`: input used by this function to compute or route work.
    - `url`: input used by this function to compute or route work.
    - `metadata`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        INSERT INTO task_artifacts (
            project_id, task_id, artifact_type, title,
            content, file_path, url, metadata, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            task_id,
            artifact_type,
            title,
            content,
            file_path,
            url,
            json.dumps(metadata or {}),
            _now(),
        ),
    ) as cur:
        artifact_id = int(cur.lastrowid)
    await db.commit()
    return artifact_id


async def list_task_artifacts(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    task_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    List task artifacts.
    
    Purpose:
    - Implement `list_task_artifacts` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `project_id`: input used by this function to compute or route work.
    - `task_id`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    if task_id is None:
        sql = (
            "SELECT * FROM task_artifacts WHERE project_id = ? "
            "ORDER BY id DESC LIMIT ?"
        )
        params: tuple[Any, ...] = (project_id, int(limit))
    else:
        sql = (
            "SELECT * FROM task_artifacts WHERE project_id = ? AND task_id = ? "
            "ORDER BY id DESC LIMIT ?"
        )
        params = (project_id, int(task_id), int(limit))

    async with db.execute(sql, params) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        try:
            row["metadata"] = json.loads(row.get("metadata", "{}"))
        except Exception:
            row["metadata"] = {}
    return rows


# ------------------------------------------------------------------
# User Memory / Persona Profile
# ------------------------------------------------------------------

async def ensure_user(
    db: aiosqlite.Connection,
    *,
    telegram_user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> dict[str, Any]:
    """
    Ensure user.
    
    Purpose:
    - Implement `ensure_user` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `telegram_user_id`: input used by this function to compute or route work.
    - `username`: input used by this function to compute or route work.
    - `first_name`: input used by this function to compute or route work.
    - `last_name`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    now = _now()
    await db.execute(
        """
        INSERT INTO users (
            telegram_user_id, username, first_name, last_name, created_at, updated_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            updated_at = excluded.updated_at,
            last_seen_at = excluded.last_seen_at
        """,
        (int(telegram_user_id), username, first_name, last_name, now, now, now),
    )
    await db.commit()
    user = await get_user_by_telegram_id(db, telegram_user_id)
    if not user:
        raise ValueError("Failed to load ensured user.")
    return user


async def get_user_by_telegram_id(
    db: aiosqlite.Connection,
    telegram_user_id: int,
) -> dict[str, Any] | None:
    """
    Get user by telegram id.
    
    Purpose:
    - Implement `get_user_by_telegram_id` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `telegram_user_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    async with db.execute(
        "SELECT * FROM users WHERE telegram_user_id = ?",
        (int(telegram_user_id),),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_user_by_id(db: aiosqlite.Connection, user_id: int) -> dict[str, Any] | None:
    """
    Get user by id.
    
    Purpose:
    - Implement `get_user_by_id` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    async with db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def set_user_memory_enabled(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    enabled: bool,
) -> None:
    """
    Set user memory enabled.
    
    Purpose:
    - Implement `set_user_memory_enabled` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `enabled`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    await db.execute(
        "UPDATE users SET memory_enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, _now(), int(user_id)),
    )
    await db.commit()


async def update_user_core_fields(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    timezone: str | None = None,
    region: str | None = None,
) -> None:
    """
    Update user core fields.
    
    Purpose:
    - Implement `update_user_core_fields` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `timezone`: input used by this function to compute or route work.
    - `region`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    fields: dict[str, Any] = {}
    if timezone is not None:
        fields["timezone"] = timezone.strip()
    if region is not None:
        fields["region"] = region.strip()
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [int(user_id)]
    await db.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
    await db.commit()


async def upsert_user_preference(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    pref_key: str,
    pref_value: str,
    source: str = "chat",
) -> None:
    """
    Upsert user preference.
    
    Purpose:
    - Implement `upsert_user_preference` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `pref_key`: input used by this function to compute or route work.
    - `pref_value`: input used by this function to compute or route work.
    - `source`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    await db.execute(
        """
        INSERT INTO user_preferences (user_id, pref_key, pref_value, source, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, pref_key) DO UPDATE SET
            pref_value = excluded.pref_value,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (int(user_id), pref_key.strip(), pref_value.strip(), source, _now()),
    )
    await db.commit()


async def get_user_preferences(
    db: aiosqlite.Connection,
    *,
    user_id: int,
) -> list[dict[str, Any]]:
    """
    Get user preferences.
    
    Purpose:
    - Implement `get_user_preferences` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        SELECT user_id, pref_key, pref_value, source, updated_at
        FROM user_preferences
        WHERE user_id = ?
        ORDER BY pref_key
        """,
        (int(user_id),),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def get_provider_usage_summary(
    db: aiosqlite.Connection,
    date: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return provider usage summary rows for a date (defaults to today).

    This is a compatibility helper used by heartbeat snapshot tasks.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with db.execute(
        """
        SELECT
            provider_name,
            date,
            requests_used,
            tokens_used,
            errors,
            last_request_at
        FROM provider_usage
        WHERE date = ?
        ORDER BY provider_name
        """,
        (date,),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def add_or_update_profile_fact(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    fact_key: str,
    fact_value: str,
    source: str = "chat",
    confidence: float = 0.6,
) -> dict[str, Any]:
    """
    Add or update profile fact.
    
    Purpose:
    - Implement `add_or_update_profile_fact` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `fact_key`: input used by this function to compute or route work.
    - `fact_value`: input used by this function to compute or route work.
    - `source`: input used by this function to compute or route work.
    - `confidence`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    now = _now()
    key = fact_key.strip().lower()
    value = fact_value.strip()
    conf = max(0.0, min(float(confidence), 1.0))

    async with db.execute(
        """
        SELECT id, confidence
        FROM user_profile_facts
        WHERE user_id = ? AND fact_key = ? AND fact_value = ?
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id), key, value),
    ) as cur:
        row = await cur.fetchone()

    if row:
        fact_id = int(row[0])
        prior_conf = float(row[1] or 0.0)
        merged_conf = max(prior_conf, conf)
        await db.execute(
            """
            UPDATE user_profile_facts
            SET is_active = 1, source = ?, confidence = ?, updated_at = ?
            WHERE id = ?
            """,
            (source, merged_conf, now, fact_id),
        )
    else:
        await db.execute(
            """
            INSERT INTO user_profile_facts (
                user_id, fact_key, fact_value, source, confidence, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (int(user_id), key, value, source, conf, now, now),
        )
    await db.commit()

    async with db.execute(
        """
        SELECT *
        FROM user_profile_facts
        WHERE user_id = ? AND fact_key = ? AND fact_value = ?
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id), key, value),
    ) as cur:
        saved = await cur.fetchone()
    if not saved:
        raise ValueError("Unable to load saved fact.")
    return dict(saved)


async def list_profile_facts(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """
    List profile facts.
    
    Purpose:
    - Implement `list_profile_facts` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `active_only`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    sql = (
        "SELECT * FROM user_profile_facts WHERE user_id = ? "
        + ("AND is_active = 1 " if active_only else "")
        + "ORDER BY updated_at DESC, id DESC"
    )
    async with db.execute(sql, (int(user_id),)) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def forget_profile_facts(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    key_or_text: str,
) -> int:
    """
    Forget profile facts.
    
    Purpose:
    - Implement `forget_profile_facts` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `key_or_text`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    needle = key_or_text.strip().lower()
    if not needle:
        return 0

    cur = await db.execute(
        """
        UPDATE user_profile_facts
        SET is_active = 0, updated_at = ?
        WHERE user_id = ? AND is_active = 1
          AND (lower(fact_key) = ? OR lower(fact_value) LIKE ?)
        """,
        (_now(), int(user_id), needle, f"%{needle}%"),
    )
    await db.commit()
    return int(cur.rowcount or 0)


async def add_user_conversation(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    role: str,
    content: str,
    chat_id: str = "",
    telegram_message_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    """
    Add user conversation.
    
    Purpose:
    - Implement `add_user_conversation` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `role`: input used by this function to compute or route work.
    - `content`: input used by this function to compute or route work.
    - `chat_id`: input used by this function to compute or route work.
    - `telegram_message_id`: input used by this function to compute or route work.
    - `metadata`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        INSERT INTO user_conversations (
            user_id, role, content, chat_id, telegram_message_id, metadata, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            role,
            content,
            chat_id,
            telegram_message_id,
            json.dumps(metadata or {}),
            _now(),
        ),
    ) as cur:
        cid = int(cur.lastrowid)
    await db.commit()
    return cid


async def list_user_conversations(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    limit: int = 50,
    since_seconds: int | None = None,
    after_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    List user conversations.
    
    Purpose:
    - Implement `list_user_conversations` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `limit`: input used by this function to compute or route work.
    - `since_seconds`: input used by this function to compute or route work.
    - `after_id`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
    """

    conditions = ["user_id = ?"]
    params: list[Any] = [int(user_id)]

    if since_seconds is not None:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=since_seconds)).strftime("%Y-%m-%dT%H:%M:%S")
        conditions.append("created_at >= ?")
        params.append(cutoff)

    if after_id is not None:
        conditions.append("id > ?")
        params.append(int(after_id))

    where = " AND ".join(conditions)
    params.append(int(limit))
    async with db.execute(
        f"SELECT * FROM user_conversations WHERE {where} ORDER BY id DESC LIMIT ?",
        params,
    ) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    rows.reverse()
    for row in rows:
        try:
            row["metadata"] = json.loads(row.get("metadata", "{}"))
        except Exception:
            row["metadata"] = {}
    return rows


async def add_conversation_summary(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    summary: str,
    covered_up_to_id: int,
    message_count: int,
) -> int:
    """Insert a rolling summary record. Returns the new summary id."""
    async with db.execute(
        """
        INSERT INTO conversation_summaries (
            user_id, summary, covered_up_to_id, message_count, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (int(user_id), summary, int(covered_up_to_id), int(message_count), _now()),
    ) as cur:
        sid = int(cur.lastrowid)
    await db.commit()
    return sid


async def get_latest_conversation_summary(
    db: aiosqlite.Connection,
    *,
    user_id: int,
) -> dict[str, Any] | None:
    """Return the most recent summary row for the user, or None."""
    async with db.execute(
        """
        SELECT *
        FROM conversation_summaries
        WHERE user_id = ?
        ORDER BY covered_up_to_id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_last_user_message_time(
    db: aiosqlite.Connection,
    *,
    user_id: int,
) -> str | None:
    """Return the ISO timestamp of the most recent user-role conversation row, or None."""
    async with db.execute(
        """
        SELECT MAX(created_at)
        FROM user_conversations
        WHERE user_id = ? AND role = 'user'
        """,
        (int(user_id),),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def add_memory_audit_log(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    action: str,
    target_type: str = "",
    target_key: str = "",
    detail: str = "",
) -> int:
    """
    Add memory audit log.
    
    Purpose:
    - Implement `add_memory_audit_log` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `user_id`: input used by this function to compute or route work.
    - `action`: input used by this function to compute or route work.
    - `target_type`: input used by this function to compute or route work.
    - `target_key`: input used by this function to compute or route work.
    - `detail`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    async with db.execute(
        """
        INSERT INTO memory_audit_log (
            user_id, action, target_type, target_key, detail, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (int(user_id), action, target_type, target_key, detail, _now()),
    ) as cur:
        audit_id = int(cur.lastrowid)
    await db.commit()
    return audit_id


# ------------------------------------------------------------------
# Orchestrator Sessions
# ------------------------------------------------------------------

async def get_or_create_session(
    db: aiosqlite.Connection,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Load session by telegram user_id string, or create with defaults."""
    row = await db.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
    row = await row.fetchone()
    if row:
        return dict(row)
    await db.execute(
        "INSERT INTO sessions (user_id) VALUES (?)",
        (user_id,),
    )
    await db.commit()
    row = await db.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
    return dict(await row.fetchone())


async def update_session(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    **fields: Any,
) -> None:
    """UPDATE sessions SET field=? WHERE user_id=?. Accepts any subset of columns."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    await db.execute(f"UPDATE sessions SET {set_clause} WHERE user_id = ?", values)
    await db.commit()
