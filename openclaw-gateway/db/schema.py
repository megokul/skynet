"""
SKYNET — SQLite Schema

All tables used by the project orchestrator.  Run ``await init_db(path)``
once at startup to ensure every table exists.
"""

from __future__ import annotations

import aiosqlite

SCHEMA_SQL = """
-- Projects
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    description     TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'ideation',
    tech_stack      TEXT DEFAULT '{}',
    github_repo     TEXT DEFAULT '',
    local_path      TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at     TEXT,
    completed_at    TEXT
);

-- Raw idea messages before plan synthesis
CREATE TABLE IF NOT EXISTS ideas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    message_text    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Synthesised project plans
CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    version         INTEGER NOT NULL DEFAULT 1,
    summary         TEXT NOT NULL,
    timeline        TEXT NOT NULL DEFAULT '[]',
    milestones      TEXT NOT NULL DEFAULT '[]',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Individual tasks within a plan
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    plan_id         INTEGER NOT NULL REFERENCES plans(id),
    milestone       TEXT DEFAULT '',
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    order_index     INTEGER NOT NULL DEFAULT 0,
    assigned_agent_role TEXT DEFAULT 'backend',
    result_summary  TEXT DEFAULT '',
    error_message   TEXT DEFAULT '',
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Specialized agents per project
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    role            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'idle',
    tasks_completed INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_active_at  TEXT,
    UNIQUE(project_id, role)
);

-- Claude/Gemini conversation history per project
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    token_count     INTEGER DEFAULT 0,
    phase           TEXT NOT NULL DEFAULT 'coding',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-provider daily usage tracking
CREATE TABLE IF NOT EXISTS provider_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name   TEXT NOT NULL,
    date            TEXT NOT NULL,
    requests_used   INTEGER NOT NULL DEFAULT 0,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    errors          INTEGER NOT NULL DEFAULT 0,
    last_request_at TEXT,
    UNIQUE(provider_name, date)
);

-- High-level project events for Telegram notifications
CREATE TABLE IF NOT EXISTS project_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    event_type      TEXT NOT NULL,
    summary         TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Idempotent action execution cache for dispatch retries
CREATE TABLE IF NOT EXISTS action_idempotency (
    task_id          TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    response_json    TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, idempotency_key)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ideas_project ON ideas(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id);
CREATE INDEX IF NOT EXISTS idx_events_project ON project_events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_provider_usage_lookup ON provider_usage(provider_name, date);
CREATE INDEX IF NOT EXISTS idx_agents_project ON agents(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_role ON tasks(assigned_agent_role);
CREATE INDEX IF NOT EXISTS idx_action_idempotency_created ON action_idempotency(created_at);
"""


_MIGRATIONS = [
    # v3: add assigned_agent_role column to tasks if missing.
    "ALTER TABLE tasks ADD COLUMN assigned_agent_role TEXT DEFAULT 'backend'",
]


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the database and ensure all tables exist."""
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA_SQL)
    await db.commit()

    # Apply migrations (safe for pre-existing databases).
    for migration in _MIGRATIONS:
        try:
            await db.execute(migration)
            await db.commit()
        except Exception:
            pass  # Column/table already exists — ignore.

    return db
