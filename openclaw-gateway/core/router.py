from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Awaitable, Callable


WRITE_INTENTS = {
    "propose_idea",
    "add_note",
    "add_task",
    "attach_file",
    "update_metadata",
}

_SWITCH_PROJECT_RE = re.compile(
    r"\b(switch project|use project|new project|create a new project)\b",
    re.IGNORECASE,
)
_SHORT_SCOPE_NEW = {"new", "new one"}
_SHORT_SCOPE_EXISTING = {"existing", "same", "current"}


@dataclass
class ScopeResolution:
    conversation_id: str
    active_project_id: str | None
    switch_requested: bool
    switch_target: str | None


@dataclass
class RoutingDecision:
    intent: str
    execute_now: bool
    requires_question: bool
    question_type: str | None
    reason: str


class Router:
    """
    Deterministic router that runs before any reasoning/model call.
    """

    def __init__(
        self,
        *,
        execute_write_intent: Callable[[str, dict[str, Any], Any], Awaitable[str]],
    ):
        self._execute_write_intent = execute_write_intent

    def resolve_conversation_scope(
        self,
        *,
        conversation: Any,
    ) -> ScopeResolution:
        return ScopeResolution(
            conversation_id=str(conversation.conversation_id),
            active_project_id=conversation.active_project_id,
            switch_requested=False,
            switch_target=None,
        )

    def resolve_project_scope(
        self,
        *,
        conversation: Any,
        user_text: str,
    ) -> ScopeResolution:
        text = (user_text or "").strip()
        switch_requested = bool(_SWITCH_PROJECT_RE.search(text))
        switch_target: str | None = None
        if switch_requested:
            match = re.search(r"\buse project ([a-zA-Z0-9_\\- ]+)\b", text, re.IGNORECASE)
            if match:
                switch_target = match.group(1).strip()

        return ScopeResolution(
            conversation_id=str(conversation.conversation_id),
            active_project_id=conversation.active_project_id,
            switch_requested=switch_requested,
            switch_target=switch_target,
        )

    def route_intent(
        self,
        *,
        conversation: Any,
        intent: str,
        user_text: str,
        payload: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        payload = payload or {}
        pending_question = conversation.pending_question or {}

        # Short answer semantics are active only under typed pending question.
        short = (user_text or "").strip().lower()
        if short in (_SHORT_SCOPE_NEW | _SHORT_SCOPE_EXISTING):
            if pending_question.get("type") != "choose_project":
                return RoutingDecision(
                    intent=intent,
                    execute_now=False,
                    requires_question=False,
                    question_type=None,
                    reason="short_reply_without_pending_question",
                )

        if intent in WRITE_INTENTS:
            if conversation.active_project_id:
                has_payload = bool(payload)
                if has_payload:
                    return RoutingDecision(
                        intent=intent,
                        execute_now=True,
                        requires_question=False,
                        question_type=None,
                        reason="write_intent_active_project_payload_present",
                    )
                return RoutingDecision(
                    intent=intent,
                    execute_now=False,
                    requires_question=True,
                    question_type="need_payload",
                    reason="write_intent_missing_payload",
                )
            return RoutingDecision(
                intent=intent,
                execute_now=False,
                requires_question=True,
                question_type="choose_project",
                reason="write_intent_without_active_project",
            )

        return RoutingDecision(
            intent=intent,
            execute_now=False,
            requires_question=False,
            question_type=None,
            reason="reasoning_intent",
        )

    async def execute_write_intent(
        self,
        *,
        intent: str,
        payload: dict[str, Any],
        context: Any,
    ) -> str:
        return await self._execute_write_intent(intent, payload, context)

    @staticmethod
    def is_pending_question_expired(question: dict[str, Any]) -> bool:
        expires = question.get("expires_at")
        if not expires:
            return False
        try:
            dt = datetime.fromisoformat(str(expires))
        except (TypeError, ValueError):
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= datetime.now(timezone.utc)
