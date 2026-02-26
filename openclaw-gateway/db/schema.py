"""
SKYNET - SQLite Schema

All tables used by orchestration and conversation layers.
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

-- Explicit conversation sessions (primary context boundary)
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id   TEXT PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title             TEXT NOT NULL DEFAULT '',
    active_role       TEXT NOT NULL DEFAULT 'igris',
    active_project_id TEXT,
    pending_question  TEXT NOT NULL DEFAULT '{}',
    pending_action    TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Messages linked to explicit conversation sessions
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    metadata         TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reminders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id  TEXT REFERENCES conversations(conversation_id) ON DELETE SET NULL,
    title            TEXT NOT NULL,
    due_at           TEXT,
    notes            TEXT DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Legacy project-scoped conversation history (compatibility)
CREATE TABLE IF NOT EXISTS project_conversations (
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

CREATE TABLE IF NOT EXISTS agent_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL REFERENCES projects(id),
    task_id          INTEGER REFERENCES tasks(id),
    agent_id         TEXT NOT NULL,
    agent_role       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running',
    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at     TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at      TEXT,
    error_message    TEXT DEFAULT '',
    metadata         TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS task_artifacts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL REFERENCES projects(id),
    task_id          INTEGER REFERENCES tasks(id),
    artifact_type    TEXT NOT NULL,
    title            TEXT NOT NULL,
    content          TEXT DEFAULT '',
    file_path        TEXT DEFAULT '',
    url              TEXT DEFAULT '',
    metadata         TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Telegram user records for persistent persona memory
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    username         TEXT DEFAULT '',
    first_name       TEXT DEFAULT '',
    last_name        TEXT DEFAULT '',
    timezone         TEXT DEFAULT '',
    region           TEXT DEFAULT '',
    memory_enabled   INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at     TEXT
);

CREATE TABLE IF NOT EXISTS user_profile_facts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fact_key         TEXT NOT NULL,
    fact_value       TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'chat',
    confidence       REAL NOT NULL DEFAULT 0.5,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pref_key         TEXT NOT NULL,
    pref_value       TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'chat',
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, pref_key)
);

CREATE TABLE IF NOT EXISTS user_conversations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role               TEXT NOT NULL,
    content            TEXT NOT NULL,
    chat_id            TEXT DEFAULT '',
    telegram_message_id TEXT DEFAULT '',
    metadata           TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memory_audit_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action           TEXT NOT NULL,
    target_type      TEXT NOT NULL DEFAULT '',
    target_key       TEXT NOT NULL DEFAULT '',
    detail           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    summary          TEXT NOT NULL,
    covered_up_to_id INTEGER NOT NULL,
    message_count    INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id          TEXT PRIMARY KEY,
    project_id       TEXT,
    conversation_phase TEXT NOT NULL DEFAULT 'discovery',
    last_intent      TEXT NOT NULL DEFAULT '',
    last_mode        TEXT NOT NULL DEFAULT 'conversation',
    last_message_at  TEXT,
    session_metadata TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ideas_project ON ideas(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_project_conversations_project ON project_conversations(project_id);
CREATE INDEX IF NOT EXISTS idx_events_project ON project_events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_provider_usage_lookup ON provider_usage(provider_name, date);
CREATE INDEX IF NOT EXISTS idx_agents_project ON agents(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_role ON tasks(assigned_agent_role);
CREATE INDEX IF NOT EXISTS idx_action_idempotency_created ON action_idempotency(created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_id, started_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_task ON agent_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_task_artifacts_project ON task_artifacts(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_artifacts_task ON task_artifacts(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_user_facts_user ON user_profile_facts(user_id, is_active, fact_key);
CREATE INDEX IF NOT EXISTS idx_user_conversations_user ON user_conversations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_audit_user ON memory_audit_log(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_summaries_user ON conversation_summaries(user_id, covered_up_to_id DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN assigned_agent_role TEXT DEFAULT 'backend'",
    "ALTER TABLE conversations ADD COLUMN active_role TEXT NOT NULL DEFAULT 'igris'",
]


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the database and ensure all tables exist."""
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA_SQL)
    await db.commit()

    await _migrate_legacy_conversations(db)

    for migration in _MIGRATIONS:
        try:
            await db.execute(migration)
            await db.commit()
        except Exception:
            pass

    return db


async def _migrate_legacy_conversations(db: aiosqlite.Connection) -> None:
    """
    If `conversations` still has the old project-scoped schema, move rows into
    `project_conversations` and recreate `conversations` as explicit sessions.
    """
    if not await _table_exists(db, "conversations"):
        return

    if not await _table_has_column(db, "conversations", "project_id"):
        return

    await db.execute(
        """
        INSERT INTO project_conversations (project_id, role, content, token_count, phase, created_at)
        SELECT project_id, role, content, token_count, phase, created_at
        FROM conversations
        """
    )
    await db.execute("DROP TABLE conversations")
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id   TEXT PRIMARY KEY,
            user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title             TEXT NOT NULL DEFAULT '',
            active_role       TEXT NOT NULL DEFAULT 'igris',
            active_project_id TEXT,
            pending_question  TEXT NOT NULL DEFAULT '{}',
            pending_action    TEXT NOT NULL DEFAULT '{}',
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id  TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            role             TEXT NOT NULL,
            content          TEXT NOT NULL,
            metadata         TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
        """
    )
    await db.commit()


async def _table_exists(db: aiosqlite.Connection, table_name: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ) as cur:
        row = await cur.fetchone()
    return bool(row)


async def _table_has_column(db: aiosqlite.Connection, table_name: str, column_name: str) -> bool:
    async with db.execute(f"PRAGMA table_info({table_name})") as cur:
        rows = await cur.fetchall()
    return any(str(row[1]) == column_name for row in rows)
