from __future__ import annotations

from core.roles.base import Role, RoleContext, RoleOutput
from db import store


class CodingSpecialistRole(Role):
    name = "coding_specialist"

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        project_id = context.conversation.active_project_id
        if not project_id:
            return RoleOutput(
                command="continue",
                response="No active project in this conversation. Ask Igris to create or select a project first.",
            )

        project = await store.get_project(context.db, project_id)
        if not project:
            return RoleOutput(command="complete", response="The active project no longer exists.")

        instructions = (user_text or "").strip()
        job_id = None
        if context.scheduler is not None and hasattr(context.scheduler, "enqueue_coding_job"):
            job_id = await context.scheduler.enqueue_coding_job(
                project_id=project_id,
                requested_by="coding_specialist",
                instructions=instructions,
            )

        return RoleOutput(
            command="complete",
            response=(
                f"Coding execution queued for {project.get('display_name') or project.get('name')}. "
                f"Job: {job_id or 'n/a'}."
            ),
            result={"project_id": project_id, "job_id": job_id},
        )