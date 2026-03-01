"""
SKYNET — SQLite Schema

4 tables only. Designed to grow — add tables per feature, never drop columns.
"""
from __future__ import annotations

import aiosqlite

SCHEMA_SQL = """
-- ── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    username         TEXT    NOT NULL DEFAULT '',
    first_name       TEXT    NOT NULL DEFAULT '',
    last_name        TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Projects ─────────────────────────────────────────────────────────────────
-- status lifecycle: ideation → planning → approved → active → done
CREATE TABLE IF NOT EXISTS projects (
    id           TEXT    PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT    NOT NULL,
    display_name TEXT    NOT NULL DEFAULT '',
    project_type TEXT    NOT NULL DEFAULT 'Other',
    description  TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'ideation',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Tasks ────────────────────────────────────────────────────────────────────
-- Scaffolded now; populated when worker execution is wired in.
-- status lifecycle: pending → running → done | failed
CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT    NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title          TEXT    NOT NULL,
    description    TEXT    NOT NULL DEFAULT '',
    status         TEXT    NOT NULL DEFAULT 'pending',
    result_summary TEXT    NOT NULL DEFAULT '',
    error_message  TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Provider usage ────────────────────────────────────────────────────────────
-- Daily quota tracking — one row per provider per day.
CREATE TABLE IF NOT EXISTS provider_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name   TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    requests_used   INTEGER NOT NULL DEFAULT 0,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    errors          INTEGER NOT NULL DEFAULT 0,
    last_request_at TEXT,
    UNIQUE(provider_name, date)
);

-- ── Indexes (only those safe to create before migrations) ─────────────────────
CREATE INDEX IF NOT EXISTS idx_users_telegram_id  ON users(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_provider_usage_day ON provider_usage(provider_name, date);
"""

# Clean tasks DDL used when recreating the table from a legacy schema.
_TASKS_CLEAN_DDL = """
    CREATE TABLE tasks_clean (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id     TEXT    NOT NULL,
        title          TEXT    NOT NULL,
        description    TEXT    NOT NULL DEFAULT '',
        status         TEXT    NOT NULL DEFAULT 'pending',
        result_summary TEXT    NOT NULL DEFAULT '',
        error_message  TEXT    NOT NULL DEFAULT '',
        created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
    )
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the SQLite database and apply the schema."""
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA_SQL)
    await db.commit()

    # ── Column migrations for projects (safe to run on every startup) ─────────
    for sql in [
        "ALTER TABLE projects ADD COLUMN description   TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN display_name  TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN user_id       INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN project_type  TEXT    NOT NULL DEFAULT 'Other'",
        "ALTER TABLE projects ADD COLUMN status        TEXT    NOT NULL DEFAULT 'ideation'",
    ]:
        try:
            await db.execute(sql)
            await db.commit()
        except Exception:
            pass  # column already exists — fine

    # ── Rebuild tasks table if it has legacy NOT-NULL columns with no default ──
    # The old schema had plan_id, order_index, etc. that break our INSERTs.
    # We migrate valid data (project_id, title, description, status, …) and drop
    # the incompatible columns by recreating the table under a new name then
    # renaming it back.
    async with db.execute("PRAGMA table_info(tasks)") as cur:
        task_cols = {row[1] for row in await cur.fetchall()}

    if "plan_id" in task_cols:
        await db.execute(_TASKS_CLEAN_DDL)
        await db.execute(
            """
            INSERT OR IGNORE INTO tasks_clean
                (id, project_id, title, description, status,
                 result_summary, error_message, created_at, updated_at)
            SELECT
                id, project_id, title,
                COALESCE(description,    ''),
                COALESCE(status,         'pending'),
                COALESCE(result_summary, ''),
                COALESCE(error_message,  ''),
                COALESCE(created_at,     datetime('now')),
                COALESCE(updated_at,     datetime('now'))
            FROM tasks
            """
        )
        await db.execute("DROP TABLE tasks")
        await db.execute("ALTER TABLE tasks_clean RENAME TO tasks")
        await db.commit()

    # ── Indexes that may reference migrated columns ────────────────────────────
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_project  ON tasks(project_id, status)",
    ]:
        try:
            await db.execute(idx_sql)
            await db.commit()
        except Exception:
            pass

    return db
