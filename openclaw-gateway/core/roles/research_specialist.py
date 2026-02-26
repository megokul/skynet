from __future__ import annotations

from core.prompt_library import engineering_prompt_block, render_prompt
from core.roles.base import Role, RoleContext, RoleOutput


class ResearchSpecialistRole(Role):
    name = "research_specialist"
    _guidance = engineering_prompt_block()

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
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
            text = f"Research failed right now: {exc}"
        return RoleOutput(command="complete", response=text)
