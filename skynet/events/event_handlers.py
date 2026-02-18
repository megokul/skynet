"""
Event Handlers — Reactive logic for system events.

Provides default event handlers for common SKYNET scenarios:
- Task failure recovery
- Worker offline alerts
- System idle optimization
- Error pattern learning

These handlers demonstrate reactive intelligence - SKYNET responding
automatically to system events without user intervention.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .event_types import Event, EventType

if TYPE_CHECKING:
    from skynet.core.orchestrator import Orchestrator
    from skynet.core.planner import Planner
    from skynet.memory.memory_manager import MemoryManager

logger = logging.getLogger("skynet.events.handlers")


# ============================================================================
# Task Lifecycle Handlers
# ============================================================================


async def on_task_failed(
    event: Event,
    planner: Planner | None = None,
    orchestrator: Orchestrator | None = None,
    memory_manager: MemoryManager | None = None,
) -> None:
    """
    Handle task failure - store failure pattern and optionally generate recovery plan.

    Args:
        event: TASK_FAILED event
        planner: Planner instance (optional, for recovery plan generation)
        orchestrator: Orchestrator instance (optional, for creating recovery job)
        memory_manager: Memory manager (optional, for storing failure pattern)
    """
    job_id = event.payload.get("job_id")
    error_message = event.payload.get("error", "Unknown error")
    execution_spec = event.payload.get("execution_spec", {})

    logger.warning(f"Task {job_id} failed: {error_message}")

    # Store failure pattern for learning
    if memory_manager:
        try:
            await memory_manager.store_failure_pattern(
                error_type="TaskExecutionFailure",
                error_message=error_message,
                context={
                    "job_id": job_id,
                    "execution_spec": execution_spec,
                    "source": event.source,
                },
                suggested_fix=None,  # TODO: AI-generated fix suggestion
            )
            logger.info(f"Stored failure pattern for job {job_id}")
        except Exception as e:
            logger.error(f"Failed to store failure pattern: {e}")

    # TODO: Generate recovery plan (Phase 5 - Initiative Engine)
    # if planner and orchestrator:
    #     recovery_plan = await planner.generate_recovery_plan(...)
    #     await orchestrator.create_job(recovery_plan, auto_approve=True)


async def on_task_completed(
    event: Event, memory_manager: MemoryManager | None = None
) -> None:
    """
    Handle task completion - store success for learning.

    Args:
        event: TASK_COMPLETED event
        memory_manager: Memory manager (optional)
    """
    job_id = event.payload.get("job_id")
    result = event.payload.get("result", {})

    logger.info(f"Task {job_id} completed successfully")

    # Store task execution in memory
    if memory_manager:
        try:
            # Extract task details from result
            user_message = result.get("user_message", "Unknown task")
            plan = result.get("plan", {})
            execution_result = result.get("execution_result", {})
            duration = result.get("duration_seconds", 0)

            await memory_manager.store_task_execution(
                request_id=job_id,
                user_message=user_message,
                plan=plan,
                result=execution_result,
                risk_level=plan.get("risk_level", "UNKNOWN"),
                duration_seconds=duration,
                success=True,
                provider=result.get("provider", "unknown"),
                target=result.get("target", "unknown"),
            )
            logger.info(f"Stored successful execution for job {job_id}")

        except Exception as e:
            logger.error(f"Failed to store task execution: {e}")


async def on_task_started(event: Event) -> None:
    """
    Handle task start - log for monitoring.

    Args:
        event: TASK_STARTED event
    """
    job_id = event.payload.get("job_id")
    worker_id = event.payload.get("worker_id", "unknown")

    logger.info(f"Task {job_id} started by worker {worker_id}")


# ============================================================================
# System Event Handlers
# ============================================================================


async def on_worker_offline(event: Event) -> None:
    """
    Handle worker going offline - log alert.

    Args:
        event: WORKER_OFFLINE event
    """
    worker_id = event.payload.get("component")
    last_heartbeat = event.payload.get("last_heartbeat")

    logger.warning(
        f"Worker {worker_id} went offline (last heartbeat: {last_heartbeat})"
    )

    # TODO: Send alert via AlertDispatcher (Phase 8 integration)
    # TODO: Attempt job failover (migrate in-flight jobs to other workers)


async def on_worker_online(event: Event) -> None:
    """
    Handle worker coming online - log info.

    Args:
        event: WORKER_ONLINE event
    """
    worker_id = event.payload.get("component")
    capabilities = event.payload.get("capabilities", [])

    logger.info(
        f"Worker {worker_id} came online (capabilities: {capabilities})"
    )


async def on_provider_unhealthy(event: Event) -> None:
    """
    Handle provider becoming unhealthy - log alert.

    Args:
        event: PROVIDER_UNHEALTHY event
    """
    provider_name = event.payload.get("component")
    health_status = event.payload.get("health_status", {})

    logger.error(
        f"Provider {provider_name} became unhealthy: {health_status}"
    )

    # TODO: Trigger provider failover in scheduler


# ============================================================================
# Error Event Handlers
# ============================================================================


async def on_error_detected(
    event: Event, memory_manager: MemoryManager | None = None
) -> None:
    """
    Handle generic error detection - store for pattern analysis.

    Args:
        event: ERROR_DETECTED event
        memory_manager: Memory manager (optional)
    """
    error_message = event.payload.get("error_message")
    context = event.payload.get("context", {})

    logger.error(f"Error detected: {error_message}")

    # Store error pattern
    if memory_manager:
        try:
            await memory_manager.store_failure_pattern(
                error_type="GenericError",
                error_message=error_message,
                context=context,
            )
        except Exception as e:
            logger.error(f"Failed to store error pattern: {e}")


async def on_deployment_failed(
    event: Event, memory_manager: MemoryManager | None = None
) -> None:
    """
    Handle deployment failure - critical alert.

    Args:
        event: DEPLOYMENT_FAILED event
        memory_manager: Memory manager (optional)
    """
    deployment_id = event.payload.get("deployment_id")
    error_message = event.payload.get("error_message")
    target = event.payload.get("target", "unknown")

    logger.critical(
        f"Deployment {deployment_id} to {target} failed: {error_message}"
    )

    # Store critical failure
    if memory_manager:
        try:
            await memory_manager.store_failure_pattern(
                error_type="DeploymentFailure",
                error_message=error_message,
                context={
                    "deployment_id": deployment_id,
                    "target": target,
                    "severity": "CRITICAL",
                },
            )
        except Exception as e:
            logger.error(f"Failed to store deployment failure: {e}")


# ============================================================================
# Opportunity Event Handlers (for autonomous initiative)
# ============================================================================


async def on_system_idle(
    event: Event,
    planner: Planner | None = None,
    orchestrator: Orchestrator | None = None,
) -> None:
    """
    Handle system idle state - opportunity for maintenance tasks.

    Args:
        event: SYSTEM_IDLE event
        planner: Planner instance (optional)
        orchestrator: Orchestrator instance (optional)
    """
    idle_duration = event.payload.get("idle_duration_seconds", 0)

    logger.info(f"System idle for {idle_duration}s")

    # TODO: Trigger maintenance tasks (Phase 5 - Initiative Engine)
    # if planner and orchestrator:
    #     maintenance_plan = await planner.generate_plan(
    #         job_id="maintenance",
    #         user_intent="Run system maintenance: cleanup old logs, check for updates"
    #     )
    #     await orchestrator.create_job(maintenance_plan, auto_approve=True)


async def on_optimization_opportunity(event: Event) -> None:
    """
    Handle optimization opportunity - log for analysis.

    Args:
        event: OPTIMIZATION_OPPORTUNITY event
    """
    opportunity_type = event.payload.get("opportunity_type")
    details = event.payload.get("details", {})

    logger.info(
        f"Optimization opportunity detected: {opportunity_type} - {details}"
    )

    # TODO: Trigger optimization task (Phase 5)


# ============================================================================
# Event Handler Registry
# ============================================================================


def register_default_handlers(
    event_bus,
    planner: Planner | None = None,
    orchestrator: Orchestrator | None = None,
    memory_manager: MemoryManager | None = None,
) -> None:
    """
    Register all default event handlers with EventBus.

    Args:
        event_bus: EventBus instance
        planner: Planner instance (optional, for recovery/initiative)
        orchestrator: Orchestrator instance (optional, for creating jobs)
        memory_manager: Memory manager (optional, for storing patterns)
    """
    logger.info("Registering default event handlers...")

    # Task lifecycle handlers
    event_bus.subscribe(
        EventType.TASK_STARTED,
        lambda e: on_task_started(e),
    )

    event_bus.subscribe(
        EventType.TASK_COMPLETED,
        lambda e: on_task_completed(e, memory_manager=memory_manager),
    )

    event_bus.subscribe(
        EventType.TASK_FAILED,
        lambda e: on_task_failed(
            e,
            planner=planner,
            orchestrator=orchestrator,
            memory_manager=memory_manager,
        ),
    )

    # System event handlers
    event_bus.subscribe(
        EventType.WORKER_ONLINE,
        lambda e: on_worker_online(e),
    )

    event_bus.subscribe(
        EventType.WORKER_OFFLINE,
        lambda e: on_worker_offline(e),
    )

    event_bus.subscribe(
        EventType.PROVIDER_UNHEALTHY,
        lambda e: on_provider_unhealthy(e),
    )

    # Error event handlers
    event_bus.subscribe(
        EventType.ERROR_DETECTED,
        lambda e: on_error_detected(e, memory_manager=memory_manager),
    )

    event_bus.subscribe(
        EventType.DEPLOYMENT_FAILED,
        lambda e: on_deployment_failed(e, memory_manager=memory_manager),
    )

    # Opportunity handlers (for future autonomous initiative)
    event_bus.subscribe(
        EventType.SYSTEM_IDLE,
        lambda e: on_system_idle(e, planner=planner, orchestrator=orchestrator),
    )

    event_bus.subscribe(
        EventType.OPTIMIZATION_OPPORTUNITY,
        lambda e: on_optimization_opportunity(e),
    )

    logger.info(
        f"Registered {event_bus.get_subscriber_count()} default event handlers"
    )
