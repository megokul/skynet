"""
SKYNET â€” SQLite Schema

Additive schema. New features extend tables; migrations avoid destructive changes.
"""
from __future__ import annotations

import aiosqlite

SCHEMA_SQL = """
-- â”€â”€ Users â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    username         TEXT    NOT NULL DEFAULT '',
    first_name       TEXT    NOT NULL DEFAULT '',
    last_name        TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- â”€â”€ Projects â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
-- status lifecycle: ideation â†’ planning â†’ approved â†’ active â†’ done
CREATE TABLE IF NOT EXISTS projects (
    id           TEXT    PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT    NOT NULL,
    display_name TEXT    NOT NULL DEFAULT '',
    project_type TEXT    NOT NULL DEFAULT 'Other',
    description  TEXT    NOT NULL DEFAULT '',
    coding_profile TEXT  NOT NULL DEFAULT 'legacy',
    quality_profile TEXT  NOT NULL DEFAULT 'legacy',
    control_loop_profile TEXT NOT NULL DEFAULT 'legacy',
    status       TEXT    NOT NULL DEFAULT 'ideation',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- â”€â”€ Tasks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
-- Scaffolded now; populated when worker execution is wired in.
-- status lifecycle: pending â†’ running â†’ done | failed
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

CREATE TABLE IF NOT EXISTS task_gate_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt    INTEGER NOT NULL,
    gate_name  TEXT    NOT NULL,
    status     TEXT    NOT NULL,
    command    TEXT    NOT NULL DEFAULT '',
    summary    TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_orchestration_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    phase      TEXT    NOT NULL DEFAULT '',
    stage      TEXT    NOT NULL DEFAULT '',
    session_id TEXT    NOT NULL DEFAULT '',
    runtime    TEXT    NOT NULL DEFAULT '',
    queue_mode TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT '',
    summary    TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_graphs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT    NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    goal            TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'active',
    planner_summary TEXT    NOT NULL DEFAULT '',
    iteration_count INTEGER NOT NULL DEFAULT 0,
    repair_count    INTEGER NOT NULL DEFAULT 0,
    max_iterations  INTEGER NOT NULL DEFAULT 40,
    max_runtime_seconds INTEGER NOT NULL DEFAULT 3600,
    max_repairs     INTEGER NOT NULL DEFAULT 1,
    max_tokens      INTEGER NOT NULL DEFAULT 250000,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    success_contract_json TEXT NOT NULL DEFAULT '{}',
    started_at      TEXT,
    finished_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_nodes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_id             INTEGER NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
    node_key             TEXT    NOT NULL,
    title                TEXT    NOT NULL,
    node_type            TEXT    NOT NULL,
    owner                TEXT    NOT NULL DEFAULT '',
    deps_json            TEXT    NOT NULL DEFAULT '[]',
    inputs_json          TEXT    NOT NULL DEFAULT '{}',
    allowed_paths_json   TEXT    NOT NULL DEFAULT '[]',
    forbidden_paths_json TEXT    NOT NULL DEFAULT '[]',
    acceptance_json      TEXT    NOT NULL DEFAULT '[]',
    priority             INTEGER NOT NULL DEFAULT 100,
    execution_lock       TEXT    NOT NULL DEFAULT 'repo-write',
    retry_budget         INTEGER NOT NULL DEFAULT 0,
    attempt_count        INTEGER NOT NULL DEFAULT 0,
    status               TEXT    NOT NULL DEFAULT 'queued',
    failure_type         TEXT    NOT NULL DEFAULT '',
    result_summary       TEXT    NOT NULL DEFAULT '',
    error_message        TEXT    NOT NULL DEFAULT '',
    started_at           TEXT,
    finished_at          TEXT,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS critic_findings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id       INTEGER NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    critic_name   TEXT    NOT NULL,
    severity      TEXT    NOT NULL,
    code          TEXT    NOT NULL DEFAULT '',
    message       TEXT    NOT NULL,
    files_json    TEXT    NOT NULL DEFAULT '[]',
    suggested_fix TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_memory (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        TEXT    NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tier              TEXT    NOT NULL,
    memory_key        TEXT    NOT NULL,
    memory_value_json TEXT    NOT NULL DEFAULT '{}',
    source_node_id    INTEGER REFERENCES task_nodes(id) ON DELETE SET NULL,
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, tier, memory_key)
);

CREATE TABLE IF NOT EXISTS task_node_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_id     INTEGER NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
    node_id      INTEGER REFERENCES task_nodes(id) ON DELETE CASCADE,
    node_key     TEXT    NOT NULL DEFAULT '',
    event_type   TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT '',
    agent        TEXT    NOT NULL DEFAULT '',
    stage        TEXT    NOT NULL DEFAULT '',
    failure_type TEXT    NOT NULL DEFAULT '',
    details_json TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS code_index_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT    NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path       TEXT    NOT NULL,
    language   TEXT    NOT NULL DEFAULT '',
    sha1       TEXT    NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, path)
);

CREATE TABLE IF NOT EXISTS code_index_symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT    NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    symbol_kind TEXT    NOT NULL DEFAULT '',
    line_no     INTEGER NOT NULL DEFAULT 0,
    signature   TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS code_index_refs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT    NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    from_path  TEXT    NOT NULL,
    to_module  TEXT    NOT NULL,
    ref_kind   TEXT    NOT NULL DEFAULT 'import'
);

-- â”€â”€ Provider usage â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
-- Daily quota tracking â€” one row per provider per day.
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

-- â”€â”€ Indexes (only those safe to create before migrations) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CREATE INDEX IF NOT EXISTS idx_users_telegram_id  ON users(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_provider_usage_day ON provider_usage(provider_name, date);
CREATE INDEX IF NOT EXISTS idx_task_gate_results_lookup
    ON task_gate_results(task_id, attempt, gate_name);
CREATE INDEX IF NOT EXISTS idx_task_orchestration_runs_lookup
    ON task_orchestration_runs(task_id, created_at, stage);
CREATE INDEX IF NOT EXISTS idx_task_graphs_project_status
    ON task_graphs(project_id, status);
CREATE INDEX IF NOT EXISTS idx_task_nodes_graph_status
    ON task_nodes(graph_id, status);
CREATE INDEX IF NOT EXISTS idx_task_nodes_graph_node_key
    ON task_nodes(graph_id, node_key);
CREATE INDEX IF NOT EXISTS idx_task_nodes_graph_status_priority
    ON task_nodes(graph_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_critic_findings_node_severity
    ON critic_findings(node_id, severity);
CREATE INDEX IF NOT EXISTS idx_project_memory_lookup
    ON project_memory(project_id, tier, memory_key);
CREATE INDEX IF NOT EXISTS idx_task_node_events_graph_created
    ON task_node_events(graph_id, created_at);
CREATE INDEX IF NOT EXISTS idx_code_index_files_project_updated
    ON code_index_files(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_code_index_symbols_project_symbol
    ON code_index_symbols(project_id, symbol);
CREATE INDEX IF NOT EXISTS idx_code_index_refs_project_from
    ON code_index_refs(project_id, from_path);
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

    # â”€â”€ Column migrations for projects (safe to run on every startup) â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for sql in [
        "ALTER TABLE projects ADD COLUMN description   TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN display_name  TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN user_id       INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN project_type  TEXT    NOT NULL DEFAULT 'Other'",
        "ALTER TABLE projects ADD COLUMN coding_profile TEXT NOT NULL DEFAULT 'legacy'",
        "ALTER TABLE projects ADD COLUMN quality_profile TEXT NOT NULL DEFAULT 'legacy'",
        "ALTER TABLE projects ADD COLUMN control_loop_profile TEXT NOT NULL DEFAULT 'legacy'",
        "ALTER TABLE projects ADD COLUMN status        TEXT    NOT NULL DEFAULT 'ideation'",
    ]:
        try:
            await db.execute(sql)
            await db.commit()
        except Exception:
            pass  # column already exists â€” fine

    # â”€â”€ Rebuild tasks table if it has legacy NOT-NULL columns with no default â”€â”€
    # The old schema had plan_id, order_index, etc. that break our INSERTs.
    # We migrate valid data (project_id, title, description, status, â€¦) and drop
    # the incompatible columns by recreating the table under a new name then
    # renaming it back.

    for sql in [
        "ALTER TABLE task_graphs ADD COLUMN iteration_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_graphs ADD COLUMN repair_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_graphs ADD COLUMN max_iterations INTEGER NOT NULL DEFAULT 40",
        "ALTER TABLE task_graphs ADD COLUMN max_runtime_seconds INTEGER NOT NULL DEFAULT 3600",
        "ALTER TABLE task_graphs ADD COLUMN max_repairs INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE task_graphs ADD COLUMN max_tokens INTEGER NOT NULL DEFAULT 250000",
        "ALTER TABLE task_graphs ADD COLUMN tokens_used INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_graphs ADD COLUMN success_contract_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE task_graphs ADD COLUMN started_at TEXT",
        "ALTER TABLE task_graphs ADD COLUMN finished_at TEXT",
        "ALTER TABLE task_nodes ADD COLUMN priority INTEGER NOT NULL DEFAULT 100",
        "ALTER TABLE task_nodes ADD COLUMN execution_lock TEXT NOT NULL DEFAULT 'repo-write'",
        "ALTER TABLE task_nodes ADD COLUMN failure_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE task_nodes ADD COLUMN started_at TEXT",
        "ALTER TABLE task_nodes ADD COLUMN finished_at TEXT",
    ]:
        try:
            await db.execute(sql)
            await db.commit()
        except Exception:
            pass

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

    # â”€â”€ Indexes that may reference migrated columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_project  ON tasks(project_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_task_gate_results_lookup "
        "ON task_gate_results(task_id, attempt, gate_name)",
        "CREATE INDEX IF NOT EXISTS idx_task_orchestration_runs_lookup "
        "ON task_orchestration_runs(task_id, created_at, stage)",
        "CREATE INDEX IF NOT EXISTS idx_task_graphs_project_status "
        "ON task_graphs(project_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_task_nodes_graph_status "
        "ON task_nodes(graph_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_task_nodes_graph_node_key "
        "ON task_nodes(graph_id, node_key)",
        "CREATE INDEX IF NOT EXISTS idx_task_nodes_graph_status_priority "
        "ON task_nodes(graph_id, status, priority)",
        "CREATE INDEX IF NOT EXISTS idx_critic_findings_node_severity "
        "ON critic_findings(node_id, severity)",
        "CREATE INDEX IF NOT EXISTS idx_project_memory_lookup "
        "ON project_memory(project_id, tier, memory_key)",
        "CREATE INDEX IF NOT EXISTS idx_task_node_events_graph_created "
        "ON task_node_events(graph_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_code_index_files_project_updated "
        "ON code_index_files(project_id, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_code_index_symbols_project_symbol "
        "ON code_index_symbols(project_id, symbol)",
        "CREATE INDEX IF NOT EXISTS idx_code_index_refs_project_from "
        "ON code_index_refs(project_id, from_path)",
    ]:
        try:
            await db.execute(idx_sql)
            await db.commit()
        except Exception:
            pass

    return db
