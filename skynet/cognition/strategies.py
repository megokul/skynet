"""
Initiative Strategies — Rules for autonomous task generation.

Defines when and how SKYNET should take initiative:
- Maintenance: Regular system upkeep
- Recovery: Error recovery and failure response
- Optimization: Performance and efficiency improvements
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from .monitors import SystemState

logger = logging.getLogger("skynet.cognition.strategies")


class InitiativeStrategy(ABC):
    """
    Base class for initiative strategies.

    Strategies decide:
    - When to trigger (should_trigger)
    - What task to generate (generate_task_description)
    """

    @abstractmethod
    async def should_trigger(self, state: SystemState) -> bool:
        """
        Check if strategy should trigger based on system state.

        Args:
            state: Current system state

        Returns:
            True if strategy should trigger, False otherwise
        """
        pass

    @abstractmethod
    async def generate_task_description(self, state: SystemState) -> str:
        """
        Generate task description for autonomous execution.

        Args:
            state: Current system state

        Returns:
            Natural language task description for Planner
        """
        pass

    def get_name(self) -> str:
        """Get strategy name."""
        return self.__class__.__name__


class MaintenanceStrategy(InitiativeStrategy):
    """
    Maintenance strategy - regular system upkeep.

    Triggers when:
    - System has been idle for sufficient time
    - Maintenance hasn't been performed recently
    - No active jobs

    Performs:
    - Log cleanup
    - Dependency update checks
    - Database optimization
    - Health checks
    """

    def __init__(
        self,
        maintenance_interval_hours: float = 24.0,
        min_idle_time_seconds: float = 300.0,  # 5 minutes
    ):
        """
        Initialize maintenance strategy.

        Args:
            maintenance_interval_hours: Hours between maintenance runs
            min_idle_time_seconds: Minimum idle time before maintenance
        """
        self.maintenance_interval_hours = maintenance_interval_hours
        self.min_idle_time_seconds = min_idle_time_seconds

    async def should_trigger(self, state: SystemState) -> bool:
        """Trigger if idle and maintenance due."""
        # Must be idle
        if not state.is_idle:
            return False

        # Must have sufficient idle time
        if state.idle_duration_seconds < self.min_idle_time_seconds:
            return False

        # Check maintenance interval
        if state.hours_since_maintenance >= self.maintenance_interval_hours:
            logger.info(
                f"Maintenance due (last maintenance: "
                f"{state.hours_since_maintenance:.1f}h ago)"
            )
            return True

        # First time (no maintenance recorded yet)
        if state.last_maintenance is None and state.is_idle:
            logger.info("No maintenance history, triggering initial maintenance")
            return True

        return False

    async def generate_task_description(self, state: SystemState) -> str:
        """Generate maintenance task description."""
        tasks = [
            "Check for outdated dependencies and log warnings",
            "Generate system health report",
            "List recent error patterns from logs",
        ]

        task_list = "\n".join(f"- {task}" for task in tasks)

        return f"""System Maintenance Tasks:

{task_list}

This is an autonomous READ_ONLY maintenance check.
Report findings but do not make any changes."""


class RecoveryStrategy(InitiativeStrategy):
    """
    Recovery strategy - automatic error recovery.

    Triggers when:
    - Recent errors detected
    - System has capacity to handle recovery
    - Error is recoverable

    Performs:
    - Error analysis
    - Recovery plan generation
    - Retry failed tasks (if safe)
    """

    def __init__(
        self, max_errors_to_handle: int = 3, min_error_age_seconds: float = 60.0
    ):
        """
        Initialize recovery strategy.

        Args:
            max_errors_to_handle: Maximum errors to handle per trigger
            min_error_age_seconds: Minimum age of error before recovery
        """
        self.max_errors_to_handle = max_errors_to_handle
        self.min_error_age_seconds = min_error_age_seconds

    async def should_trigger(self, state: SystemState) -> bool:
        """Trigger if recent errors exist and system has capacity."""
        # Must have errors
        if not state.pending_errors:
            return False

        # Must not be overloaded
        if state.active_jobs > 2:
            logger.debug("System busy, skipping recovery")
            return False

        # Errors should be old enough (not transient).
        oldest_allowed_age = self.min_error_age_seconds
        now = time.time()
        stale_errors = 0
        for err in state.pending_errors:
            timestamp = err.get("timestamp")
            if not timestamp:
                stale_errors += 1
                continue
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                err_age = now - parsed.timestamp()
                if err_age >= oldest_allowed_age:
                    stale_errors += 1
            except Exception:
                stale_errors += 1

        if stale_errors == 0:
            logger.debug("All recent errors look transient; skipping recovery")
            return False

        logger.info(f"Recovery needed for {len(state.pending_errors)} errors")
        return True

    async def generate_task_description(self, state: SystemState) -> str:
        """Generate recovery task description."""
        errors = state.pending_errors[: self.max_errors_to_handle]

        error_list = "\n".join(
            f"- {err.get('error_type', 'Unknown')}: {err.get('error_message', '')}"
            for err in errors
        )

        return f"""Error Recovery Analysis:

Recent errors detected:
{error_list}

Please analyze these errors and suggest recovery actions.
This is a READ_ONLY analysis - do not attempt fixes without approval."""


class OptimizationStrategy(InitiativeStrategy):
    """
    Optimization strategy - performance improvements.

    Triggers when:
    - Optimization opportunities detected
    - System is idle
    - Sufficient time has passed

    Performs:
    - Performance analysis
    - Resource usage review
    - Efficiency recommendations
    """

    def __init__(self, check_interval_hours: float = 48.0):
        """
        Initialize optimization strategy.

        Args:
            check_interval_hours: Hours between optimization checks
        """
        self.check_interval_hours = check_interval_hours
        self._last_check: float = 0.0

    async def should_trigger(self, state: SystemState) -> bool:
        """Trigger if opportunities exist and idle."""
        # Must be idle
        if not state.is_idle:
            return False

        # Must have opportunities
        if not state.optimization_opportunities:
            return False

        now = time.time()
        if self._last_check and now - self._last_check < self.check_interval_hours * 3600:
            return False
        self._last_check = now

        logger.info(
            f"Optimization opportunities: {state.optimization_opportunities}"
        )
        return True

    async def generate_task_description(self, state: SystemState) -> str:
        """Generate optimization task description."""
        opportunities = "\n".join(
            f"- {opp}" for opp in state.optimization_opportunities
        )

        return f"""System Optimization Analysis:

Identified opportunities:
{opportunities}

Please analyze these opportunities and provide recommendations.
This is a READ_ONLY analysis."""


# ============================================================================
# Strategy Registry
# ============================================================================


def get_default_strategies() -> list[InitiativeStrategy]:
    """
    Get default initiative strategies.

    Returns:
        List of strategy instances
    """
    return [
        MaintenanceStrategy(
            maintenance_interval_hours=24.0,  # Daily maintenance
            min_idle_time_seconds=300.0,  # 5 minutes idle
        ),
        RecoveryStrategy(
            max_errors_to_handle=3,
            min_error_age_seconds=60.0,  # 1 minute
        ),
        OptimizationStrategy(
            check_interval_hours=48.0,  # Check every 2 days
        ),
    ]
