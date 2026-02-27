"""
Commander role (`igris`) responsible for intent-led delegation.

Igris either delegates to a specialist role or produces a direct response when
intent confidence does not justify delegation.
"""

from __future__ import annotations

import logging

from core.dev_trace import (
    DevTracePhase,
    trace_control_flow,
    trace_data_flow,
    trace_decision,
    trace_output,
    trace_prompt,
    trace_role_enter,
)
from core.prompt_library import commander_prompt_block, render_prompt
from core.roles.base import Role, RoleContext, RoleOutput

logger = logging.getLogger("skynet.core.roles.igris")


_SPECIALIST_ROLES = {
    "project_specialist",
    "coding_specialist",
    "weather_specialist",
    "reminder_specialist",
    "research_specialist",
}

_INTENT_ROLE_MAP = {
    "project_create": "project_specialist",
    "start_project": "project_specialist",
    "project_management": "project_specialist",
    "weather": "weather_specialist",
    "weather_query": "weather_specialist",
    "forecast": "weather_specialist",
    "set_reminder": "reminder_specialist",
    "reminder": "reminder_specialist",
    "coding": "coding_specialist",
    "implementation": "coding_specialist",
    "run_coding": "coding_specialist",
    "execute_project": "coding_specialist",
    "research": "research_specialist",
    "research_query": "research_specialist",
    "investigation": "research_specialist",
}


class IgrisRole(Role):
    """
    IgrisRole.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `IgrisRole`.
    """

    name = "igris"
    _guidance = commander_prompt_block()

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Handle message.
        
        Purpose:
        - Implement `handle_message` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `context`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `RoleOutput` when available; otherwise side effects only.
        """

        trace_control_flow(DevTracePhase.ROUTING, stack_depth=2)
        trace_role_enter(DevTracePhase.ROUTING, "igris")
        trace_output(DevTracePhase.ROUTING, key="conversation_id", value=context.conversation.id)

        # Fast path for pure greetings: avoid an unnecessary LLM round trip for
        # a deterministic response.
        lowered = (user_text or "").strip().lower()
        if lowered in {"hi", "hello", "hey", "yo"}:
            trace_decision(
                DevTracePhase.ROUTING,
                {
                    "routing_rule": "deterministic greeting fast path",
                    "selected_action": "respond",
                    "reasoning": "simple greeting does not require specialist",
                },
            )
            return RoleOutput(command="respond", response="Igris online. How should we proceed?")

        extractor = context.intent_extractor
        if extractor is None:
            trace_decision(
                DevTracePhase.ROUTING,
                {
                    "routing_rule": "extractor unavailable",
                    "selected_action": "respond",
                    "reasoning": "cannot classify intent without extractor",
                },
            )
            return RoleOutput(command="respond", response="Igris is unavailable right now.")

        # For non-trivial messages, use the structured extractor to produce
        # intent + confidence + optional role recommendation.
        intent = await extractor.classify_intent(
            user_text,
            active_role=context.conversation.active_role,
            active_project_id=context.conversation.active_project_id,
        )

        # Delegation stays explicit and confidence-gated; low-confidence intents
        # remain with commander for direct response.
        target = self.select_specialist(intent.intent, intent.recommended_role, intent.confidence)
        trace_data_flow(
            DevTracePhase.ROUTING,
            source_name="intent.classification",
            source_value={
                "intent": intent.intent,
                "confidence": intent.confidence,
                "recommended_role": intent.recommended_role or "",
            },
            target_name="routing.target_role",
            target_value=target or "none",
        )
        if target:
            return RoleOutput(command="delegate", target_role=target)

        # Fall back to direct commander response for non-delegated intents.
        response = await self._compose_direct_response(context, intent.intent, user_text)
        trace_output(DevTracePhase.ROUTING, key="direct_response", value=response)
        return RoleOutput(command="respond", response=response)

    def select_specialist(self, intent_name: str, recommended_role: str | None, confidence: float) -> str | None:
        # Prefer model-recommended specialist when it is valid and confidence is
        # strong enough to avoid random role switching.
        """
        Select specialist.
        
        Purpose:
        - Implement `select_specialist` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `intent_name`: input used by this function to compute or route work.
        - `recommended_role`: input used by this function to compute or route work.
        - `confidence`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str | None` when available; otherwise side effects only.
        """

        trace_control_flow(DevTracePhase.ROUTING, stack_depth=2)
        normalized_recommended = (recommended_role or "").strip().lower()
        if normalized_recommended in _SPECIALIST_ROLES and confidence >= 0.45:
            trace_decision(
                DevTracePhase.ROUTING,
                {
                    "routing_rule": "recommended role with confidence gate",
                    "intent": intent_name,
                    "recommended_role": normalized_recommended,
                    "classifier_confidence": confidence,
                    "mapped_role": normalized_recommended,
                },
            )
            return normalized_recommended

        # Deterministic fallback mapping keeps the commander behavior stable if
        # providers omit recommended_role.
        mapped = _INTENT_ROLE_MAP.get((intent_name or "").strip().lower())
        if mapped and confidence >= 0.35:
            trace_decision(
                DevTracePhase.ROUTING,
                {
                    "routing_rule": "intent-role fallback mapping",
                    "intent": intent_name,
                    "recommended_role": normalized_recommended,
                    "classifier_confidence": confidence,
                    "mapped_role": mapped,
                },
            )
            return mapped
        trace_decision(
            DevTracePhase.ROUTING,
            {
                "routing_rule": "stay_with_igris",
                "intent": intent_name,
                "recommended_role": normalized_recommended,
                "classifier_confidence": confidence,
                "mapped_role": "none",
                "reasoning": "confidence below thresholds or no mapping",
            },
        )
        return None

    async def _compose_direct_response(self, context: RoleContext, intent_name: str, user_text: str) -> str:
        """
        Compose direct response.
        
        Purpose:
        - Implement `_compose_direct_response` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `context`: input used by this function to compute or route work.
        - `intent_name`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        prompt = render_prompt(
            "core/roles/igris_direct_user.md",
            intent=intent_name,
            active_project_id=context.conversation.active_project_id or "none",
            user_message=user_text[:1200],
        )
        system_prompt = render_prompt(
            "core/roles/igris_system.md",
            commander_guidance=self._guidance,
        ).strip()
        try:
            trace_prompt(
                DevTracePhase.ROUTING,
                prompt_file="core/roles/igris_direct_user.md",
                model="router:auto",
            )
            response = await context.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system_prompt,
                max_tokens=220,
                task_type="general",
                allowed_providers=None,
            )
            trace_prompt(
                DevTracePhase.ROUTING,
                prompt_file="core/roles/igris_direct_user.md",
                model=str(getattr(response, "model", "") or "router:auto"),
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception:
            logger.exception("Igris direct response generation failed")
        return "Igris acknowledged. Please provide the next concrete instruction."
