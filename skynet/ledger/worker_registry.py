"""
SKYNET Ledger - Worker Registry

Tracks worker lifecycle, heartbeats, and online/offline status.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from skynet.utils import iso_now as _utc_now


class WorkerRegistry:
    """Database-backed worker registry."""

    def __init__(self, db: aiosqlite.Connection, heartbeat_timeout_seconds: int = 60) -> None:
        self.db = db
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

    async def register_worker(
        self,
        worker_id: str,
        provider_name: str,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update a worker and mark it online."""
        now = _utc_now()
        capabilities_json = json.dumps(capabilities or [])
        metadata_json = json.dumps(metadata or {})

        await self.db.execute(
            """
            INSERT INTO workers (
                id, provider_name, status, capabilities, metadata,
                last_heartbeat, created_at, updated_at
            )
            VALUES (?, ?, 'online', ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                provider_name = excluded.provider_name,
                status = 'online',
                capabilities = excluded.capabilities,
                metadata = excluded.metadata,
                last_heartbeat = excluded.last_heartbeat,
                updated_at = excluded.updated_at
            """,
            (
                worker_id,
                provider_name,
                capabilities_json,
                metadata_json,
                now,
                now,
                now,
            ),
        )
        await self.db.commit()
        return await self.get_worker(worker_id)

    async def heartbeat(self, worker_id: str) -> bool:
        """Refresh worker heartbeat and keep status online."""
        now = _utc_now()
        cur = await self.db.execute(
            """
            UPDATE workers
            SET status = 'online', last_heartbeat = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, worker_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def set_runtime_state(
        self,
        worker_id: str,
        status: str,
        current_job_id: str | None = None,
    ) -> bool:
        """Update worker status and current job assignment."""
        now = _utc_now()
        cur = await self.db.execute(
            """
            UPDATE workers
            SET status = ?, current_job_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, current_job_id, now, worker_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def mark_offline(self, worker_id: str) -> bool:
        """Explicitly mark worker as offline."""
        now = _utc_now()
        cur = await self.db.execute(
            "UPDATE workers SET status = 'offline', updated_at = ? WHERE id = ?",
            (now, worker_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        """Fetch one worker as a dictionary."""
        async with self.db.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_worker_dict(dict(row))

    async def get_online_workers(self, provider_name: str | None = None) -> list[dict[str, Any]]:
        """Return currently online workers with non-stale heartbeats."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=self.heartbeat_timeout_seconds))
        cutoff_iso = cutoff.isoformat()

        if provider_name:
            query = (
                "SELECT * FROM workers "
                "WHERE status = 'online' AND provider_name = ? AND last_heartbeat >= ? "
                "ORDER BY last_heartbeat DESC"
            )
            params: tuple[Any, ...] = (provider_name, cutoff_iso)
        else:
            query = (
                "SELECT * FROM workers "
                "WHERE status = 'online' AND last_heartbeat >= ? "
                "ORDER BY last_heartbeat DESC"
            )
            params = (cutoff_iso,)

        async with self.db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [self._row_to_worker_dict(dict(row)) for row in rows]

    async def cleanup_stale_workers(self) -> int:
        """Mark stale workers offline and return affected count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=self.heartbeat_timeout_seconds))
        cutoff_iso = cutoff.isoformat()
        now = _utc_now()

        cur = await self.db.execute(
            """
            UPDATE workers
            SET status = 'offline', updated_at = ?
            WHERE status = 'online' AND last_heartbeat < ?
            """,
            (now, cutoff_iso),
        )
        await self.db.commit()
        return cur.rowcount

    def _row_to_worker_dict(self, row: dict[str, Any]) -> dict[str, Any]:
        row["capabilities"] = json.loads(row.get("capabilities", "[]"))
        row["metadata"] = json.loads(row.get("metadata", "{}"))
        return row
