from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import time
from typing import Any, Awaitable, Callable


@dataclass
class InboxMessage:
    message: str
    received_at: float
    future: asyncio.Future[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _ConversationInbox:
    queue: deque[InboxMessage] = field(default_factory=deque)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InboxManager:
    """
    Per-conversation inbox queue.

    - never drops messages
    - sequential processing per conversation
    - optional 800ms coalescing window
    """

    def __init__(
        self,
        processor: Callable[[str, list[InboxMessage]], Awaitable[str]],
        *,
        coalesce_window_seconds: float = 0.8,
        idle_timeout_seconds: float = 300.0,
    ):
        self.processor = processor
        self.coalesce_window_seconds = coalesce_window_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self._inboxes: dict[str, _ConversationInbox] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._map_lock = asyncio.Lock()

    async def append_message(
        self,
        conversation_id: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        inbox_message = InboxMessage(
            message=message,
            received_at=time.monotonic(),
            future=future,
            metadata=metadata or {},
        )
        inbox = await self._ensure_conversation_inbox(conversation_id)
        async with inbox.lock:
            inbox.queue.append(inbox_message)
            inbox.event.set()
        return await future

    async def process_inbox(self, conversation_id: str) -> None:
        inbox = await self._ensure_conversation_inbox(conversation_id)
        await self._drain(conversation_id, inbox)

    async def _ensure_conversation_inbox(self, conversation_id: str) -> _ConversationInbox:
        async with self._map_lock:
            inbox = self._inboxes.get(conversation_id)
            if inbox is None:
                inbox = _ConversationInbox()
                self._inboxes[conversation_id] = inbox
            task = self._workers.get(conversation_id)
            if task is None or task.done():
                self._workers[conversation_id] = asyncio.create_task(
                    self._drain(conversation_id, inbox),
                    name=f"inbox-{conversation_id}",
                )
            return inbox

    async def _drain(self, conversation_id: str, inbox: _ConversationInbox) -> None:
        this_task = asyncio.current_task()
        try:
            while True:
                try:
                    await asyncio.wait_for(inbox.event.wait(), timeout=self.idle_timeout_seconds)
                except asyncio.TimeoutError:
                    async with inbox.lock:
                        if inbox.queue:
                            continue
                        inbox.event.clear()
                    break

                while True:
                    batch = await self._take_batch(inbox)
                    if not batch:
                        break
                    try:
                        response = await self.processor(conversation_id, batch)
                    except Exception as exc:
                        response = f"ERROR: inbox processor failed: {exc}"
                    self._resolve_future(batch[0].future, response)
                    for msg in batch[1:]:
                        self._resolve_future(msg.future, "")
        finally:
            async with self._map_lock:
                if self._workers.get(conversation_id) is this_task:
                    self._workers.pop(conversation_id, None)
                inbox_ref = self._inboxes.get(conversation_id)
                if inbox_ref is inbox:
                    async with inbox.lock:
                        if not inbox.queue:
                            self._inboxes.pop(conversation_id, None)

    async def _take_batch(self, inbox: _ConversationInbox) -> list[InboxMessage]:
        async with inbox.lock:
            if not inbox.queue:
                inbox.event.clear()
                return []
            first = inbox.queue.popleft()
            batch = [first]
            deadline = first.received_at + self.coalesce_window_seconds
            while inbox.queue and inbox.queue[0].received_at <= deadline:
                if not self._can_coalesce(batch[-1], inbox.queue[0]):
                    break
                batch.append(inbox.queue.popleft())
            if not inbox.queue:
                inbox.event.clear()

        now = time.monotonic()
        if now < deadline:
            await asyncio.sleep(deadline - now)
            async with inbox.lock:
                while inbox.queue and inbox.queue[0].received_at <= deadline:
                    if not self._can_coalesce(batch[-1], inbox.queue[0]):
                        break
                    batch.append(inbox.queue.popleft())
                if not inbox.queue:
                    inbox.event.clear()

        return batch

    def _can_coalesce(self, previous: InboxMessage, current: InboxMessage) -> bool:
        # Do not coalesce explicit command boundaries.
        if previous.message.strip().startswith("/") or current.message.strip().startswith("/"):
            return False
        if previous.metadata.get("boundary") or current.metadata.get("boundary"):
            return False
        return True

    def _resolve_future(self, future: asyncio.Future[str], value: str) -> None:
        if future.done():
            return
        future.set_result(value)
