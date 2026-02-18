"""
CHATHAN Worker — Per-Project Action Queue

Ensures actions for the same project are executed sequentially,
while different projects can run in parallel. Includes rate limiting
and exponential backoff on errors.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger("chathan.executor.queue")

# Rate limiting: minimum interval between actions (seconds).
_MIN_ACTION_INTERVAL = 0.5

# Backoff settings.
_BACKOFF_BASE = 1.0     # Initial backoff seconds.
_BACKOFF_MAX = 30.0     # Maximum backoff seconds.
_BACKOFF_FACTOR = 2.0   # Exponential factor.
_MAX_RETRIES = 3         # Max retries before giving up.


class ProjectQueue:
    """Sequential action queue for a single project."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._last_action_time: float = 0.0
        self._error_count: int = 0

    def start(self) -> None:
        """Start the queue worker."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def enqueue(
        self,
        action: str,
        params: dict[str, Any],
        executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Add an action to the queue and wait for its result."""
        item = _QueueItem(action=action, params=params, executor=executor)
        await self._queue.put(item)
        self.start()
        return await item.future

    async def _run(self) -> None:
        """Process queued actions sequentially."""
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # Queue idle for 60s — stop the worker.
                break

            # Rate limiting.
            elapsed = time.monotonic() - self._last_action_time
            if elapsed < _MIN_ACTION_INTERVAL:
                await asyncio.sleep(_MIN_ACTION_INTERVAL - elapsed)

            result = await self._execute_with_backoff(item)
            item.future.set_result(result)
            self._last_action_time = time.monotonic()

        self._task = None

    async def _execute_with_backoff(self, item: _QueueItem) -> dict[str, Any]:
        """Execute with exponential backoff on errors."""
        backoff = _BACKOFF_BASE
        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = await item.executor(item.action, item.params)
                self._error_count = 0
                return result
            except Exception as exc:
                self._error_count += 1
                if attempt >= _MAX_RETRIES:
                    logger.error(
                        "Action %s failed after %d retries: %s",
                        item.action, _MAX_RETRIES, exc,
                    )
                    return {
                        "status": "error",
                        "error": f"Failed after {_MAX_RETRIES} retries: {exc}",
                    }
                logger.warning(
                    "Action %s failed (attempt %d), retrying in %.1fs: %s",
                    item.action, attempt + 1, backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)

        return {"status": "error", "error": "Unexpected queue error"}


class _QueueItem:
    """A single queued action with its future for result delivery."""

    def __init__(
        self,
        action: str,
        params: dict[str, Any],
        executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ):
        self.action = action
        self.params = params
        self.executor = executor
        self.future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().get_future()


class ActionQueueManager:
    """Manages per-project action queues."""

    def __init__(self):
        self._queues: dict[str, ProjectQueue] = {}

    def get_queue(self, project_id: str) -> ProjectQueue:
        """Get or create a queue for a project."""
        if project_id not in self._queues:
            self._queues[project_id] = ProjectQueue(project_id)
        return self._queues[project_id]

    async def enqueue(
        self,
        project_id: str,
        action: str,
        params: dict[str, Any],
        executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Enqueue an action for a specific project."""
        queue = self.get_queue(project_id)
        return await queue.enqueue(action, params, executor)

    def cleanup_idle(self) -> int:
        """Remove queues whose workers have stopped. Returns count removed."""
        idle = [
            pid for pid, q in self._queues.items()
            if q._task is None or q._task.done()
        ]
        for pid in idle:
            del self._queues[pid]
        return len(idle)
