from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from core.trace import trace_flow
from core.tracing import trace_step
from db import store


@dataclass(slots=True)
class Conversation:
    id: str
    user_id: int
    title: str
    active_role: str
    active_project_id: str | None
    pending_question: dict[str, Any]
    pending_action: dict[str, Any]
    created_at: str
    updated_at: str

    @property
    def conversation_id(self) -> str:
        return self.id


class ConversationManager:
    """Conversation state manager for explicit chat sessions."""

    def __init__(self, db):
        self.db = db

    async def create_conversation(self, user_id: int, title: str | None = None) -> Conversation:
        safe_title = (title or "").strip() or datetime.now(timezone.utc).strftime("Conversation %Y-%m-%d %H:%M")
        row = await store.create_conversation_session(
            self.db,
            user_id=int(user_id),
            title=safe_title,
            active_role="igris",
        )
        await store.set_user_active_conversation(
            self.db,
            user_id=int(user_id),
            conversation_id=row["conversation_id"],
        )
        trace_flow(
            "conversation.create",
            user_id=user_id,
            conversation_id=row["conversation_id"],
            title=safe_title,
        )
        return self._to_conversation(row)

    async def list_conversations(self, user_id: int, limit: int = 50) -> list[Conversation]:
        rows = await store.list_conversation_sessions(self.db, user_id=int(user_id), limit=int(limit))
        return [self._to_conversation(row) for row in rows]

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = await store.get_conversation_session(self.db, conversation_id=conversation_id)
        if not row:
            return None
        return self._to_conversation(row)

    async def get_or_create_active_conversation(self, user_id: int) -> Conversation:
        active_id = await store.get_user_active_conversation(self.db, user_id=int(user_id))
        if active_id:
            existing = await self.get_conversation(active_id)
            if existing:
                return existing

        listed = await self.list_conversations(int(user_id), limit=1)
        if listed:
            await self.set_active_conversation(int(user_id), listed[0].id)
            return listed[0]

        return await self.create_conversation(int(user_id))

    async def set_active_conversation(self, user_id: int, conversation_id: str) -> None:
        await store.set_user_active_conversation(
            self.db,
            user_id=int(user_id),
            conversation_id=conversation_id,
        )
        trace_flow(
            "conversation.active.set",
            user_id=user_id,
            conversation_id=conversation_id,
        )

    async def set_active_role(self, conversation_id: str, role: str) -> None:
        started = time.perf_counter()
        before = await self.get_conversation(conversation_id)
        resolved = (role or "igris").strip() or "igris"
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            active_role=resolved,
        )
        trace_step(
            function_name="set_active_role",
            file_name="conversation_manager.py",
            role="state",
            parameters={"conversation_id": conversation_id, "role": resolved},
            state_before={"active_role": before.active_role if before else None},
            state_after={"active_role": resolved},
            result={"success": True},
            execution_time_ms=(time.perf_counter() - started) * 1000.0,
        )
        trace_flow(
            "conversation.role.set",
            conversation_id=conversation_id,
            active_role=resolved,
        )

    async def set_active_project(self, conversation_id: str, project_id: str | None) -> None:
        started = time.perf_counter()
        before = await self.get_conversation(conversation_id)
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            active_project_id=project_id,
        )
        trace_step(
            function_name="set_active_project",
            file_name="conversation_manager.py",
            role="state",
            parameters={"conversation_id": conversation_id, "project_id": project_id},
            state_before={"active_project_id": before.active_project_id if before else None},
            state_after={"active_project_id": project_id},
            result={"success": True},
            execution_time_ms=(time.perf_counter() - started) * 1000.0,
        )
        trace_flow(
            "conversation.project.set",
            conversation_id=conversation_id,
            active_project_id=project_id or "",
        )

    async def set_pending_question(self, conversation_id: str, question: dict[str, Any]) -> None:
        payload = dict(question or {})
        now = datetime.now(timezone.utc)
        payload.setdefault("created_at", now.isoformat())
        payload.setdefault("expires_at", (now + timedelta(minutes=15)).isoformat())
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            pending_question=payload,
        )
        trace_flow(
            "conversation.pending_question.set",
            conversation_id=conversation_id,
            pending_question=payload,
        )

    async def clear_pending_question(self, conversation_id: str) -> None:
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            pending_question={},
        )
        trace_flow(
            "conversation.pending_question.clear",
            conversation_id=conversation_id,
        )

    async def set_pending_action(self, conversation_id: str, action: dict[str, Any]) -> None:
        payload = dict(action or {})
        now = datetime.now(timezone.utc)
        payload.setdefault("created_at", now.isoformat())
        payload.setdefault("expires_at", (now + timedelta(hours=24)).isoformat())
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            pending_action=payload,
        )
        trace_flow(
            "conversation.pending_action.set",
            conversation_id=conversation_id,
            pending_action=payload,
        )

    async def clear_pending_action(self, conversation_id: str) -> None:
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            pending_action={},
        )
        trace_flow(
            "conversation.pending_action.clear",
            conversation_id=conversation_id,
        )

    async def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        started = time.perf_counter()
        before_count = await self._count_messages(conversation_id)
        message_id = await store.add_session_message(
            self.db,
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata=metadata,
        )
        after_count = before_count + 1
        trace_step(
            function_name="add_message",
            file_name="conversation_manager.py",
            role="state",
            parameters={
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
            },
            state_before={"conversation.message_count": before_count},
            state_after={"conversation.message_count": after_count},
            result={"message_id": message_id},
            execution_time_ms=(time.perf_counter() - started) * 1000.0,
        )
        trace_flow(
            "conversation.message.add",
            conversation_id=conversation_id,
            message_id=message_id,
            role=role,
            content=content,
            metadata=metadata or {},
        )
        return message_id

    async def list_messages(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return await store.list_session_messages(self.db, conversation_id=conversation_id, limit=int(limit))

    async def _count_messages(self, conversation_id: str) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return 0
        try:
            return int(row["c"])
        except Exception:
            return int(row[0])

    def _to_conversation(self, row: dict[str, Any]) -> Conversation:
        return Conversation(
            id=str(row["conversation_id"]),
            user_id=int(row["user_id"]),
            title=str(row.get("title") or "Conversation"),
            active_role=str(row.get("active_role") or "igris"),
            active_project_id=(str(row["active_project_id"]) if row.get("active_project_id") else None),
            pending_question=row.get("pending_question") if isinstance(row.get("pending_question"), dict) else {},
            pending_action=row.get("pending_action") if isinstance(row.get("pending_action"), dict) else {},
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )
