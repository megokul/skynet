"""
SKYNET ledger schema.

Minimal control-plane persistence:
- workers (infrastructure metadata)
- job_locks (best-effort distributed lock utility)
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
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the database and ensure control-plane tables exist."""
    if db_path != ":memory:":
        Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    return db
