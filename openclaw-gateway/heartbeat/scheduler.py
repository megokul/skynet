"""
SKYNET Heartbeat — Autonomous Task Scheduler

Runs periodic background tasks without user intervention.
Each task has a configurable interval and can be paused/resumed
independently.

Tasks run in the asyncio event loop — they must be async and
should complete quickly (< 30s) to avoid blocking other tasks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger("skynet.heartbeat")


@dataclass
class HeartbeatTask:
    """Definition of a periodic background task."""

    name: str
    description: str
    interval_seconds: int
    handler: Callable[..., Awaitable[None]]
    enabled: bool = True
    last_run: float = 0.0
    run_count: int = 0
    last_error: str = ""
    context: Any = None          # arbitrary context passed to handler


class HeartbeatScheduler:
    """
    Manages periodic autonomous tasks.

    Start the scheduler with ``await start()`` — it runs forever in a
    background asyncio task, checking every tick which tasks are due.
    """

    def __init__(self, tick_interval: int = 60):
        self._tasks: dict[str, HeartbeatTask] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._tick_interval = tick_interval

    def register(self, task: HeartbeatTask) -> None:
        """Register a periodic task."""
        self._tasks[task.name] = task
        logger.info(
            "Heartbeat task registered: %s (every %ds)",
            task.name, task.interval_seconds,
        )

    def unregister(self, name: str) -> bool:
        """Remove a task by name."""
        return self._tasks.pop(name, None) is not None

    async def start(self) -> None:
        """Start the heartbeat loop as a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat scheduler started (tick=%ds)", self._tick_interval)

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Heartbeat scheduler stopped")

    def pause_task(self, name: str) -> bool:
        """Pause a specific task."""
        task = self._tasks.get(name)
        if task:
            task.enabled = False
            logger.info("Heartbeat task paused: %s", name)
            return True
        return False

    def resume_task(self, name: str) -> bool:
        """Resume a specific task."""
        task = self._tasks.get(name)
        if task:
            task.enabled = True
            logger.info("Heartbeat task resumed: %s", name)
            return True
        return False

    def get_status(self) -> list[dict[str, Any]]:
        """Return status of all registered tasks."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "interval_seconds": t.interval_seconds,
                "enabled": t.enabled,
                "last_run": t.last_run,
                "run_count": t.run_count,
                "last_error": t.last_error,
                "next_run_in": max(
                    0,
                    t.interval_seconds - (time.time() - t.last_run),
                ) if t.last_run else 0,
            }
            for t in self._tasks.values()
        ]

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    async def _loop(self) -> None:
        """Main heartbeat loop — check and run due tasks each tick."""
        logger.info("Heartbeat loop started with %d tasks", len(self._tasks))
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Heartbeat tick error")
            await asyncio.sleep(self._tick_interval)

    async def _tick(self) -> None:
        """One tick — run all tasks that are due."""
        now = time.time()
        for task in self._tasks.values():
            if not task.enabled:
                continue
            elapsed = now - task.last_run
            if elapsed < task.interval_seconds:
                continue

            # Task is due.
            try:
                logger.debug("Running heartbeat task: %s", task.name)
                await asyncio.wait_for(
                    task.handler(task.context),
                    timeout=30.0,
                )
                task.last_run = time.time()
                task.run_count += 1
                task.last_error = ""
            except asyncio.TimeoutError:
                task.last_error = "Timed out (30s limit)"
                logger.warning("Heartbeat task %s timed out", task.name)
            except Exception as exc:
                task.last_error = str(exc)
                logger.warning("Heartbeat task %s failed: %s", task.name, exc)
            # Always update last_run to prevent rapid retries on failure.
            task.last_run = time.time()
