"""
SKYNET Core — Orchestrator

The main coordinator that manages job lifecycle and orchestrates all components.
This is the brain's control loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import aiosqlite

from skynet.policy.engine import PolicyEngine
from skynet.ledger.models import Job, JobStatus, RiskLevel
from skynet.chathan.protocol.plan_spec import PlanSpec
from skynet.events import Event, EventType

if TYPE_CHECKING:
    from skynet.core.planner import Planner
    from skynet.core.dispatcher import Dispatcher
    from skynet.events import EventEngine

logger = logging.getLogger("skynet.core.orchestrator")


class Orchestrator:
    """
    Main coordinator for SKYNET job lifecycle.

    Manages the complete flow:
    1. User submits intent → CREATED
    2. Generate plan → PLANNED
    3. User approves → QUEUED (via dispatcher)
    4. Worker executes → RUNNING → SUCCEEDED/FAILED

    Example:
        orchestrator = Orchestrator(planner, dispatcher, policy_engine)
        job_id = await orchestrator.create_task("deploy bot to production", "proj_123")
        plan = await orchestrator.generate_plan(job_id)
        # User reviews plan...
        await orchestrator.approve_plan(job_id)
    """

    def __init__(
        self,
        planner: Planner,
        dispatcher: Dispatcher,
        policy_engine: PolicyEngine,
        ledger_db: aiosqlite.Connection | None = None,
        event_engine: EventEngine | None = None,
    ):
        """
        Initialize the Orchestrator.

        Args:
            planner: Component that generates plans from user intent
            dispatcher: Component that converts plans to execution specs
            policy_engine: Safety and risk validation engine
            ledger_db: Database connection for persistence (optional)
            event_engine: Event engine for reactive intelligence (optional)
        """
        self.planner = planner
        self.dispatcher = dispatcher
        self.policy_engine = policy_engine
        self.ledger_db = ledger_db
        self.event_engine = event_engine

        # In-memory job store (will be replaced with database in Phase 2)
        self._jobs: dict[str, Job] = {}

        # Approval futures for blocking wait_for_approval
        self._approval_futures: dict[str, asyncio.Future] = {}

        logger.info(
            "Orchestrator initialized (db_persistence=%s, events=%s)",
            self.ledger_db is not None,
            self.event_engine is not None,
        )

    async def create_task(
        self,
        user_intent: str,
        project_id: str = "default",
    ) -> str:
        """
        Create a new task from user intent.

        Args:
            user_intent: User's natural language task description
            project_id: Project identifier

        Returns:
            job_id for tracking the task
        """
        # Generate unique job ID
        job_id = f"job_{uuid.uuid4().hex[:12]}"

        # Create Job object
        job = Job(
            id=job_id,
            project_id=project_id,
            status=JobStatus.CREATED,
            user_intent=user_intent,
        )

        # Store in memory cache
        self._jobs[job_id] = job
        await self._save_job(job)

        logger.info(f"Task created: {job_id} - {user_intent[:50]}...")

        # Publish TASK_CREATED event
        if self.event_engine:
            try:
                await self.event_engine.publish(
                    Event(
                        type=EventType.TASK_CREATED,
                        payload={
                            "job_id": job_id,
                            "user_intent": user_intent,
                            "project_id": project_id,
                        },
                        source="orchestrator",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to publish TASK_CREATED event: {e}")

        return job_id

    async def generate_plan(self, job_id: str) -> dict[str, Any]:
        """
        Generate a plan for the job using AI.

        Args:
            job_id: Job identifier

        Returns:
            PlanSpec dictionary for user approval

        Raises:
            ValueError: If job_id not found
        """
        # Load job from ledger/cache
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.status != JobStatus.CREATED:
            raise ValueError(f"Job {job_id} already has plan (status={job.status})")

        logger.info(f"Generating plan for job {job_id}...")

        # Call planner to generate plan (returns dictionary)
        plan_dict = await self.planner.generate_plan(
            job_id=job_id,
            user_intent=job.user_intent,
            context={"project_id": job.project_id},
        )

        # Convert AI plan dictionary to PlanSpec object
        plan_spec = PlanSpec.from_ai_plan(
            project_id=job.project_id,
            job_id=job_id,
            plan_dict=plan_dict,
        )

        # Classify risk level using policy engine
        risk_level = self.policy_engine.classify_risk(plan_spec)

        # Determine if approval required
        approval_required = risk_level in ["WRITE", "ADMIN"]

        # Update job with plan
        job.plan_spec = plan_spec.to_dict()  # Store as dictionary
        job.risk_level = RiskLevel(risk_level)  # Convert string to enum
        job.approval_required = approval_required
        job.status = JobStatus.PLANNED
        job.updated_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"Plan generated for job {job_id}: "
            f"{len(plan_spec.steps)} steps, "
            f"risk={risk_level}, "
            f"approval_required={approval_required}"
        )
        await self._save_job(job)

        # Publish TASK_PLANNED event
        if self.event_engine:
            try:
                await self.event_engine.publish(
                    Event(
                        type=EventType.TASK_PLANNED,
                        payload={
                            "job_id": job_id,
                            "plan": plan_spec.to_dict(),
                            "risk_level": risk_level,
                            "approval_required": approval_required,
                            "steps_count": len(plan_spec.steps),
                        },
                        source="orchestrator",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to publish TASK_PLANNED event: {e}")

        return plan_spec.to_dict()  # Return as dictionary for compatibility

    async def approve_plan(self, job_id: str) -> None:
        """
        Approve a plan and dispatch for execution.

        Args:
            job_id: Job identifier

        Raises:
            ValueError: If job_id not found or not in PLANNED status
        """
        # Load job from ledger/cache
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.status != JobStatus.PLANNED:
            raise ValueError(f"Job {job_id} not ready for approval (status={job.status})")

        logger.info(f"Approving job {job_id}...")

        # Mark as approved
        job.approved_at = datetime.now(timezone.utc).isoformat()

        # Dispatch job (converts PlanSpec → ExecutionSpec, enqueues)
        execution_spec = await self.dispatcher.dispatch(job_id, job.plan_spec)
        job.execution_spec = execution_spec.to_dict()

        # Update status to QUEUED
        job.status = JobStatus.QUEUED
        job.queued_at = datetime.now(timezone.utc).isoformat()
        job.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save_job(job)

        logger.info(f"Job {job_id} approved and queued for execution")

        # Publish TASK_APPROVED and TASK_QUEUED events
        if self.event_engine:
            try:
                await self.event_engine.publish(
                    Event(
                        type=EventType.TASK_APPROVED,
                        payload={
                            "job_id": job_id,
                            "execution_spec": execution_spec.to_dict(),
                        },
                        source="orchestrator",
                    )
                )
                await self.event_engine.publish(
                    Event(
                        type=EventType.TASK_QUEUED,
                        payload={
                            "job_id": job_id,
                            "provider": execution_spec.provider,
                        },
                        source="orchestrator",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to publish approval events: {e}")

        # Resolve approval future if waiting
        if job_id in self._approval_futures:
            self._approval_futures[job_id].set_result(True)
            del self._approval_futures[job_id]

    async def deny_plan(self, job_id: str, reason: str = "User denied") -> None:
        """
        Deny a plan and cancel the job.

        Args:
            job_id: Job identifier
            reason: Reason for denial

        Raises:
            ValueError: If job_id not found
        """
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        logger.info(f"Denying job {job_id}: {reason}")

        job.status = JobStatus.CANCELLED
        job.error_message = reason
        job.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save_job(job)

        # Publish TASK_DENIED event
        if self.event_engine:
            try:
                await self.event_engine.publish(
                    Event(
                        type=EventType.TASK_DENIED,
                        payload={
                            "job_id": job_id,
                            "reason": reason,
                        },
                        source="orchestrator",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to publish TASK_DENIED event: {e}")

        # Resolve approval future if waiting
        if job_id in self._approval_futures:
            self._approval_futures[job_id].set_result(False)
            del self._approval_futures[job_id]

    async def cancel_job(self, job_id: str) -> None:
        """
        Cancel a job at any stage.

        Args:
            job_id: Job identifier

        Raises:
            ValueError: If job_id not found
        """
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        logger.info(f"Cancelling job {job_id} (current status={job.status})")
        previous_status = job.status

        # Update status
        job.status = JobStatus.CANCELLED
        job.updated_at = datetime.now(timezone.utc).isoformat()

        # Best-effort cancellation in queue/executor layer.
        try:
            from skynet.queue.celery_app import cancel_job as cancel_enqueued_job

            if previous_status in (JobStatus.QUEUED, JobStatus.RUNNING):
                cancel_enqueued_job(job_id)
                logger.info(f"Cancellation signal sent for job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to send cancellation signal for {job_id}: {e}")

        await self._save_job(job)

        logger.info(f"Job {job_id} cancelled")

        # Publish TASK_CANCELLED event
        if self.event_engine:
            try:
                await self.event_engine.publish(
                    Event(
                        type=EventType.TASK_CANCELLED,
                        payload={
                            "job_id": job_id,
                            "previous_status": previous_status.value if hasattr(previous_status, "value") else str(previous_status),
                        },
                        source="orchestrator",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to publish TASK_CANCELLED event: {e}")

    async def get_status(self, job_id: str) -> dict[str, Any]:
        """
        Get current status of a job.

        Args:
            job_id: Job identifier

        Returns:
            Job status dictionary

        Raises:
            ValueError: If job_id not found
        """
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        return job.to_dict()

    async def wait_for_approval(
        self,
        job_id: str,
        timeout: int = 300,
    ) -> bool:
        """
        Block until user approves or denies the plan.

        Used by Telegram bot to wait for user interaction.

        Args:
            job_id: Job identifier
            timeout: Maximum wait time in seconds

        Returns:
            True if approved, False if denied or timeout

        Raises:
            ValueError: If job_id not found
        """
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.status != JobStatus.PLANNED:
            raise ValueError(f"Job {job_id} not awaiting approval (status={job.status})")

        logger.info(f"Waiting for approval of job {job_id} (timeout={timeout}s)")

        # Create future for approval
        future = asyncio.Future()
        self._approval_futures[job_id] = future

        try:
            # Wait with timeout
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Approval timeout for job {job_id}")
            # Auto-cancel on timeout
            job.status = JobStatus.CANCELLED
            job.error_message = "Approval timeout"
            job.updated_at = datetime.now(timezone.utc).isoformat()
            await self._save_job(job)
            return False
        finally:
            # Clean up future
            if job_id in self._approval_futures:
                del self._approval_futures[job_id]

    async def list_jobs(
        self,
        project_id: str | None = None,
        status: JobStatus | None = None,
    ) -> list[dict[str, Any]]:
        """
        List all jobs, optionally filtered.

        Args:
            project_id: Filter by project ID
            status: Filter by job status

        Returns:
            List of job dictionaries
        """
        if self.ledger_db:
            jobs = await self._list_jobs_from_db(project_id=project_id, status=status)
            return [j.to_dict() for j in jobs]

        jobs = list(self._jobs.values())

        if project_id:
            jobs = [j for j in jobs if j.project_id == project_id]

        if status:
            jobs = [j for j in jobs if j.status == status]

        # Sort by created_at descending
        jobs.sort(key=lambda j: j.created_at, reverse=True)

        return [j.to_dict() for j in jobs]

    async def _get_job(self, job_id: str) -> Job | None:
        """Fetch job from cache, then DB if available."""
        job = self._jobs.get(job_id)
        if job:
            return job
        if not self.ledger_db:
            return None

        async with self.ledger_db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            job = self._row_to_job(dict(row))
            self._jobs[job.id] = job
            return job

    async def _save_job(self, job: Job) -> None:
        """Persist job to DB when configured."""
        self._jobs[job.id] = job
        if not self.ledger_db:
            return

        await self.ledger_db.execute(
            """
            INSERT INTO jobs (
                id, project_id, status, user_intent, plan_spec, execution_spec,
                provider, worker_id, risk_level, approval_required, approved_at,
                queued_at, started_at, completed_at, error_message, result_summary,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_id = excluded.project_id,
                status = excluded.status,
                user_intent = excluded.user_intent,
                plan_spec = excluded.plan_spec,
                execution_spec = excluded.execution_spec,
                provider = excluded.provider,
                worker_id = excluded.worker_id,
                risk_level = excluded.risk_level,
                approval_required = excluded.approval_required,
                approved_at = excluded.approved_at,
                queued_at = excluded.queued_at,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                error_message = excluded.error_message,
                result_summary = excluded.result_summary,
                updated_at = excluded.updated_at
            """,
            (
                job.id,
                job.project_id,
                job.status.value,
                job.user_intent,
                json.dumps(job.plan_spec),
                json.dumps(job.execution_spec),
                job.provider,
                job.worker_id,
                job.risk_level.value,
                int(job.approval_required),
                job.approved_at,
                job.queued_at,
                job.started_at,
                job.completed_at,
                job.error_message,
                job.result_summary,
                job.created_at,
                job.updated_at,
            ),
        )
        await self.ledger_db.commit()

    async def _list_jobs_from_db(
        self,
        project_id: str | None = None,
        status: JobStatus | None = None,
    ) -> list[Job]:
        """List jobs from DB with optional filters."""
        where: list[str] = []
        params: list[Any] = []
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if status:
            where.append("status = ?")
            params.append(status.value)

        query = "SELECT * FROM jobs"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC"

        async with self.ledger_db.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
            jobs = [self._row_to_job(dict(row)) for row in rows]
            for job in jobs:
                self._jobs[job.id] = job
            return jobs

    def _row_to_job(self, row: dict[str, Any]) -> Job:
        """Convert DB row into Job model."""
        row["plan_spec"] = json.loads(row.get("plan_spec") or "{}")
        row["execution_spec"] = json.loads(row.get("execution_spec") or "{}")
        row["approval_required"] = bool(row.get("approval_required", 1))
        return Job.from_dict(row)
