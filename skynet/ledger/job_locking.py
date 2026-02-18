"""
SKYNET Ledger - Job Locking

Simple distributed lock manager backed by SQLite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class JobLockManager:
    """Atomic job lock operations with expiration support."""

    def __init__(self, db: aiosqlite.Connection, lock_timeout_seconds: int = 300) -> None:
        self.db = db
        self.lock_timeout_seconds = lock_timeout_seconds

    async def acquire_lock(
        self,
        job_id: str,
        worker_id: str,
        timeout_seconds: int | None = None,
    ) -> bool:
        """
        Acquire a lock if currently unlocked.

        Returns True when acquired, False when already locked by another worker.
        """
        now = _utc_now()
        ttl = timeout_seconds if timeout_seconds is not None else self.lock_timeout_seconds
        expires_at = _iso(now + timedelta(seconds=ttl))
        now_iso = _iso(now)

        # Clean up expired locks first, then try atomic insert.
        await self.db.execute(
            "DELETE FROM job_locks WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now_iso,),
        )
        cur = await self.db.execute(
            """
            INSERT OR IGNORE INTO job_locks (job_id, worker_id, acquired_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, worker_id, now_iso, expires_at),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def release_lock(self, job_id: str, worker_id: str) -> bool:
        """Release lock if owned by this worker."""
        cur = await self.db.execute(
            "DELETE FROM job_locks WHERE job_id = ? AND worker_id = ?",
            (job_id, worker_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def extend_lock(
        self,
        job_id: str,
        worker_id: str,
        additional_seconds: int,
    ) -> bool:
        """Extend lock expiration if lock is still owned and unexpired."""
        now = _utc_now()
        now_iso = _iso(now)

        async with self.db.execute(
            """
            SELECT expires_at FROM job_locks
            WHERE job_id = ? AND worker_id = ?
            """,
            (job_id, worker_id),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return False

        current_expires_raw = row[0]
        if current_expires_raw is None:
            new_expiry = now + timedelta(seconds=additional_seconds)
        else:
            current_expires = datetime.fromisoformat(current_expires_raw)
            if current_expires.tzinfo is None:
                current_expires = current_expires.replace(tzinfo=timezone.utc)
            if current_expires <= now:
                return False
            new_expiry = current_expires + timedelta(seconds=additional_seconds)

        cur = await self.db.execute(
            """
            UPDATE job_locks
            SET expires_at = ?
            WHERE job_id = ? AND worker_id = ? AND (expires_at IS NULL OR expires_at > ?)
            """,
            (_iso(new_expiry), job_id, worker_id, now_iso),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def cleanup_expired_locks(self) -> int:
        """Delete expired locks and return count."""
        now_iso = _iso(_utc_now())
        cur = await self.db.execute(
            "DELETE FROM job_locks WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now_iso,),
        )
        await self.db.commit()
        return cur.rowcount

    async def is_locked(self, job_id: str) -> bool:
        """Check if job has a currently valid lock."""
        now_iso = _iso(_utc_now())
        async with self.db.execute(
            """
            SELECT 1 FROM job_locks
            WHERE job_id = ? AND (expires_at IS NULL OR expires_at > ?)
            LIMIT 1
            """,
            (job_id, now_iso),
        ) as cur:
            return await cur.fetchone() is not None

    async def get_lock_owner(self, job_id: str) -> str | None:
        """Return worker_id holding a non-expired lock, else None."""
        now_iso = _iso(_utc_now())
        async with self.db.execute(
            """
            SELECT worker_id FROM job_locks
            WHERE job_id = ? AND (expires_at IS NULL OR expires_at > ?)
            LIMIT 1
            """,
            (job_id, now_iso),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None
