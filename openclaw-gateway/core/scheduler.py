from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from db import store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class CodingJob:
    id: str
    project_id: str
    requested_by: str
    instructions: str
    status: str = "queued"
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    error: str = ""


class BackgroundScheduler:
    """Background dispatcher for coding/execution jobs."""

    def __init__(self, *, project_manager: Any | None):
        self._project_manager = project_manager
        self._queue: asyncio.Queue[CodingJob] = asyncio.Queue()
        self._jobs: dict[str, CodingJob] = {}
        self._worker: asyncio.Task[None] | None = None

    async def enqueue_coding_job(self, *, project_id: str, requested_by: str, instructions: str) -> str:
        job = CodingJob(
            id=f"job_{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            requested_by=requested_by,
            instructions=instructions,
        )
        self._jobs[job.id] = job
        await self._queue.put(job)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="background-scheduler")
        return job.id

    def get_job(self, job_id: str) -> CodingJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[CodingJob]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at)

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._execute_job(job)
            finally:
                self._queue.task_done()

    async def _execute_job(self, job: CodingJob) -> None:
        job.status = "running"
        job.started_at = _now_iso()

        pm = self._project_manager
        if pm is None:
            job.status = "failed"
            job.error = "project manager unavailable"
            job.finished_at = _now_iso()
            return

        try:
            project = await store.get_project(pm.db, job.project_id)
            if not project:
                raise ValueError("project not found")

            status = str(project.get("status") or "")
            if status in {"ideation", "planning"}:
                plan = await store.get_active_plan(pm.db, job.project_id)
                if not plan and hasattr(pm, "generate_plan"):
                    await pm.generate_plan(job.project_id)
                if hasattr(pm, "approve_plan"):
                    await pm.approve_plan(job.project_id)

            if hasattr(pm, "start_execution"):
                await pm.start_execution(job.project_id)

            await store.add_event(
                pm.db,
                job.project_id,
                "coding_job_started",
                f"Background coding job {job.id} started",
                detail=job.instructions[:500],
            )
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            try:
                await store.add_event(
                    pm.db,
                    job.project_id,
                    "coding_job_failed",
                    f"Background coding job {job.id} failed",
                    detail=str(exc)[:1000],
                )
            except Exception:
                pass
        finally:
            job.finished_at = _now_iso()