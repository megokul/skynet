from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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

    async def set_active_role(self, conversation_id: str, role: str) -> None:
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            active_role=(role or "igris").strip() or "igris",
        )

    async def set_active_project(self, conversation_id: str, project_id: str | None) -> None:
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            active_project_id=project_id,
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

    async def clear_pending_question(self, conversation_id: str) -> None:
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            pending_question={},
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

    async def clear_pending_action(self, conversation_id: str) -> None:
        await store.update_conversation_session(
            self.db,
            conversation_id=conversation_id,
            pending_action={},
        )

    async def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return await store.add_session_message(
            self.db,
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata=metadata,
        )

    async def list_messages(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return await store.list_session_messages(self.db, conversation_id=conversation_id, limit=int(limit))

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