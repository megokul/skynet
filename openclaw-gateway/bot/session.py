"""Session state model and persistence adapter for bot orchestrator turns.

Purpose:
- Represent per-user orchestration state in a typed Session dataclass.
- Normalize pending-question/pending-action metadata payloads.
- Load/update session rows while keeping in-memory state synchronized.

How it works:
- Reads session row, parses metadata JSON, and computes inactivity gap.
- Validates project references and clears stale project ids automatically.
- Merges metadata updates rather than overwriting full session blobs.

Why this exists:
- Encapsulation avoids duplicated state plumbing across orchestrator code paths.
- Normalization keeps pending-state handling robust against malformed rows.
- Merge semantics reduce accidental loss of unrelated metadata fields."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.invariants import PendingAction, PendingQuestion
from db import store

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """
    Session.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `Session`.
    """

    user_id: str
    project_id: str | None
    conversation_phase: str
    last_intent: str
    last_mode: str
    last_message_at: datetime | None
    time_gap: timedelta | None
    metadata: dict[str, Any]
    project: dict[str, Any] | None


def get_pending_question(session: Session) -> PendingQuestion | None:
    """
    Get pending question.
    
    Purpose:
    - Implement `get_pending_question` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `session`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `PendingQuestion | None` when available; otherwise side effects only.
    """

    raw = (session.metadata or {}).get("pending_question")
    normalized = _normalize_pending_question(raw)
    return PendingQuestion(**normalized) if normalized else None


def get_pending_action(session: Session) -> PendingAction | None:
    """
    Get pending action.
    
    Purpose:
    - Implement `get_pending_action` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `session`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `PendingAction | None` when available; otherwise side effects only.
    """

    raw = (session.metadata or {}).get("pending_action")
    normalized = _normalize_pending_action(raw)
    return PendingAction(**normalized) if normalized else None


class SessionLoader:
    """
    SessionLoader.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `SessionLoader`.
    """

    def __init__(self, db, project_manager):
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `db`: input used by this function to compute or route work.
        - `project_manager`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.db = db
        self.project_manager = project_manager

    async def load(self, telegram_user_id: int) -> Session:
        """
        Load.
        
        Purpose:
        - Implement `load` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `telegram_user_id`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `Session` when available; otherwise side effects only.
        """

        user_id = str(telegram_user_id)
        row = await store.get_or_create_session(self.db, user_id=user_id)

        # Parse last_message_at and compute time_gap
        now = datetime.now(timezone.utc)
        last_msg_at = None
        time_gap = None
        if row.get("last_message_at"):
            try:
                last_msg_at = _parse_datetime(row["last_message_at"])
                if last_msg_at:
                    time_gap = now - last_msg_at
            except (ValueError, TypeError):
                pass

        # Parse metadata JSON
        try:
            metadata = json.loads(row.get("session_metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        normalized_metadata = _normalize_metadata(metadata)
        if normalized_metadata != metadata:
            metadata = normalized_metadata
            await store.update_session(
                self.db,
                user_id=user_id,
                session_metadata=json.dumps(metadata),
                updated_at=_utcnow_iso(),
            )

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
        changes["updated_at"] = _utcnow_iso()

        # Metadata: MERGE, never overwrite
        if "session_metadata" in changes:
            merged = dict(session.metadata)
            merged.update(changes["session_metadata"])
            merged = _normalize_metadata(merged)
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
                session.last_message_at = _parse_datetime(value) if value else None
            except (TypeError, ValueError):
                session.last_message_at = None
            if session.last_message_at:
                session.time_gap = datetime.now(timezone.utc) - session.last_message_at
            else:
                session.time_gap = None

        await store.update_session(self.db, user_id=session.user_id, **changes)

    async def set_pending_question(
        self,
        session: Session,
        pending_question: PendingQuestion | dict[str, Any],
    ) -> None:
        """
        Set pending question.
        
        Purpose:
        - Implement `set_pending_question` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `session`: input used by this function to compute or route work.
        - `pending_question`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if isinstance(pending_question, PendingQuestion):
            payload = {
                "type": pending_question.type,
                "choices": pending_question.choices,
                "turn_id": pending_question.turn_id,
                "expires_at": pending_question.expires_at,
                "payload": pending_question.payload,
                "created_at": pending_question.created_at,
            }
        else:
            payload = dict(pending_question)
        normalized = _normalize_pending_question(payload)
        if not normalized:
            raise ValueError("Invalid pending_question payload.")
        await self.update(session, session_metadata={"pending_question": normalized})

    async def clear_pending_question(self, session: Session) -> None:
        """
        Clear pending question.
        
        Purpose:
        - Implement `clear_pending_question` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        await self.update(session, session_metadata={"pending_question": None})

    async def set_pending_action(
        self,
        session: Session,
        pending_action: PendingAction | dict[str, Any],
    ) -> None:
        """
        Set pending action.
        
        Purpose:
        - Implement `set_pending_action` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `session`: input used by this function to compute or route work.
        - `pending_action`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if isinstance(pending_action, PendingAction):
            payload = {
                "type": pending_action.type,
                "project_id": pending_action.project_id,
                "plan_id": pending_action.plan_id,
                "created_at": pending_action.created_at,
                "expires_at": pending_action.expires_at,
            }
        else:
            payload = dict(pending_action)
        normalized = _normalize_pending_action(payload)
        if not normalized:
            raise ValueError("Invalid pending_action payload.")
        await self.update(session, session_metadata={"pending_action": normalized})

    async def clear_pending_action(self, session: Session) -> None:
        """
        Clear pending action.
        
        Purpose:
        - Implement `clear_pending_action` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        await self.update(session, session_metadata={"pending_action": None})


def _utcnow_iso() -> str:
    """
    Utcnow iso.
    
    Purpose:
    - Implement `_utcnow_iso` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    """
    Parse datetime.
    
    Purpose:
    - Implement `_parse_datetime` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `value`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `datetime | None` when available; otherwise side effects only.
    """

    if value is None:
        return None
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_metadata(metadata: Any) -> dict[str, Any]:
    """
    Normalize metadata.
    
    Purpose:
    - Implement `_normalize_metadata` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `metadata`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    if not isinstance(metadata, dict):
        return {}
    normalized = dict(metadata)
    pq = _normalize_pending_question(normalized.get("pending_question"))
    pa = _normalize_pending_action(normalized.get("pending_action"))
    if pq:
        normalized["pending_question"] = pq
    else:
        normalized.pop("pending_question", None)
    if pa:
        normalized["pending_action"] = pa
    else:
        normalized.pop("pending_action", None)
    return normalized


def _normalize_pending_question(raw: Any) -> dict[str, Any] | None:
    """
    Normalize pending question.
    
    Purpose:
    - Implement `_normalize_pending_question` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `raw`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    if not isinstance(raw, dict):
        return None
    q_type = str(raw.get("type") or "").strip()
    turn_id = str(raw.get("turn_id") or "").strip()
    expires_at = str(raw.get("expires_at") or "").strip()
    if not q_type or not turn_id or not expires_at:
        return None
    if not _is_future(expires_at):
        return None
    choices_raw = raw.get("choices") or []
    if isinstance(choices_raw, (tuple, list)):
        choices = [str(x).strip() for x in choices_raw if str(x).strip()]
    else:
        choices = []
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else None
    created_at = str(raw.get("created_at") or "").strip() or _utcnow_iso()
    return {
        "type": q_type,
        "choices": choices,
        "turn_id": turn_id,
        "expires_at": expires_at,
        "payload": payload,
        "created_at": created_at,
    }


def _normalize_pending_action(raw: Any) -> dict[str, Any] | None:
    """
    Normalize pending action.
    
    Purpose:
    - Implement `_normalize_pending_action` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `raw`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

    if not isinstance(raw, dict):
        return None
    action_type = str(raw.get("type") or "").strip()
    project_id = str(raw.get("project_id") or "").strip()
    if not action_type or not project_id:
        return None
    expires_raw = raw.get("expires_at")
    expires_at = str(expires_raw).strip() if expires_raw else ""
    if expires_at and not _is_future(expires_at):
        return None
    plan_id = raw.get("plan_id")
    if plan_id is not None:
        try:
            plan_id = int(plan_id)
        except (TypeError, ValueError):
            plan_id = None
    created_at = str(raw.get("created_at") or "").strip() or _utcnow_iso()
    normalized: dict[str, Any] = {
        "type": action_type,
        "project_id": project_id,
        "created_at": created_at,
    }
    if expires_at:
        normalized["expires_at"] = expires_at
    if plan_id is not None:
        normalized["plan_id"] = plan_id
    return normalized


def _is_future(iso_value: str) -> bool:
    """
    Is future.
    
    Purpose:
    - Implement `_is_future` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `iso_value`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `bool` when available; otherwise side effects only.
    """

    try:
        dt = _parse_datetime(iso_value)
    except (TypeError, ValueError):
        return False
    return bool(dt and dt > datetime.now(timezone.utc))
