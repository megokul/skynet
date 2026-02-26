"""
Deterministic pre-role routing helpers.

The router resolves conversation/project scope and write-intent gate decisions
before any specialist/LLM role logic executes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.conversation_manager import Conversation, ConversationManager
from core.trace import trace_flow


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
    """Result of deterministic project-scope inspection for one user turn."""

    conversation_id: str
    active_project_id: str | None
    switch_requested: bool


@dataclass(slots=True)
class RoutingDecision:
    """Deterministic pre-role routing decision for a single intent."""

    intent: str
    execute_now: bool
    requires_question: bool
    question_type: str | None


class Router:
    """
    Deterministic continuity router that runs before role reasoning.

    This component is intentionally non-LLM and side-effect-light:
    - Resolves which conversation should receive the turn.
    - Resolves project scope and switch hints from explicit user text.
    - Produces deterministic write-intent routing decisions.

    It does not execute role logic directly.
    """

    def __init__(self, conversation_manager: ConversationManager):
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
        - `conversation_manager`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self._conversation_manager = conversation_manager

    async def resolve_conversation_scope(
        self,
        *,
        user_id: int,
        requested_conversation_id: str | None = None,
    ) -> Conversation:
        """
        Resolve the conversation context for this turn.

        Priority:
        1. Explicit requested conversation (if it belongs to the user).
        2. User's currently active conversation.
        3. Auto-create a new conversation.
        """
        if requested_conversation_id:
            existing = await self._conversation_manager.get_conversation(requested_conversation_id)
            if existing and existing.user_id == int(user_id):
                await self._conversation_manager.set_active_conversation(int(user_id), existing.id)
                trace_flow(
                    "router.conversation_scope.requested",
                    user_id=user_id,
                    requested_conversation_id=requested_conversation_id,
                    resolved_conversation_id=existing.id,
                )
                return existing
        resolved = await self._conversation_manager.get_or_create_active_conversation(int(user_id))
        trace_flow(
            "router.conversation_scope.active",
            user_id=user_id,
            resolved_conversation_id=resolved.id,
            requested_conversation_id=requested_conversation_id or "",
        )
        return resolved

    def resolve_project_scope(self, *, conversation: Conversation, user_text: str) -> ScopeResolution:
        """
        Determine whether this message explicitly asks to switch project scope.

        Note: this is lexical/deterministic, used as a guardrail before any
        model-driven intent reasoning.
        """
        lowered = (user_text or "").strip().lower()
        switch_requested = any(phrase in lowered for phrase in _SWITCH_PROJECT_PHRASES)
        resolution = ScopeResolution(
            conversation_id=conversation.id,
            active_project_id=conversation.active_project_id,
            switch_requested=switch_requested,
        )
        trace_flow(
            "router.project_scope.resolved",
            conversation_id=conversation.id,
            active_project_id=conversation.active_project_id or "",
            switch_requested=switch_requested,
            user_text=user_text,
        )
        return resolution

    def route_intent(
        self,
        *,
        conversation: Conversation,
        intent: str,
        user_text: str,
        payload: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        """
        Route intents deterministically before role execution.

        For write intents:
        - Execute immediately only when active project exists and payload exists.
        - Ask for payload when scope exists but payload is missing.
        - Ask project-selection question when no active project exists.

        Non-write intents are returned for normal role handling.
        """
        payload = payload or {}

        if intent in WRITE_INTENTS:
            if conversation.active_project_id and payload:
                decision = RoutingDecision(intent=intent, execute_now=True, requires_question=False, question_type=None)
                self._trace_decision(conversation.id, decision, bool(payload))
                return decision
            if conversation.active_project_id:
                decision = RoutingDecision(intent=intent, execute_now=False, requires_question=True, question_type="need_payload")
                self._trace_decision(conversation.id, decision, bool(payload))
                return decision
            decision = RoutingDecision(intent=intent, execute_now=False, requires_question=True, question_type="choose_project")
            self._trace_decision(conversation.id, decision, bool(payload))
            return decision

        decision = RoutingDecision(intent=intent, execute_now=False, requires_question=False, question_type=None)
        self._trace_decision(conversation.id, decision, bool(payload))
        return decision

    @staticmethod
    def _trace_decision(conversation_id: str, decision: RoutingDecision, has_payload: bool) -> None:
        """Emit one normalized decision log event for easier debugging."""
        trace_flow(
            "router.intent.route",
            conversation_id=conversation_id,
            intent=decision.intent,
            execute_now=decision.execute_now,
            requires_question=decision.requires_question,
            question_type=decision.question_type or "",
            has_payload=has_payload,
        )
