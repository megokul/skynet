from __future__ import annotations

import logging
from typing import Any

from core.prompt_library import commander_prompt_block, render_prompt
from core.roles.base import Role, RoleContext, RoleOutput
from core.trace import trace_flow

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
    name = "igris"
    _guidance = commander_prompt_block()

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        trace_flow(
            "role.igris.handle.start",
            conversation_id=context.conversation.id,
            active_project_id=context.conversation.active_project_id or "",
            text=user_text,
        )
        extractor = context.intent_extractor
        if extractor is None:
            return RoleOutput(command="respond", response="Igris is unavailable right now.")

        intent = await extractor.extract(
            user_text,
            active_role=context.conversation.active_role,
            active_project_id=context.conversation.active_project_id,
        )

        target = self._pick_target_role(intent.intent, intent.recommended_role, intent.confidence)
        trace_flow(
            "role.igris.intent",
            conversation_id=context.conversation.id,
            intent=intent.intent,
            confidence=intent.confidence,
            recommended_role=intent.recommended_role or "",
            selected_target=target or "",
        )
        if target:
            return RoleOutput(command="delegate", target_role=target)

        lowered = (user_text or "").strip().lower()
        if lowered in {"hi", "hello", "hey", "yo"}:
            return RoleOutput(command="respond", response="Igris online. How should we proceed?")

        response = await self._compose_direct_response(context, intent.intent, user_text)
        trace_flow(
            "role.igris.respond",
            conversation_id=context.conversation.id,
            response=response,
        )
        return RoleOutput(command="respond", response=response)

    def _pick_target_role(self, intent_name: str, recommended_role: str | None, confidence: float) -> str | None:
        normalized_recommended = (recommended_role or "").strip().lower()
        if normalized_recommended in _SPECIALIST_ROLES and confidence >= 0.45:
            return normalized_recommended

        mapped = _INTENT_ROLE_MAP.get((intent_name or "").strip().lower())
        if mapped and confidence >= 0.35:
            return mapped
        return None

    async def _compose_direct_response(self, context: RoleContext, intent_name: str, user_text: str) -> str:
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
            response = await context.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system_prompt,
                max_tokens=220,
                task_type="general",
                allowed_providers=None,
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception:
            logger.exception("Igris direct response generation failed")
        return "Igris acknowledged. Please provide the next concrete instruction."
