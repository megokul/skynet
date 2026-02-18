"""
SKYNET — Task Scheduler

Manages parallel project execution.  Each active project gets one
asyncio.Task running an AgentWorker (v3) or legacy Worker (v2).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

import aiosqlite

from ai.provider_router import ProviderRouter
from search.web_search import WebSearcher
from skills.registry import SkillRegistry

logger = logging.getLogger("skynet.core.scheduler")


class Scheduler:
    """Manages parallel project workers."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        router: ProviderRouter,
        searcher: WebSearcher,
        gateway_api_url: str,
        on_progress: Callable[[str, str, str], Awaitable[None]],
        request_approval: Callable[[str, str, dict], Awaitable[bool]],
        max_parallel: int = 3,
        skill_registry: SkillRegistry | None = None,
        memory_manager: Any | None = None,
    ):
        self.db = db
        self.router = router
        self.searcher = searcher
        self.gateway_url = gateway_api_url
        self.on_progress = on_progress
        self.request_approval = request_approval
        self.max_parallel = max_parallel
        self.skill_registry = skill_registry
        self.memory_manager = memory_manager

        self._tasks: dict[str, asyncio.Task] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    @property
    def running_count(self) -> int:
        return len(self._tasks)

    def is_running(self, project_id: str) -> bool:
        return project_id in self._tasks

    async def submit(self, project_id: str) -> None:
        if project_id in self._tasks:
            raise RuntimeError(f"Project {project_id} is already running.")
        if len(self._tasks) >= self.max_parallel:
            raise RuntimeError(
                f"Max parallel limit reached ({self.max_parallel}). "
                f"Pause or cancel a project first."
            )

        pause_event = asyncio.Event()
        pause_event.set()  # not paused
        cancel_event = asyncio.Event()

        # Use AgentWorker (v3) when skill_registry is available,
        # otherwise fall back to legacy Worker.
        if self.skill_registry is not None:
            from agents.agent_worker import AgentWorker
            worker = AgentWorker(
                project_id=project_id,
                db=self.db,
                router=self.router,
                searcher=self.searcher,
                skill_registry=self.skill_registry,
                memory_manager=self.memory_manager,
                gateway_api_url=self.gateway_url,
                pause_event=pause_event,
                cancel_event=cancel_event,
                on_progress=self.on_progress,
                request_approval=self.request_approval,
            )
        else:
            from .worker import Worker
            worker = Worker(
                project_id=project_id,
                db=self.db,
                router=self.router,
                searcher=self.searcher,
                gateway_api_url=self.gateway_url,
                pause_event=pause_event,
                cancel_event=cancel_event,
                on_progress=self.on_progress,
                request_approval=self.request_approval,
            )

        task = asyncio.create_task(
            worker.run(),
            name=f"project-{project_id}",
        )
        task.add_done_callback(lambda t: self._cleanup(project_id))

        self._tasks[project_id] = task
        self._pause_events[project_id] = pause_event
        self._cancel_events[project_id] = cancel_event

        logger.info("Started worker for project %s", project_id)

    def pause(self, project_id: str) -> bool:
        event = self._pause_events.get(project_id)
        if event:
            event.clear()
            return True
        return False

    def resume(self, project_id: str) -> bool:
        event = self._pause_events.get(project_id)
        if event:
            event.set()
            return True
        return False

    def cancel(self, project_id: str) -> bool:
        event = self._cancel_events.get(project_id)
        if event:
            event.set()
            return True
        return False

    def cancel_all(self) -> int:
        count = 0
        for project_id in list(self._tasks):
            if self.cancel(project_id):
                count += 1
        return count

    def _cleanup(self, project_id: str) -> None:
        self._tasks.pop(project_id, None)
        self._pause_events.pop(project_id, None)
        self._cancel_events.pop(project_id, None)
        logger.info("Worker finished for project %s", project_id)
