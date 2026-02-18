"""
Event Bus — Central event dispatcher with pub/sub pattern.

Provides asynchronous event distribution to registered handlers.
Uses asyncio.Queue for non-blocking event processing.

Pattern inspired by BackgroundScheduler and ProviderMonitor from SKYNET codebase.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable

from .event_types import Event, EventType

logger = logging.getLogger("skynet.events.bus")


EventHandler = Callable[[Event], Awaitable[None]]
"""Type alias for async event handler functions."""


class EventBus:
    """
    Central event dispatcher using pub/sub pattern.

    Features:
    - Asynchronous event processing (non-blocking publish)
    - Multiple subscribers per event type
    - Wildcard subscription ("*" for all events)
    - Background processing loop
    - Graceful startup/shutdown

    Usage:
        bus = EventBus()
        await bus.start()

        # Subscribe
        async def on_task_completed(event: Event):
            logger.info(f"Task {event.payload['job_id']} completed")

        bus.subscribe(EventType.TASK_COMPLETED, on_task_completed)

        # Publish
        await bus.publish(Event(...))

        # Shutdown
        await bus.stop()
    """

    def __init__(self, max_queue_size: int = 1000):
        """
        Initialize EventBus.

        Args:
            max_queue_size: Maximum events in queue before blocking
        """
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._running = False
        self._task: asyncio.Task | None = None
        self._event_count = 0
        self._error_count = 0

        logger.info("EventBus initialized")

    # ========================================================================
    # Subscription Management
    # ========================================================================

    def subscribe(self, event_type: str | EventType, handler: EventHandler) -> None:
        """
        Subscribe to event type.

        Args:
            event_type: Event type to listen for (or "*" for all events)
            handler: Async function to call when event occurs

        Example:
            async def on_task_failed(event: Event):
                # Handle failure
                pass

            bus.subscribe(EventType.TASK_FAILED, on_task_failed)
        """
        # Normalize EventType to string
        if isinstance(event_type, EventType):
            event_type = event_type.value

        self._subscribers[event_type].append(handler)
        logger.info(
            f"Subscribed handler to '{event_type}' "
            f"({len(self._subscribers[event_type])} handlers)"
        )

    def unsubscribe(
        self, event_type: str | EventType, handler: EventHandler
    ) -> None:
        """
        Unsubscribe from event type.

        Args:
            event_type: Event type to stop listening for
            handler: Handler function to remove
        """
        if isinstance(event_type, EventType):
            event_type = event_type.value

        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(handler)
                logger.info(f"Unsubscribed handler from '{event_type}'")
            except ValueError:
                logger.warning(f"Handler not found for '{event_type}'")

    def get_subscriber_count(self, event_type: str | EventType | None = None) -> int:
        """
        Get number of subscribers.

        Args:
            event_type: Specific event type (or None for total)

        Returns:
            Number of subscribers
        """
        if event_type is None:
            # Total across all types
            return sum(len(handlers) for handlers in self._subscribers.values())
        else:
            if isinstance(event_type, EventType):
                event_type = event_type.value
            return len(self._subscribers.get(event_type, []))

    # ========================================================================
    # Event Publishing
    # ========================================================================

    async def publish(self, event: Event) -> None:
        """
        Publish event to all subscribers (non-blocking).

        Event is added to queue and processed by background loop.

        Args:
            event: Event to publish

        Raises:
            asyncio.QueueFull: If queue is full (should never happen with await)
        """
        if not self._running:
            logger.warning(
                f"EventBus not running, dropping event: {event.type}"
            )
            return

        try:
            await self._event_queue.put(event)
            logger.debug(f"Published event: {event.type} from {event.source}")
        except asyncio.QueueFull:
            logger.error(
                f"Event queue full! Dropping event: {event.type}"
            )
            self._error_count += 1

    def publish_nowait(self, event: Event) -> None:
        """
        Publish event without waiting (synchronous version).

        Use with caution - may raise QueueFull if queue is full.

        Args:
            event: Event to publish

        Raises:
            asyncio.QueueFull: If queue is full
        """
        if not self._running:
            logger.warning(
                f"EventBus not running, dropping event: {event.type}"
            )
            return

        try:
            self._event_queue.put_nowait(event)
            logger.debug(f"Published event (nowait): {event.type}")
        except asyncio.QueueFull:
            logger.error(
                f"Event queue full! Dropping event: {event.type}"
            )
            self._error_count += 1

    # ========================================================================
    # Background Processing
    # ========================================================================

    async def _process_events(self) -> None:
        """
        Background loop processing events from queue.

        Runs until stopped. Handles events sequentially, calling all
        registered handlers for each event type.
        """
        logger.info("EventBus processing loop started")

        while self._running:
            try:
                # Wait for next event (with timeout for clean shutdown)
                try:
                    event = await asyncio.wait_for(
                        self._event_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # No event received, loop again (allows clean shutdown check)
                    continue

                # Process event
                await self._dispatch_event(event)
                self._event_count += 1

            except asyncio.CancelledError:
                # Normal during shutdown when the processing task is cancelled.
                break
            except Exception as e:
                logger.exception(f"Error in event processing loop: {e}")
                self._error_count += 1
                # Continue processing despite errors

        logger.info("EventBus processing loop stopped")

    async def _dispatch_event(self, event: Event) -> None:
        """
        Dispatch event to all registered handlers.

        Args:
            event: Event to dispatch
        """
        event_type = event.type
        handlers_called = 0

        # Get handlers for specific event type
        specific_handlers = self._subscribers.get(event_type, [])

        # Get wildcard handlers (subscribed to all events)
        wildcard_handlers = self._subscribers.get("*", [])

        # Call all handlers
        all_handlers = specific_handlers + wildcard_handlers

        if not all_handlers:
            logger.debug(f"No handlers for event: {event_type}")
            return

        logger.debug(
            f"Dispatching event '{event_type}' to {len(all_handlers)} handlers"
        )

        for handler in all_handlers:
            try:
                # Call handler asynchronously
                await handler(event)
                handlers_called += 1

            except Exception as e:
                logger.exception(
                    f"Error in event handler for '{event_type}': {e}"
                )
                self._error_count += 1
                # Continue calling other handlers despite error

        logger.debug(
            f"Event '{event_type}' processed by {handlers_called} handlers"
        )

    # ========================================================================
    # Lifecycle Management
    # ========================================================================

    async def start(self) -> None:
        """
        Start EventBus background processing.

        Creates background task that processes events from queue.
        """
        if self._running:
            logger.warning("EventBus already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._process_events())
        logger.info("EventBus started")

    async def stop(self) -> None:
        """
        Stop EventBus background processing.

        Waits for current event to finish, then stops processing loop.
        """
        if not self._running:
            logger.warning("EventBus not running")
            return

        logger.info("Stopping EventBus...")
        self._running = False

        # Wait for processing task to finish
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.CancelledError:
                # Processing task cancelled cleanly while waiting on queue I/O.
                pass
            except asyncio.TimeoutError:
                logger.warning("EventBus shutdown timeout, cancelling task")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

        logger.info(
            f"EventBus stopped (processed {self._event_count} events, "
            f"{self._error_count} errors)"
        )

    @property
    def is_running(self) -> bool:
        """Check if EventBus is running."""
        return self._running

    def get_stats(self) -> dict[str, int]:
        """
        Get EventBus statistics.

        Returns:
            Dictionary with stats (event_count, error_count, queue_size, subscribers)
        """
        return {
            "event_count": self._event_count,
            "error_count": self._error_count,
            "queue_size": self._event_queue.qsize(),
            "total_subscribers": self.get_subscriber_count(),
            "running": self._running,
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"EventBus(running={self._running}, "
            f"subscribers={self.get_subscriber_count()}, "
            f"events_processed={self._event_count})"
        )
