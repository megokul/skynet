"""
SKYNET Ledger - Control-plane task queue and locking.

Provides:
- Atomic task claims (`locked_by`, `locked_at`, `claim_token`)
- Explicit state-machine transitions
- Strict dependency enforcement (`dependencies` / `dependents`)
- File ownership registry to prevent parallel write conflicts
- Task event stream for read-model endpoints
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import aiosqlite


TASK_STATE_QUEUED = "queued"
TASK_STATE_CLAIMED = "claimed"
TASK_STATE_RUNNING = "running"
TASK_STATE_SUCCEEDED = "succeeded"
TASK_STATE_FAILED = "failed"
TASK_STATE_RELEASED = "released"
TASK_STATE_FAILED_TIMEOUT = "failed_timeout"

TASK_STATE_ALIASES = {
    "pending": TASK_STATE_QUEUED,
    "completed": TASK_STATE_SUCCEEDED,
}

READY_TASK_STATES = {TASK_STATE_QUEUED, TASK_STATE_RELEASED}
ACTIVE_TASK_STATES = {TASK_STATE_CLAIMED, TASK_STATE_RUNNING}
TERMINAL_TASK_STATES = {TASK_STATE_SUCCEEDED, TASK_STATE_FAILED, TASK_STATE_FAILED_TIMEOUT}
DEPENDENCY_DONE_STATES = {TASK_STATE_SUCCEEDED}

LEGAL_TASK_TRANSITIONS: dict[str, set[str]] = {
    TASK_STATE_QUEUED: {TASK_STATE_CLAIMED},
    TASK_STATE_RELEASED: {TASK_STATE_CLAIMED},
    TASK_STATE_CLAIMED: {
        TASK_STATE_RUNNING,
        TASK_STATE_RELEASED,
        TASK_STATE_FAILED,
        TASK_STATE_FAILED_TIMEOUT,
    },
    TASK_STATE_RUNNING: {
        TASK_STATE_SUCCEEDED,
        TASK_STATE_FAILED,
        TASK_STATE_RELEASED,
        TASK_STATE_FAILED_TIMEOUT,
    },
    TASK_STATE_SUCCEEDED: set(),
    TASK_STATE_FAILED: set(),
    TASK_STATE_FAILED_TIMEOUT: set(),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _json_loads_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _json_loads_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _uniq_nonempty(items: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in items or []:
        v = str(item).strip()
        if v and v not in out:
            out.append(v)
    return out


def _normalize_status(status: str | None) -> str:
    value = str(status or "").strip().lower()
    return TASK_STATE_ALIASES.get(value, value)


class TaskQueueManager:
    """Database-backed control-plane scheduler state."""

    def __init__(self, db: aiosqlite.Connection, lock_timeout_seconds: int = 300) -> None:
        self.db = db
        self.lock_timeout_seconds = lock_timeout_seconds

    async def enqueue_task(
        self,
        *,
        action: str,
        params: dict[str, Any] | None = None,
        task_id: str | None = None,
        priority: int = 0,
        dependencies: list[str] | None = None,
        required_files: list[str] | None = None,
        gateway_id: str | None = None,
    ) -> dict[str, Any]:
        now = _iso_now()
        task_id = task_id or f"task-{uuid4().hex[:12]}"
        deps = _uniq_nonempty(dependencies)
        files = _uniq_nonempty(required_files)

        if task_id in deps:
            raise ValueError("Task cannot depend on itself.")

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute("SELECT 1 FROM control_tasks WHERE id = ?", (task_id,)) as cur:
                if await cur.fetchone():
                    raise ValueError(f"Task '{task_id}' already exists.")

            if deps:
                placeholders = ",".join("?" for _ in deps)
                async with self.db.execute(
                    f"SELECT id FROM control_tasks WHERE id IN ({placeholders})",
                    tuple(deps),
                ) as cur:
                    existing = {row[0] for row in await cur.fetchall()}
                missing = [d for d in deps if d not in existing]
                if missing:
                    raise ValueError(f"Dependency tasks not found: {', '.join(missing)}")

            await self.db.execute(
                """
                INSERT INTO control_tasks (
                    id, action, params, status, priority,
                    dependencies, dependents, required_files,
                    gateway_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)
                """,
                (
                    task_id,
                    action,
                    json.dumps(params or {}),
                    TASK_STATE_QUEUED,
                    int(priority),
                    json.dumps(deps),
                    json.dumps(files),
                    gateway_id,
                    now,
                    now,
                ),
            )

            for dep_id in deps:
                async with self.db.execute(
                    "SELECT dependents FROM control_tasks WHERE id = ?",
                    (dep_id,),
                ) as cur:
                    row = await cur.fetchone()
                dep_dependents = _json_loads_list(row[0] if row else "[]")
                if task_id not in dep_dependents:
                    dep_dependents.append(task_id)
                    await self.db.execute(
                        "UPDATE control_tasks SET dependents = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(dep_dependents), now, dep_id),
                    )

            if await self._graph_has_cycle():
                raise ValueError("Dependency graph cycle detected; task enqueue rejected.")

            await self._append_event(
                task_id=task_id,
                event_type="enqueued",
                from_status=None,
                to_status=TASK_STATE_QUEUED,
                message="Task enqueued.",
                payload={"action": action, "priority": int(priority), "gateway_id": gateway_id},
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        task = await self.get_task(task_id)
        if not task:
            raise ValueError("Task was enqueued but could not be loaded.")
        return task

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self.db.execute("SELECT * FROM control_tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
        return self._row_to_task(dict(row)) if row else None

    async def list_tasks(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        normalized = _normalize_status(status) if status else None
        if normalized:
            sql = "SELECT * FROM control_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?"
            params = (normalized, int(limit))
        else:
            sql = "SELECT * FROM control_tasks ORDER BY created_at DESC LIMIT ?"
            params = (int(limit),)
        async with self.db.execute(sql, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        return [self._row_to_task(r) for r in rows]

    async def peek_next_ready_task(self, *, worker_id: str | None = None) -> dict[str, Any] | None:
        """
        Dry-run readiness check for agents.

        Does not lock the task and may race with actual claimers.
        """
        async with self.db.execute(
            """
            SELECT id, status, dependencies, required_files
            FROM control_tasks
            WHERE status IN (?, ?) AND locked_by IS NULL
            ORDER BY priority DESC, created_at ASC
            LIMIT 200
            """,
            (TASK_STATE_QUEUED, TASK_STATE_RELEASED),
        ) as cur:
            candidates = [dict(r) for r in await cur.fetchall()]

        status_map = await self._task_status_map()
        for cand in candidates:
            deps = _json_loads_list(cand.get("dependencies"))
            if any(status_map.get(dep_id) not in DEPENDENCY_DONE_STATES for dep_id in deps):
                continue
            task_id = str(cand["id"])
            files = _uniq_nonempty(_json_loads_list(cand.get("required_files")))
            if not await self._files_unowned(task_id=task_id, files=files):
                continue
            task = await self.get_task(task_id)
            if task:
                return task
        return None

    async def claim_next_ready_task(
        self,
        *,
        worker_id: str,
        lock_timeout_seconds: int | None = None,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """
        Atomically claim one ready task.

        Readiness rules:
        - status in {queued, released}
        - not currently locked
        - all dependencies are succeeded
        - required files are not owned by another active task
        """
        now = _iso_now()
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                """
                SELECT id, status, dependencies
                FROM control_tasks
                WHERE status IN (?, ?) AND locked_by IS NULL
                ORDER BY priority DESC, created_at ASC
                LIMIT 200
                """,
                (TASK_STATE_QUEUED, TASK_STATE_RELEASED),
            ) as cur:
                candidates = [dict(r) for r in await cur.fetchall()]

            status_map = await self._task_status_map()
            for cand in candidates:
                deps = _json_loads_list(cand.get("dependencies"))
                if any(status_map.get(dep_id) not in DEPENDENCY_DONE_STATES for dep_id in deps):
                    continue

                task_id = str(cand["id"])
                previous_status = _normalize_status(cand.get("status"))
                claim_token = uuid4().hex
                upd = await self.db.execute(
                    """
                    UPDATE control_tasks
                    SET status = ?, locked_by = ?, locked_at = ?, claim_token = ?, updated_at = ?
                    WHERE id = ? AND status = ? AND locked_by IS NULL
                    """,
                    (
                        TASK_STATE_CLAIMED,
                        worker_id,
                        now,
                        claim_token,
                        now,
                        task_id,
                        previous_status,
                    ),
                )
                if upd.rowcount == 0:
                    continue

                task = await self._get_task_for_update(task_id)
                if not task:
                    continue

                files = _uniq_nonempty(task.get("required_files", []))
                conflict = False
                for file_path in files:
                    ins = await self.db.execute(
                        """
                        INSERT OR IGNORE INTO control_task_file_ownership (
                            file_path, owning_task, claim_token, claimed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (file_path, task_id, claim_token, now),
                    )
                    if ins.rowcount == 0:
                        async with self.db.execute(
                            "SELECT owning_task FROM control_task_file_ownership WHERE file_path = ?",
                            (file_path,),
                        ) as cur:
                            owner_row = await cur.fetchone()
                        owner = owner_row[0] if owner_row else None
                        if owner != task_id:
                            conflict = True
                            break

                if conflict:
                    await self.db.execute(
                        "DELETE FROM control_task_file_ownership WHERE owning_task = ? AND claim_token = ?",
                        (task_id, claim_token),
                    )
                    await self.db.execute(
                        """
                        UPDATE control_tasks
                        SET status = ?, locked_by = NULL, locked_at = NULL, claim_token = NULL, updated_at = ?
                        WHERE id = ? AND claim_token = ?
                        """,
                        (previous_status, now, task_id, claim_token),
                    )
                    await self._append_event(
                        task_id=task_id,
                        event_type="claim_conflict",
                        from_status=TASK_STATE_CLAIMED,
                        to_status=previous_status,
                        worker_id=worker_id,
                        claim_token=claim_token,
                        message="Claim reverted due to required-file ownership conflict.",
                    )
                    continue

                await self._append_event(
                    task_id=task_id,
                    event_type="claimed",
                    from_status=previous_status,
                    to_status=TASK_STATE_CLAIMED,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    message="Task claimed.",
                )
                await self.db.commit()
                return await self.get_task(task_id)

            await self.db.commit()
            return None
        except Exception:
            await self.db.rollback()
            raise

    async def mark_task_running(
        self,
        *,
        task_id: str,
        worker_id: str,
        claim_token: str,
    ) -> bool:
        """Transition a claimed task to running."""
        now = _iso_now()
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT status, locked_by, claim_token FROM control_tasks WHERE id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await self.db.rollback()
                return False

            status = _normalize_status(row[0])
            if row[1] != worker_id or row[2] != claim_token:
                await self.db.rollback()
                return False
            if TASK_STATE_RUNNING not in LEGAL_TASK_TRANSITIONS.get(status, set()):
                await self.db.rollback()
                return False

            await self.db.execute(
                """
                UPDATE control_tasks
                SET status = ?, updated_at = ?
                WHERE id = ? AND claim_token = ?
                """,
                (TASK_STATE_RUNNING, now, task_id, claim_token),
            )
            await self._append_event(
                task_id=task_id,
                event_type="running",
                from_status=status,
                to_status=TASK_STATE_RUNNING,
                worker_id=worker_id,
                claim_token=claim_token,
                message="Task execution started.",
            )
            await self.db.commit()
            return True
        except Exception:
            await self.db.rollback()
            raise

    async def complete_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        claim_token: str,
        success: bool,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        now = _iso_now()
        next_status = TASK_STATE_SUCCEEDED if success else TASK_STATE_FAILED
        event_type = "succeeded" if success else "failed"

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT status, locked_by, claim_token FROM control_tasks WHERE id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await self.db.rollback()
                return False

            status = _normalize_status(row[0])
            if row[1] != worker_id or row[2] != claim_token:
                await self.db.rollback()
                return False
            if next_status not in LEGAL_TASK_TRANSITIONS.get(status, set()):
                await self.db.rollback()
                return False

            await self.db.execute(
                """
                UPDATE control_tasks
                SET status = ?,
                    result = ?,
                    error = ?,
                    completed_at = ?,
                    updated_at = ?,
                    locked_by = NULL,
                    locked_at = NULL,
                    claim_token = NULL
                WHERE id = ? AND claim_token = ?
                """,
                (
                    next_status,
                    json.dumps(result or {}),
                    (error or "")[:2000],
                    now,
                    now,
                    task_id,
                    claim_token,
                ),
            )
            await self.db.execute(
                "DELETE FROM control_task_file_ownership WHERE owning_task = ?",
                (task_id,),
            )
            await self._append_event(
                task_id=task_id,
                event_type=event_type,
                from_status=status,
                to_status=next_status,
                worker_id=worker_id,
                claim_token=claim_token,
                message="Task finished." if success else "Task failed.",
                payload={"success": success, "error": error or ""},
            )
            await self.db.commit()
            return True
        except Exception:
            await self.db.rollback()
            raise

    async def release_claim(
        self,
        *,
        task_id: str,
        worker_id: str,
        claim_token: str,
        reason: str = "",
        back_to_pending: bool = True,
    ) -> bool:
        """
        Release a task claim.

        Legacy argument `back_to_pending` maps to:
        - True  -> `released` (eligible for future claim)
        - False -> `failed`
        """
        now = _iso_now()
        next_status = TASK_STATE_RELEASED if back_to_pending else TASK_STATE_FAILED
        event_type = "released" if back_to_pending else "failed"

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT status, locked_by, claim_token FROM control_tasks WHERE id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await self.db.rollback()
                return False

            status = _normalize_status(row[0])
            if row[1] != worker_id or row[2] != claim_token:
                await self.db.rollback()
                return False
            if next_status not in LEGAL_TASK_TRANSITIONS.get(status, set()):
                await self.db.rollback()
                return False

            completed_at = now if next_status in TERMINAL_TASK_STATES else None
            await self.db.execute(
                """
                UPDATE control_tasks
                SET status = ?,
                    error = ?,
                    completed_at = ?,
                    updated_at = ?,
                    locked_by = NULL,
                    locked_at = NULL,
                    claim_token = NULL
                WHERE id = ? AND claim_token = ?
                """,
                (
                    next_status,
                    reason[:2000],
                    completed_at,
                    now,
                    task_id,
                    claim_token,
                ),
            )
            await self.db.execute(
                "DELETE FROM control_task_file_ownership WHERE owning_task = ?",
                (task_id,),
            )
            await self._append_event(
                task_id=task_id,
                event_type=event_type,
                from_status=status,
                to_status=next_status,
                worker_id=worker_id,
                claim_token=claim_token,
                message=(reason or "Task claim released.")[:2000],
            )
            await self.db.commit()
            return True
        except Exception:
            await self.db.rollback()
            raise

    async def mark_failed_timeout(
        self,
        *,
        task_id: str,
        worker_id: str,
        claim_token: str,
        reason: str = "Task lock exceeded TTL and worker/gateway health checks failed.",
    ) -> bool:
        """Mark a stale claim as failed due to timeout."""
        now = _iso_now()
        next_status = TASK_STATE_FAILED_TIMEOUT

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT status, locked_by, claim_token FROM control_tasks WHERE id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await self.db.rollback()
                return False

            status = _normalize_status(row[0])
            if row[1] != worker_id or row[2] != claim_token:
                await self.db.rollback()
                return False
            if next_status not in LEGAL_TASK_TRANSITIONS.get(status, set()):
                await self.db.rollback()
                return False

            await self.db.execute(
                """
                UPDATE control_tasks
                SET status = ?,
                    error = ?,
                    completed_at = ?,
                    updated_at = ?,
                    locked_by = NULL,
                    locked_at = NULL,
                    claim_token = NULL
                WHERE id = ? AND claim_token = ?
                """,
                (next_status, reason[:2000], now, now, task_id, claim_token),
            )
            await self.db.execute(
                "DELETE FROM control_task_file_ownership WHERE owning_task = ?",
                (task_id,),
            )
            await self._append_event(
                task_id=task_id,
                event_type="failed_timeout",
                from_status=status,
                to_status=next_status,
                worker_id=worker_id,
                claim_token=claim_token,
                message=reason[:2000],
            )
            await self.db.commit()
            return True
        except Exception:
            await self.db.rollback()
            raise

    async def claim_file(
        self,
        *,
        task_id: str,
        claim_token: str,
        file_path: str,
    ) -> tuple[bool, str | None]:
        """
        Explicitly claim a file for an active task.

        Returns (ok, owner_task_id_if_conflict).
        """
        file_path = str(file_path).strip()
        if not file_path:
            return False, None

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                "SELECT status, claim_token FROM control_tasks WHERE id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await self.db.rollback()
                return False, None

            status = _normalize_status(row[0])
            if status not in ACTIVE_TASK_STATES or row[1] != claim_token:
                await self.db.rollback()
                return False, None

            ins = await self.db.execute(
                """
                INSERT OR IGNORE INTO control_task_file_ownership (
                    file_path, owning_task, claim_token, claimed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (file_path, task_id, claim_token, _iso_now()),
            )
            if ins.rowcount > 0:
                await self.db.commit()
                return True, task_id

            async with self.db.execute(
                "SELECT owning_task FROM control_task_file_ownership WHERE file_path = ?",
                (file_path,),
            ) as cur:
                owner_row = await cur.fetchone()
            owner = owner_row[0] if owner_row else None
            await self.db.rollback()
            return owner == task_id, owner
        except Exception:
            await self.db.rollback()
            raise

    async def release_files_for_task(self, task_id: str) -> int:
        cur = await self.db.execute(
            "DELETE FROM control_task_file_ownership WHERE owning_task = ?",
            (task_id,),
        )
        await self.db.commit()
        return cur.rowcount

    async def list_file_ownership(self) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT file_path, owning_task, claim_token, claimed_at FROM control_task_file_ownership ORDER BY file_path"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def list_task_events(
        self,
        *,
        task_id: str | None = None,
        limit: int = 200,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if task_id:
            where.append("task_id = ?")
            params.append(task_id)
        if since:
            where.append("created_at >= ?")
            params.append(since)

        sql = (
            "SELECT id, task_id, event_type, from_status, to_status, worker_id, "
            "claim_token, message, payload, created_at "
            "FROM control_task_events"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))

        async with self.db.execute(sql, tuple(params)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        rows.reverse()
        for row in rows:
            row["payload"] = _json_loads_dict(row.get("payload"))
        return rows

    async def list_active_assignments(self, *, limit: int = 500) -> list[dict[str, Any]]:
        async with self.db.execute(
            """
            SELECT
                id AS task_id,
                action,
                status,
                locked_by AS agent_id,
                locked_at,
                gateway_id,
                claim_token,
                updated_at
            FROM control_tasks
            WHERE status IN (?, ?) AND locked_by IS NOT NULL
            ORDER BY locked_at ASC
            LIMIT ?
            """,
            (TASK_STATE_CLAIMED, TASK_STATE_RUNNING, int(limit)),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def list_stale_locked_tasks(self, *, ttl_seconds: int | None = None) -> list[dict[str, Any]]:
        ttl = int(ttl_seconds or self.lock_timeout_seconds)
        now_dt = _utc_now()
        stale: list[dict[str, Any]] = []

        async with self.db.execute(
            """
            SELECT *
            FROM control_tasks
            WHERE status IN (?, ?)
              AND locked_by IS NOT NULL
              AND locked_at IS NOT NULL
            """,
            (TASK_STATE_CLAIMED, TASK_STATE_RUNNING),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        for row in rows:
            locked_at_dt = _parse_iso(row.get("locked_at"))
            if not locked_at_dt:
                continue
            if locked_at_dt + timedelta(seconds=ttl) <= now_dt:
                stale.append(self._row_to_task(row))
        return stale

    async def _files_unowned(self, *, task_id: str, files: list[str]) -> bool:
        for file_path in files:
            async with self.db.execute(
                "SELECT owning_task FROM control_task_file_ownership WHERE file_path = ?",
                (file_path,),
            ) as cur:
                owner_row = await cur.fetchone()
            owner = owner_row[0] if owner_row else None
            if owner and owner != task_id:
                return False
        return True

    async def _task_status_map(self) -> dict[str, str]:
        async with self.db.execute("SELECT id, status FROM control_tasks") as cur:
            rows = await cur.fetchall()
        return {str(r[0]): _normalize_status(str(r[1])) for r in rows}

    async def _get_task_for_update(self, task_id: str) -> dict[str, Any] | None:
        async with self.db.execute("SELECT * FROM control_tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
        return self._row_to_task(dict(row)) if row else None

    async def _append_event(
        self,
        *,
        task_id: str,
        event_type: str,
        from_status: str | None = None,
        to_status: str | None = None,
        worker_id: str | None = None,
        claim_token: str | None = None,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO control_task_events (
                task_id, event_type, from_status, to_status,
                worker_id, claim_token, message, payload, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                event_type,
                from_status,
                to_status,
                worker_id,
                claim_token,
                message[:2000],
                json.dumps(payload or {}),
                _iso_now(),
            ),
        )

    async def _graph_has_cycle(self) -> bool:
        async with self.db.execute("SELECT id, dependencies FROM control_tasks") as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        graph: dict[str, list[str]] = {}
        for row in rows:
            graph[str(row["id"])] = [str(x) for x in _json_loads_list(row.get("dependencies"))]

        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> bool:
            if node in visited:
                return False
            if node in visiting:
                return True
            visiting.add(node)
            for nxt in graph.get(node, []):
                if nxt not in graph:
                    return True
                if dfs(nxt):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        for node in list(graph.keys()):
            if dfs(node):
                return True
        return False

    def _row_to_task(self, row: dict[str, Any]) -> dict[str, Any]:
        row["status"] = _normalize_status(row.get("status"))
        row["params"] = _json_loads_dict(row.get("params"))
        row["dependencies"] = _json_loads_list(row.get("dependencies"))
        row["dependents"] = _json_loads_list(row.get("dependents"))
        row["required_files"] = _json_loads_list(row.get("required_files"))
        row["result"] = _json_loads_dict(row.get("result"))
        return row
