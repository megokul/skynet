"""
Research specialist role.

Generates read-only research answers using dedicated research prompts and then
hands control back to commander in the same turn.
"""

from __future__ import annotations

import logging

from core.dev_trace import (
    DevTracePhase,
    trace_control_flow,
    trace_decision,
    trace_output,
    trace_prompt,
    trace_role_enter,
)
from core.prompt_library import engineering_prompt_block, render_prompt
from core.roles.base import Role, RoleContext, RoleOutput

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

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Generate a research-style answer with engineering guidance prompts.

        This role is read-only and completes in one turn.
        """
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        trace_role_enter(DevTracePhase.SPECIALIST, self.name)
        prompt = render_prompt(
            "core/roles/research_user.md",
            question=user_text[:1500],
        )
        system_prompt = render_prompt(
            "core/roles/research_system.md",
            engineering_guidance=self._guidance,
        ).strip()
        try:
            trace_prompt(
                DevTracePhase.SPECIALIST,
                prompt_file="core/roles/research_user.md",
                model="router:auto",
            )
            trace_prompt(
                DevTracePhase.SPECIALIST,
                prompt_file="core/roles/research_system.md",
                model="router:auto",
            )
            response = await context.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system_prompt,
                max_tokens=500,
                task_type="general",
                allowed_providers=None,
            )
            trace_prompt(
                DevTracePhase.SPECIALIST,
                prompt_file="core/roles/research_user.md",
                model=str(getattr(response, "model", "") or "router:auto"),
            )
            trace_prompt(
                DevTracePhase.SPECIALIST,
                prompt_file="core/roles/research_system.md",
                model=str(getattr(response, "model", "") or "router:auto"),
            )
            text = (response.text or "").strip() or "I need a bit more detail for research."
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "research specialist llm response",
                    "selected_action": "complete",
                    "reasoning": "response generated successfully",
                },
            )
        except Exception as exc:
            logger.exception("Research specialist failed")
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "research error fallback",
                    "selected_action": "complete",
                    "reasoning": str(exc),
                },
            )
            text = f"Research failed right now: {exc}"
        trace_output(DevTracePhase.SPECIALIST, key="research_response", value=text)
        return RoleOutput(command="complete", response=text)
