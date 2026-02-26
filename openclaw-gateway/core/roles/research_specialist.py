"""
Research specialist role.

Generates read-only research answers using dedicated research prompts and then
hands control back to commander in the same turn.
"""

from __future__ import annotations

import logging

from core.prompt_library import engineering_prompt_block, render_prompt
from core.roles.base import Role, RoleContext, RoleOutput
from core.trace import trace_flow
from core.tracing import trace

logger = logging.getLogger("skynet.core.roles.research")


class ResearchSpecialistRole(Role):
    """
    ResearchSpecialistRole.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `ResearchSpecialistRole`.
    """

    name = "research_specialist"
    _guidance = engineering_prompt_block()

    @trace(
        role="research_specialist",
        prompt="prompts/core/roles/research_user.md",
        step_name="research_specialist_handle",
    )
    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Generate a research-style answer with engineering guidance prompts.

        This role is read-only and completes in one turn.
        """
        trace_flow(
            "role.research.handle.start",
            conversation_id=context.conversation.id,
            question=user_text,
        )
        prompt = render_prompt(
            "core/roles/research_user.md",
            question=user_text[:1500],
        )
        system_prompt = render_prompt(
            "core/roles/research_system.md",
            engineering_guidance=self._guidance,
        ).strip()
        try:
            response = await context.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system_prompt,
                max_tokens=500,
                task_type="general",
                allowed_providers=None,
            )
            text = (response.text or "").strip() or "I need a bit more detail for research."
        except Exception as exc:
            logger.exception("Research specialist failed")
            trace_flow(
                "role.research.handle.error",
                conversation_id=context.conversation.id,
                error=str(exc),
            )
            text = f"Research failed right now: {exc}"
        trace_flow(
            "role.research.handle.complete",
            conversation_id=context.conversation.id,
            response=text,
        )
        return RoleOutput(command="complete", response=text)
