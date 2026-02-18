"""
Initiative Engine — Autonomous task generation and proactive intelligence.

The core of SKYNET's autonomous capabilities.

Continuously monitors system state and takes initiative to:
- Perform maintenance
- Recover from errors
- Optimize performance
- Learn from patterns

This is SKYNET's "self-awareness" - the ability to act without being told.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .constraints import INITIATIVE_CONSTRAINTS, SafetyConstraints
from .monitors import SystemStateMonitor
from .strategies import get_default_strategies, InitiativeStrategy

if TYPE_CHECKING:
    from skynet.core.orchestrator import Orchestrator
    from skynet.core.planner import Planner
    from skynet.memory.memory_manager import MemoryManager
    from skynet.events import EventBus
    from skynet.ledger.worker_registry import WorkerRegistry

logger = logging.getLogger("skynet.cognition.initiative")


class InitiativeEngine:
    """
    Autonomous initiative engine for SKYNET.

    Monitors system state and proactively generates tasks for:
    - System maintenance
    - Error recovery
    - Performance optimization
    - Learning opportunities

    Safety-constrained to prevent runaway task generation.

    Usage:
        engine = InitiativeEngine(
            planner=planner,
            orchestrator=orchestrator,
            memory_manager=memory,
            event_bus=event_bus
        )

        await engine.start()
    """

    def __init__(
        self,
        planner: Planner | None = None,
        orchestrator: Orchestrator | None = None,
        memory_manager: MemoryManager | None = None,
        worker_registry: WorkerRegistry | None = None,
        event_bus: EventBus | None = None,
        strategies: list[InitiativeStrategy] | None = None,
        constraints: SafetyConstraints | None = None,
        check_interval_seconds: float = 300.0,  # 5 minutes
    ):
        """
        Initialize InitiativeEngine.

        Args:
            planner: Planner for generating plans
            orchestrator: Orchestrator for creating jobs
            memory_manager: Memory manager for history
            worker_registry: Worker registry for load/availability signals
            event_bus: Event bus for publishing events
            strategies: List of initiative strategies (uses defaults if None)
            constraints: Safety constraints (uses defaults if None)
            check_interval_seconds: How often to check for initiatives
        """
        self.planner = planner
        self.orchestrator = orchestrator
        self.memory_manager = memory_manager
        self.event_bus = event_bus

        # Strategies and constraints
        self.strategies = strategies or get_default_strategies()
        self.constraints = constraints or INITIATIVE_CONSTRAINTS

        # System monitor
        self.monitor = SystemStateMonitor(
            orchestrator=orchestrator,
            memory_manager=memory_manager,
            worker_registry=worker_registry,
        )

        # Background task management
        self._running = False
        self._task: asyncio.Task | None = None
        self.check_interval = check_interval_seconds

        # Statistics
        self._autonomous_tasks_created = 0
        self._autonomous_tasks_blocked = 0
        self._task_history: list[dict] = []

        logger.info(
            f"InitiativeEngine initialized "
            f"(strategies={len(self.strategies)}, "
            f"check_interval={check_interval_seconds}s)"
        )

    # ========================================================================
    # Lifecycle Management
    # ========================================================================

    async def start(self) -> None:
        """Start autonomous monitoring loop."""
        if self._running:
            logger.warning("InitiativeEngine already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("InitiativeEngine started")

    async def stop(self) -> None:
        """Stop autonomous monitoring loop."""
        if not self._running:
            logger.warning("InitiativeEngine not running")
            return

        logger.info("Stopping InitiativeEngine...")
        self._running = False

        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("InitiativeEngine shutdown timeout, cancelling")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

        logger.info(
            f"InitiativeEngine stopped "
            f"(created={self._autonomous_tasks_created}, "
            f"blocked={self._autonomous_tasks_blocked})"
        )

    @property
    def is_running(self) -> bool:
        """Check if engine is running."""
        return self._running

    # ========================================================================
    # Autonomous Monitoring Loop
    # ========================================================================

    async def _monitor_loop(self) -> None:
        """
        Main monitoring loop.

        Continuously checks system state and takes initiative when appropriate.
        """
        logger.info("Initiative monitoring loop started")

        while self._running:
            try:
                # Wait for next check interval
                await asyncio.sleep(self.check_interval)

                # Assess system state
                state = await self.monitor.assess_system_state()

                # Check each strategy
                for strategy in self.strategies:
                    try:
                        await self._check_strategy(strategy, state)
                    except Exception as e:
                        logger.exception(
                            f"Error checking strategy {strategy.get_name()}: {e}"
                        )

            except Exception as e:
                logger.exception(f"Error in initiative monitoring loop: {e}")
                # Continue loop despite errors

        logger.info("Initiative monitoring loop stopped")

    async def _check_strategy(
        self, strategy: InitiativeStrategy, state
    ) -> None:
        """
        Check if strategy should trigger and create task if appropriate.

        Args:
            strategy: Initiative strategy to check
            state: Current system state
        """
        # Check if strategy should trigger
        try:
            should_trigger = await strategy.should_trigger(state)
        except Exception as e:
            logger.error(
                f"Strategy {strategy.get_name()} trigger check failed: {e}"
            )
            return

        if not should_trigger:
            return

        logger.info(f"Strategy triggered: {strategy.get_name()}")

        # Generate task description
        try:
            task_description = await strategy.generate_task_description(state)
        except Exception as e:
            logger.error(
                f"Strategy {strategy.get_name()} task generation failed: {e}"
            )
            return

        # Create autonomous task
        await self._create_autonomous_task(
            task_description=task_description,
            strategy_name=strategy.get_name(),
            risk_level="READ_ONLY",  # All autonomous tasks are READ_ONLY
        )

    async def _create_autonomous_task(
        self, task_description: str, strategy_name: str, risk_level: str = "READ_ONLY"
    ) -> None:
        """
        Create an autonomous task.

        Args:
            task_description: Task description for Planner
            strategy_name: Name of strategy that triggered
            risk_level: Risk level (default: READ_ONLY)
        """
        logger.info(
            f"Creating autonomous task from strategy '{strategy_name}': "
            f"{task_description[:50]}..."
        )

        # Check safety constraints
        allowed, reason = self.constraints.check_task_allowed(
            task_description, risk_level
        )

        if not allowed:
            logger.warning(
                f"Autonomous task blocked by safety constraints: {reason}"
            )
            self._autonomous_tasks_blocked += 1
            return

        # Check rate limits
        allowed, reason = self.constraints.check_rate_limit(self._task_history)

        if not allowed:
            logger.warning(f"Autonomous task blocked by rate limit: {reason}")
            self._autonomous_tasks_blocked += 1
            return

        # Create task via orchestrator
        if not self.orchestrator:
            logger.warning("No orchestrator available, cannot create task")
            return

        try:
            # Create job
            job_id = await self.orchestrator.create_task(
                user_intent=task_description,
                project_id="autonomous",
            )

            # Generate plan
            plan = await self.orchestrator.generate_plan(job_id)

            # Auto-approve if READ_ONLY
            if risk_level == "READ_ONLY":
                await self.orchestrator.approve_plan(job_id)
                logger.info(
                    f"Autonomous task created and approved: {job_id} "
                    f"(strategy={strategy_name})"
                )
            else:
                logger.info(
                    f"Autonomous task created (requires approval): {job_id}"
                )

            # Track task
            self._task_history.append({
                "job_id": job_id,
                "strategy": strategy_name,
                "created_at": datetime.utcnow().isoformat(),
                "approved": risk_level == "READ_ONLY",
            })

            self._autonomous_tasks_created += 1

            # Mark activity
            self.monitor.mark_activity()

            # Mark maintenance if maintenance strategy
            if strategy_name == "MaintenanceStrategy":
                self.monitor.mark_maintenance()

        except Exception as e:
            logger.exception(f"Failed to create autonomous task: {e}")
            self._autonomous_tasks_blocked += 1

    # ========================================================================
    # Statistics and Monitoring
    # ========================================================================

    def get_stats(self) -> dict:
        """
        Get initiative engine statistics.

        Returns:
            Dictionary with stats
        """
        return {
            "running": self._running,
            "strategies": len(self.strategies),
            "tasks_created": self._autonomous_tasks_created,
            "tasks_blocked": self._autonomous_tasks_blocked,
            "check_interval_seconds": self.check_interval,
            "constraints": {
                "max_tasks_per_hour": self.constraints.max_tasks_per_hour,
                "allowed_risk_levels": self.constraints.allowed_risk_levels,
            },
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"InitiativeEngine("
            f"running={self._running}, "
            f"strategies={len(self.strategies)}, "
            f"created={self._autonomous_tasks_created}, "
            f"blocked={self._autonomous_tasks_blocked})"
        )
