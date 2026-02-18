"""
System State Monitors - Track system health and opportunities.

Monitors:
- System idle state
- Active jobs
- Recent errors
- Resource usage
- Optimization opportunities
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from skynet.core.orchestrator import Orchestrator
    from skynet.memory.memory_manager import MemoryManager
    from skynet.ledger.worker_registry import WorkerRegistry

logger = logging.getLogger("skynet.cognition.monitors")


@dataclass
class SystemState:
    """
    Snapshot of system state.

    Used by InitiativeEngine to decide when to take action.
    """

    # System activity
    is_idle: bool
    idle_duration_seconds: float
    active_jobs: int
    pending_jobs: int

    # Error state
    pending_errors: list[dict[str, Any]] = field(default_factory=list)
    failure_rate_24h: float = 0.0

    # Resource state
    disk_usage_percent: float = 0.0
    memory_usage_percent: float = 0.0

    # Maintenance tracking
    last_maintenance: datetime | None = None
    hours_since_maintenance: float = 0.0

    # Optimization opportunities
    optimization_opportunities: list[str] = field(default_factory=list)

    # Worker state
    online_workers: int = 0
    busy_workers: int = 0

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"SystemState(idle={self.is_idle}, "
            f"active_jobs={self.active_jobs}, "
            f"errors={len(self.pending_errors)}, "
            f"workers={self.online_workers}/{self.busy_workers})"
        )


class SystemStateMonitor:
    """
    Monitors system state for autonomous decision-making.

    Gathers data from:
    - Orchestrator (job status)
    - MemoryManager (error history)
    - WorkerRegistry (worker status)
    - System metrics (disk, memory)
    """

    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        memory_manager: MemoryManager | None = None,
        worker_registry: WorkerRegistry | None = None,
        idle_threshold_seconds: float = 300,
    ):
        self.orchestrator = orchestrator
        self.memory_manager = memory_manager
        self.worker_registry = worker_registry
        self.idle_threshold_seconds = idle_threshold_seconds

        self._last_activity_time = datetime.utcnow()
        self._last_maintenance_time: datetime | None = None

        logger.info("SystemStateMonitor initialized")

    async def assess_system_state(self) -> SystemState:
        """Assess current system state."""
        active_jobs = await self._count_active_jobs()
        pending_jobs = await self._count_pending_jobs()

        now = datetime.utcnow()
        idle_duration = (now - self._last_activity_time).total_seconds()
        is_idle = active_jobs == 0 and idle_duration >= self.idle_threshold_seconds

        pending_errors = await self._get_pending_errors()
        failure_rate = await self._calculate_failure_rate()

        hours_since_maintenance = 0.0
        if self._last_maintenance_time:
            hours_since_maintenance = (
                now - self._last_maintenance_time
            ).total_seconds() / 3600.0

        online_workers, busy_workers = await self._get_worker_state()
        opportunities = await self._find_optimization_opportunities()

        state = SystemState(
            is_idle=is_idle,
            idle_duration_seconds=idle_duration,
            active_jobs=active_jobs,
            pending_jobs=pending_jobs,
            pending_errors=pending_errors,
            failure_rate_24h=failure_rate,
            last_maintenance=self._last_maintenance_time,
            hours_since_maintenance=hours_since_maintenance,
            optimization_opportunities=opportunities,
            online_workers=online_workers,
            busy_workers=busy_workers,
        )

        logger.debug(f"System state: {state}")
        return state

    def mark_activity(self) -> None:
        """Mark that system activity occurred."""
        self._last_activity_time = datetime.utcnow()
        logger.debug("System activity marked")

    def mark_maintenance(self) -> None:
        """Mark that maintenance was performed."""
        self._last_maintenance_time = datetime.utcnow()
        logger.info("Maintenance timestamp recorded")

    async def _count_active_jobs(self) -> int:
        """Count currently executing jobs."""
        if not self.orchestrator:
            return 0

        try:
            jobs = await self.orchestrator.list_jobs()
            return sum(1 for job in jobs if job.get("status") == "running")
        except Exception as e:
            logger.warning(f"Failed to count active jobs: {e}")
            return 0

    async def _count_pending_jobs(self) -> int:
        """Count jobs waiting for approval or execution."""
        if not self.orchestrator:
            return 0

        try:
            jobs = await self.orchestrator.list_jobs()
            pending_states = {"created", "planned", "queued"}
            return sum(1 for job in jobs if job.get("status") in pending_states)
        except Exception as e:
            logger.warning(f"Failed to count pending jobs: {e}")
            return 0

    async def _get_pending_errors(self) -> list[dict[str, Any]]:
        """Get recent errors that have not been addressed."""
        if not self.memory_manager:
            return []

        try:
            since = datetime.utcnow() - timedelta(hours=24)
            failures = await self.memory_manager.get_recent_failures(since=since, limit=10)
            return [
                {
                    "error_type": f.content.get("error_type", "Unknown"),
                    "error_message": f.content.get("error_message", ""),
                    "timestamp": f.timestamp.isoformat(),
                }
                for f in failures
            ]
        except Exception as e:
            logger.warning(f"Failed to get pending errors: {e}")
            return []

    async def _calculate_failure_rate(self) -> float:
        """Calculate failure rate over last 24 hours."""
        if not self.memory_manager:
            return 0.0

        try:
            failures = await self.get_recent_failure_count(hours=24)
            executions = await self.get_recent_execution_count(hours=24)
            if executions <= 0:
                return 0.0
            return min(1.0, failures / executions)
        except Exception as e:
            logger.warning(f"Failed to calculate failure rate: {e}")
            return 0.0

    async def _get_worker_state(self) -> tuple[int, int]:
        """Get worker state as (online_workers, busy_workers)."""
        if not self.worker_registry:
            return (0, 0)

        try:
            online = await self.worker_registry.get_online_workers()
            online_workers = len(online)
            busy_workers = 0

            async with self.worker_registry.db.execute(
                "SELECT COUNT(*) AS cnt FROM workers WHERE status = 'busy'"
            ) as cur:
                row = await cur.fetchone()
                if row:
                    busy_workers = int(row["cnt"])

            return (online_workers, busy_workers)
        except Exception as e:
            logger.warning(f"Failed to get worker state: {e}")
            return (0, 0)

    async def _find_optimization_opportunities(self) -> list[str]:
        """Identify potential optimization opportunities."""
        opportunities: list[str] = []

        failure_rate = await self._calculate_failure_rate()
        if failure_rate >= 0.2:
            opportunities.append("High recent failure rate; review flaky steps and retries")

        if self.worker_registry:
            online_workers, busy_workers = await self._get_worker_state()
            if online_workers > 0 and busy_workers == online_workers:
                opportunities.append("All workers are busy; consider scaling worker pool")

        if self.memory_manager:
            try:
                strategies = await self.memory_manager.get_success_strategies(limit=3)
                if strategies:
                    opportunities.append("Recent successful patterns available for reuse")
            except Exception as e:
                logger.debug(f"Could not evaluate success strategy opportunities: {e}")

        if (
            not opportunities
            and os.getenv("SKYNET_ENABLE_BASELINE_OPT_OPPORTUNITY", "true").lower() == "true"
        ):
            opportunities.append("Run periodic optimization review for proactive tuning")

        return opportunities

    async def get_recent_failure_count(self, hours: int = 24) -> int:
        """Get number of failures in the recent window."""
        if not self.memory_manager:
            return 0
        since = datetime.utcnow() - timedelta(hours=hours)
        failures = await self.memory_manager.get_recent_failures(since=since, limit=1000)
        return len(failures)

    async def get_recent_execution_count(self, hours: int = 24) -> int:
        """Estimate total executions in the recent window from memory records."""
        if not self.memory_manager:
            return 0
        since = datetime.utcnow() - timedelta(hours=hours)
        memories = await self.memory_manager.storage.search_memories(limit=2000)
        return sum(1 for mem in memories if mem.timestamp >= since)