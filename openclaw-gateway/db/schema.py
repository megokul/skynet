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
CREATE INDEX IF NOT EXISTS idx_tasks_project      ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_provider_usage_day ON provider_usage(provider_name, date);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the SQLite database and apply the schema."""
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA_SQL)
    await db.commit()

    # ── Column migrations (safe to run on every startup) ──────────────────────
    _migrations = [
        # projects table — columns added after the initial rewrite
        "ALTER TABLE projects ADD COLUMN description   TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN display_name  TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN user_id       INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN project_type  TEXT    NOT NULL DEFAULT 'Other'",
        "ALTER TABLE projects ADD COLUMN status        TEXT    NOT NULL DEFAULT 'ideation'",
        # tasks table — columns added after initial rewrite
        "ALTER TABLE tasks ADD COLUMN result_summary TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE tasks ADD COLUMN error_message  TEXT NOT NULL DEFAULT ''",
    ]
    for sql in _migrations:
        try:
            await db.execute(sql)
            await db.commit()
        except Exception:
            pass  # column already exists or table not yet created — fine

    # ── Indexes that reference potentially-migrated columns ───────────────────
    try:
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_user "
            "ON projects(user_id, created_at DESC)"
        )
        await db.commit()
    except Exception:
        pass

    return db
