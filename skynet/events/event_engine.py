"""
Event Engine — Background service managing SKYNET's reactive intelligence.

Provides lifecycle management for the event system:
- Starts/stops EventBus
- Registers default event handlers
- Provides API for custom handler registration
- Monitors event processing health

This is the main entry point for SKYNET's event-driven architecture.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .event_bus import EventBus, EventHandler
from .event_handlers import register_default_handlers
from .event_types import Event, EventType

if TYPE_CHECKING:
    from skynet.core.orchestrator import Orchestrator
    from skynet.core.planner import Planner
    from skynet.memory.memory_manager import MemoryManager

logger = logging.getLogger("skynet.events.engine")


class EventEngine:
    """
    Event Engine — Central coordinator for SKYNET's event system.

    Manages EventBus lifecycle and provides reactive intelligence through
    event-driven architecture.

    Usage:
        # Initialize with components
        engine = EventEngine(
            planner=planner,
            orchestrator=orchestrator,
            memory_manager=memory_manager
        )

        # Start event processing
        await engine.start()

        # Publish events
        await engine.publish(Event(...))

        # Subscribe to events
        async def custom_handler(event: Event):
            # Handle event
            pass

        engine.subscribe(EventType.TASK_COMPLETED, custom_handler)

        # Shutdown
        await engine.stop()
    """

    def __init__(
        self,
        planner: Planner | None = None,
        orchestrator: Orchestrator | None = None,
        memory_manager: MemoryManager | None = None,
        max_queue_size: int = 1000,
        register_defaults: bool = True,
    ):
        """
        Initialize EventEngine.

        Args:
            planner: Planner instance (for recovery/initiative handlers)
            orchestrator: Orchestrator instance (for creating jobs)
            memory_manager: Memory manager (for storing patterns)
            max_queue_size: Maximum events in queue
            register_defaults: Whether to register default handlers
        """
        self.event_bus = EventBus(max_queue_size=max_queue_size)
        self.planner = planner
        self.orchestrator = orchestrator
        self.memory_manager = memory_manager
        self._register_defaults = register_defaults

        logger.info("EventEngine initialized")

    # ========================================================================
    # Lifecycle Management
    # ========================================================================

    async def start(self) -> None:
        """
        Start event engine.

        - Starts EventBus background processing
        - Registers default event handlers (if enabled)
        """
        if self.event_bus.is_running:
            logger.warning("EventEngine already running")
            return

        logger.info("Starting EventEngine...")

        # Start EventBus
        await self.event_bus.start()

        # Register default handlers
        if self._register_defaults:
            register_default_handlers(
                self.event_bus,
                planner=self.planner,
                orchestrator=self.orchestrator,
                memory_manager=self.memory_manager,
            )

        logger.info(
            f"EventEngine started "
            f"({self.event_bus.get_subscriber_count()} handlers registered)"
        )

    async def stop(self) -> None:
        """
        Stop event engine.

        Waits for current events to finish processing, then stops EventBus.
        """
        if not self.event_bus.is_running:
            logger.warning("EventEngine not running")
            return

        logger.info("Stopping EventEngine...")

        # Stop EventBus (waits for current event to finish)
        await self.event_bus.stop()

        stats = self.event_bus.get_stats()
        logger.info(
            f"EventEngine stopped "
            f"(processed {stats['event_count']} events, "
            f"{stats['error_count']} errors)"
        )

    @property
    def is_running(self) -> bool:
        """Check if engine is running."""
        return self.event_bus.is_running

    # ========================================================================
    # Event Publishing API
    # ========================================================================

    async def publish(self, event: Event) -> None:
        """
        Publish event to all subscribers.

        Args:
            event: Event to publish
        """
        await self.event_bus.publish(event)

    def publish_nowait(self, event: Event) -> None:
        """
        Publish event without waiting (synchronous).

        Args:
            event: Event to publish
        """
        self.event_bus.publish_nowait(event)

    # ========================================================================
    # Subscription API
    # ========================================================================

    def subscribe(self, event_type: str | EventType, handler: EventHandler) -> None:
        """
        Subscribe to event type.

        Args:
            event_type: Event type to listen for
            handler: Async function to call when event occurs
        """
        self.event_bus.subscribe(event_type, handler)

    def unsubscribe(
        self, event_type: str | EventType, handler: EventHandler
    ) -> None:
        """
        Unsubscribe from event type.

        Args:
            event_type: Event type to stop listening for
            handler: Handler function to remove
        """
        self.event_bus.unsubscribe(event_type, handler)

    # ========================================================================
    # Monitoring and Statistics
    # ========================================================================

    def get_stats(self) -> dict:
        """
        Get event engine statistics.

        Returns:
            Dictionary with stats (event_count, error_count, subscribers, etc.)
        """
        bus_stats = self.event_bus.get_stats()

        # Add component availability info
        bus_stats["components"] = {
            "planner": self.planner is not None,
            "orchestrator": self.orchestrator is not None,
            "memory_manager": self.memory_manager is not None,
        }

        return bus_stats

    def get_subscriber_count(self, event_type: str | EventType | None = None) -> int:
        """
        Get number of subscribers.

        Args:
            event_type: Specific event type (or None for total)

        Returns:
            Number of subscribers
        """
        return self.event_bus.get_subscriber_count(event_type)

    # ========================================================================
    # Component Injection (for dynamic updates)
    # ========================================================================

    def set_planner(self, planner: Planner) -> None:
        """
        Set or update planner instance.

        Args:
            planner: Planner instance
        """
        self.planner = planner
        logger.info("EventEngine planner updated")

    def set_orchestrator(self, orchestrator: Orchestrator) -> None:
        """
        Set or update orchestrator instance.

        Args:
            orchestrator: Orchestrator instance
        """
        self.orchestrator = orchestrator
        logger.info("EventEngine orchestrator updated")

    def set_memory_manager(self, memory_manager: MemoryManager) -> None:
        """
        Set or update memory manager instance.

        Args:
            memory_manager: Memory manager instance
        """
        self.memory_manager = memory_manager
        logger.info("EventEngine memory_manager updated")

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"EventEngine(running={self.is_running}, "
            f"subscribers={self.get_subscriber_count()}, "
            f"components=[planner={'✓' if self.planner else '✗'}, "
            f"orchestrator={'✓' if self.orchestrator else '✗'}, "
            f"memory={'✓' if self.memory_manager else '✗'}])"
        )
