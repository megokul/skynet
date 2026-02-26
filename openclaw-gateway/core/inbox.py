from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Awaitable, Callable

from core.trace import trace_flow

logger = logging.getLogger("skynet.core.inbox")


@dataclass(slots=True)
class InboxMessage:
    text: str
    received_at: float
    future: asyncio.Future[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _QueueState:
    queue: deque[InboxMessage] = field(default_factory=deque)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InboxManager:
    """Per-conversation queue with strict sequential processing."""

    def __init__(
        self,
        processor: Callable[[str, list[InboxMessage]], Awaitable[str]],
        *,
        coalesce_window_seconds: float = 0.8,
        idle_timeout_seconds: float = 300.0,
    ):
        self._processor = processor
        self._coalesce_window = float(coalesce_window_seconds)
        self._idle_timeout = float(idle_timeout_seconds)
        self._state: dict[str, _QueueState] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._map_lock = asyncio.Lock()

    async def append(self, conversation_id: str, message: str, metadata: dict[str, Any] | None = None) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        item = InboxMessage(
            text=message,
            received_at=time.monotonic(),
            future=future,
            metadata=metadata or {},
        )
        trace_flow(
            "inbox.append.received",
            conversation_id=conversation_id,
            text=message,
            metadata=metadata or {},
        )
        state = await self._ensure(conversation_id)
        async with state.lock:
            state.queue.append(item)
            state.event.set()
            queue_size = len(state.queue)
        trace_flow(
            "inbox.append.queued",
            conversation_id=conversation_id,
            queue_size=queue_size,
        )
        return await future

    async def process(self, conversation_id: str) -> None:
        state = await self._ensure(conversation_id)
        await self._drain(conversation_id, state)

    # Backward-compatible aliases
    async def append_message(self, conversation_id: str, message: str, *, metadata: dict[str, Any] | None = None) -> str:
        return await self.append(conversation_id, message, metadata)

    async def process_inbox(self, conversation_id: str) -> None:
        await self.process(conversation_id)

    async def _ensure(self, conversation_id: str) -> _QueueState:
        async with self._map_lock:
            state = self._state.get(conversation_id)
            if state is None:
                state = _QueueState()
                self._state[conversation_id] = state
                trace_flow("inbox.state.created", conversation_id=conversation_id)

            worker = self._workers.get(conversation_id)
            if worker is None or worker.done():
                self._workers[conversation_id] = asyncio.create_task(
                    self._drain(conversation_id, state),
                    name=f"inbox-{conversation_id}",
                )
                trace_flow("inbox.worker.started", conversation_id=conversation_id)
            return state

    async def _drain(self, conversation_id: str, state: _QueueState) -> None:
        this_task = asyncio.current_task()
        try:
            while True:
                try:
                    await asyncio.wait_for(state.event.wait(), timeout=self._idle_timeout)
                except asyncio.TimeoutError:
                    async with state.lock:
                        if state.queue:
                            continue
                        state.event.clear()
                    break

                while True:
                    batch = await self._take_batch(state)
                    if not batch:
                        break
                    trace_flow(
                        "inbox.batch.processing",
                        conversation_id=conversation_id,
                        batch_size=len(batch),
                        merged_text="\\n".join(item.text for item in batch),
                    )
                    try:
                        response = await self._processor(conversation_id, batch)
                    except Exception as exc:
                        logger.exception("Inbox processing failed conversation=%s", conversation_id)
                        trace_flow(
                            "inbox.batch.error",
                            conversation_id=conversation_id,
                            error=str(exc),
                        )
                        response = f"ERROR: inbox processing failed: {exc}"

                    self._resolve(batch[0].future, response)
                    for extra in batch[1:]:
                        self._resolve(extra.future, "")
        finally:
            async with self._map_lock:
                if self._workers.get(conversation_id) is this_task:
                    self._workers.pop(conversation_id, None)
                current = self._state.get(conversation_id)
                if current is state:
                    async with state.lock:
                        if not state.queue:
                            self._state.pop(conversation_id, None)
                            trace_flow("inbox.state.removed", conversation_id=conversation_id)

    async def _take_batch(self, state: _QueueState) -> list[InboxMessage]:
        async with state.lock:
            if not state.queue:
                state.event.clear()
                return []
            first = state.queue.popleft()
            batch = [first]
            deadline = first.received_at + self._coalesce_window
            while state.queue and state.queue[0].received_at <= deadline:
                nxt = state.queue[0]
                if not self._can_coalesce(batch[-1], nxt):
                    break
                batch.append(state.queue.popleft())
            if not state.queue:
                state.event.clear()

        now = time.monotonic()
        if now < deadline:
            await asyncio.sleep(deadline - now)
            async with state.lock:
                while state.queue and state.queue[0].received_at <= deadline:
                    nxt = state.queue[0]
                    if not self._can_coalesce(batch[-1], nxt):
                        break
                    batch.append(state.queue.popleft())
                if not state.queue:
                    state.event.clear()
        trace_flow(
            "inbox.batch.taken",
            batch_size=len(batch),
            first_message=first.text,
        )
        return batch

    def _can_coalesce(self, previous: InboxMessage, current: InboxMessage) -> bool:
        prev_text = previous.text.strip()
        cur_text = current.text.strip()
        if not prev_text or not cur_text:
            return False
        if prev_text.startswith("/") or cur_text.startswith("/"):
            return False
        if previous.metadata.get("boundary") or current.metadata.get("boundary"):
            return False
        return True

    @staticmethod
    def _resolve(future: asyncio.Future[str], value: str) -> None:
        if not future.done():
            future.set_result(value)
