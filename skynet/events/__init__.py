"""
SKYNET Event System — Reactive Intelligence Module.

Provides event-driven architecture for SKYNET to respond to system events:
- Task lifecycle events (created, started, completed, failed)
- System events (worker status, provider health)
- Error events (failures, deployments)
- Opportunity events (system idle, optimization opportunities)

Core Components:
- EventBus: Central pub/sub event dispatcher
- Event: Base event class with type, payload, timestamp
- EventType: Enum of all event types
- EventEngine: Background service managing event processing
- Event Handlers: Reactive logic for specific event types

Usage:
    from skynet.events import EventBus, Event, EventType

    # Publish event
    await event_bus.publish(Event(
        type=EventType.TASK_COMPLETED,
        payload={'job_id': '123', 'result': {...}},
        source='worker'
    ))

    # Subscribe to events
    async def on_task_failed(event: Event):
        # Generate recovery plan
        pass

    event_bus.subscribe(EventType.TASK_FAILED, on_task_failed)
"""

from .event_types import Event, EventType
from .event_bus import EventBus
from .event_engine import EventEngine

__all__ = [
    "Event",
    "EventType",
    "EventBus",
    "EventEngine",
]
