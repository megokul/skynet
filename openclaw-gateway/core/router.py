from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.conversation_manager import Conversation, ConversationManager


WRITE_INTENTS = {
    "propose_idea",
    "add_note",
    "add_task",
    "attach_file",
    "update_metadata",
}

_SWITCH_PROJECT_PHRASES = (
    "switch project",
    "use project",
    "new project",
    "create a new project",
    "start a new project",
)


@dataclass(slots=True)
class ScopeResolution:
    conversation_id: str
    active_project_id: str | None
    switch_requested: bool


@dataclass(slots=True)
class RoutingDecision:
    intent: str
    execute_now: bool
    requires_question: bool
    question_type: str | None


class Router:
    """Deterministic continuity router that runs before role reasoning."""

    def __init__(
        self,
        conversation_manager: ConversationManager,
        *,
        execute_write_intent: Callable[[str, dict[str, Any], Any], Awaitable[str]] | None = None,
    ):
        self._conversation_manager = conversation_manager
        self._execute_write_intent = execute_write_intent

    async def resolve_conversation_scope(
        self,
        *,
        user_id: int,
        requested_conversation_id: str | None = None,
    ) -> Conversation:
        if requested_conversation_id:
            existing = await self._conversation_manager.get_conversation(requested_conversation_id)
            if existing and existing.user_id == int(user_id):
                await self._conversation_manager.set_active_conversation(int(user_id), existing.id)
                return existing
        return await self._conversation_manager.get_or_create_active_conversation(int(user_id))

    def resolve_project_scope(self, *, conversation: Conversation, user_text: str) -> ScopeResolution:
        lowered = (user_text or "").strip().lower()
        switch_requested = any(phrase in lowered for phrase in _SWITCH_PROJECT_PHRASES)
        return ScopeResolution(
            conversation_id=conversation.id,
            active_project_id=conversation.active_project_id,
            switch_requested=switch_requested,
        )

    def route_intent(
        self,
        *,
        conversation: Conversation,
        intent: str,
        user_text: str,
        payload: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        payload = payload or {}

        if intent in WRITE_INTENTS:
            if conversation.active_project_id and payload:
                return RoutingDecision(intent=intent, execute_now=True, requires_question=False, question_type=None)
            if conversation.active_project_id:
                return RoutingDecision(intent=intent, execute_now=False, requires_question=True, question_type="need_payload")
            return RoutingDecision(intent=intent, execute_now=False, requires_question=True, question_type="choose_project")

        return RoutingDecision(intent=intent, execute_now=False, requires_question=False, question_type=None)

    async def execute_write_intent(self, *, intent: str, payload: dict[str, Any], context: Any) -> str:
        if not self._execute_write_intent:
            raise RuntimeError("execute_write_intent callback is not configured")
        return await self._execute_write_intent(intent, payload, context)