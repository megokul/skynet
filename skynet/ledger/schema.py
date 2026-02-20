"""
SKYNET ledger schema.

Control-plane persistence:
- workers (infrastructure metadata)
- job_locks (best-effort distributed lock utility)
- control_tasks (authoritative scheduler queue in control plane)
- control_task_file_ownership (conflict prevention for file writes)
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workers (
    id              TEXT PRIMARY KEY,
    provider_name   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'offline',
    capabilities    TEXT NOT NULL DEFAULT '[]',
    current_job_id  TEXT,
    last_heartbeat  TEXT NOT NULL DEFAULT (datetime('now')),
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_locks (
    job_id          TEXT PRIMARY KEY,
    worker_id       TEXT NOT NULL,
    acquired_at     TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
CREATE INDEX IF NOT EXISTS idx_workers_provider ON workers(provider_name);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_job_locks_worker ON job_locks(worker_id);
CREATE INDEX IF NOT EXISTS idx_job_locks_expires ON job_locks(expires_at);

CREATE TABLE IF NOT EXISTS control_tasks (
    id              TEXT PRIMARY KEY,
    action          TEXT NOT NULL,
    params          TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'queued',
    priority        INTEGER NOT NULL DEFAULT 0,
    dependencies    TEXT NOT NULL DEFAULT '[]',
    dependents      TEXT NOT NULL DEFAULT '[]',
    required_files  TEXT NOT NULL DEFAULT '[]',
    locked_by       TEXT,
    locked_at       TEXT,
    claim_token     TEXT,
    gateway_id      TEXT,
    result          TEXT NOT NULL DEFAULT '{}',
    error           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS control_task_file_ownership (
    file_path       TEXT PRIMARY KEY,
    owning_task     TEXT NOT NULL REFERENCES control_tasks(id) ON DELETE CASCADE,
    claim_token     TEXT,
    claimed_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS control_task_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL REFERENCES control_tasks(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT,
    worker_id       TEXT,
    claim_token     TEXT,
    message         TEXT NOT NULL DEFAULT '',
    payload         TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_control_tasks_status ON control_tasks(status);
CREATE INDEX IF NOT EXISTS idx_control_tasks_priority ON control_tasks(priority, created_at);
CREATE INDEX IF NOT EXISTS idx_control_tasks_locked_by ON control_tasks(locked_by);
CREATE UNIQUE INDEX IF NOT EXISTS idx_control_tasks_claim_token ON control_tasks(claim_token) WHERE claim_token IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_control_file_ownership_task ON control_task_file_ownership(owning_task);
CREATE INDEX IF NOT EXISTS idx_control_task_events_task ON control_task_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_control_task_events_created ON control_task_events(created_at);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the database and ensure control-plane tables exist."""
    if db_path != ":memory:":
        Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA_SQL)
    # Normalize legacy statuses after state-machine rename.
    await db.execute("UPDATE control_tasks SET status = 'queued' WHERE status = 'pending'")
    await db.execute("UPDATE control_tasks SET status = 'succeeded' WHERE status = 'completed'")
    await db.commit()
    return db
