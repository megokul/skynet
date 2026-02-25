from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from db import store

logger = logging.getLogger(__name__)


@dataclass
class Session:
    user_id: str
    project_id: str | None
    conversation_phase: str
    last_intent: str
    last_mode: str
    last_message_at: datetime | None
    time_gap: timedelta | None
    metadata: dict[str, Any]
    project: dict[str, Any] | None


class SessionLoader:
    def __init__(self, db, project_manager):
        self.db = db
        self.project_manager = project_manager

    async def load(self, telegram_user_id: int) -> Session:
        user_id = str(telegram_user_id)
        row = await store.get_or_create_session(self.db, user_id=user_id)

        # Parse last_message_at and compute time_gap
        last_msg_at = None
        time_gap = None
        if row.get("last_message_at"):
            try:
                last_msg_at = datetime.fromisoformat(row["last_message_at"])
                time_gap = datetime.utcnow() - last_msg_at
            except (ValueError, TypeError):
                pass

        # Parse metadata JSON
        try:
            metadata = json.loads(row.get("session_metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        project_id = row.get("project_id")
        project = None

        # Validate project still exists -- if deleted, clear session reference
        if project_id:
            project = await store.get_project(self.db, project_id)
            if not project:
                logger.warning("Session references deleted project %s, clearing", project_id)
                await store.update_session(self.db, user_id=user_id, project_id=None)
                project_id = None

        return Session(
            user_id=user_id,
            project_id=project_id,
            conversation_phase=row.get("conversation_phase", "discovery"),
            last_intent=row.get("last_intent", ""),
            last_mode=row.get("last_mode", "conversation"),
            last_message_at=last_msg_at,
            time_gap=time_gap,
            metadata=metadata,
            project=project,
        )

    async def update(self, session: Session, **changes) -> None:
        """
        Write changes to DB AND sync in-memory Session object.
        Always stamps updated_at automatically.
        Merges session_metadata (never overwrites).
        """
        changes["updated_at"] = datetime.utcnow().isoformat()

        # Metadata: MERGE, never overwrite
        if "session_metadata" in changes:
            merged = dict(session.metadata)
            merged.update(changes["session_metadata"])
            changes["session_metadata"] = json.dumps(merged)
            session.metadata = merged

        # Sync scalar fields back to in-memory Session
        if "project_id" in changes:
            session.project_id = changes["project_id"]
            if changes["project_id"] is None:
                session.project = None
        if "conversation_phase" in changes:
            session.conversation_phase = changes["conversation_phase"]
        if "last_mode" in changes:
            session.last_mode = changes["last_mode"]
        if "last_intent" in changes:
            session.last_intent = changes["last_intent"]
        if "last_message_at" in changes:
            value = changes["last_message_at"]
            try:
                session.last_message_at = datetime.fromisoformat(value) if value else None
            except (TypeError, ValueError):
                session.last_message_at = None
            if session.last_message_at:
                session.time_gap = datetime.utcnow() - session.last_message_at
            else:
                session.time_gap = None

        await store.update_session(self.db, user_id=session.user_id, **changes)
