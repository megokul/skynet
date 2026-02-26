from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.roles.base import Role, RoleContext, RoleOutput
from db import store


_INVALID_NAMES = {
    "today",
    "tomorrow",
    "now",
    "new",
    "project",
    "app",
    "application",
    "one",
    "it",
}


def _slugify_name(name: str) -> str:
    raw = (name or "").strip().lower()
    chars: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_"}:
            chars.append(ch)
        elif ch.isspace():
            chars.append("-")
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:64]


class ProjectSpecialistRole(Role):
    name = "project_specialist"

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        conversation = context.conversation
        pending = conversation.pending_question or {}
        pending_type = str(pending.get("type") or "")

        if pending_type == "need_project_name":
            return await self._handle_project_name(context, user_text)

        if pending_type == "need_project_requirements":
            return await self._handle_requirements(context, user_text)

        if conversation.active_project_id:
            return await self._append_idea_to_active(context, user_text)

        await context.conversation_manager.set_pending_question(
            conversation.id,
            {
                "type": "need_project_name",
                "choices": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {},
            },
        )
        return RoleOutput(
            command="continue",
            response="What should I name the new project?",
        )

    async def _handle_project_name(self, context: RoleContext, user_text: str) -> RoleOutput:
        conversation = context.conversation
        proposed = (user_text or "").strip().strip(".!,?;:")
        if not proposed:
            return RoleOutput(command="continue", response="Please share a project name.")

        normalized = _slugify_name(proposed)
        if not normalized or normalized in _INVALID_NAMES:
            return RoleOutput(
                command="continue",
                response="That name is too ambiguous. Please provide a specific project name.",
            )

        project = await store.get_project_by_name(context.db, normalized)
        if project is None:
            if context.project_manager is not None and hasattr(context.project_manager, "create_project"):
                project = await context.project_manager.create_project(proposed)
            else:
                project = await store.create_project(
                    context.db,
                    name=normalized,
                    display_name=proposed,
                    local_path="",
                )

        await context.conversation_manager.set_active_project(conversation.id, project["id"])
        await context.conversation_manager.set_pending_question(
            conversation.id,
            {
                "type": "need_project_requirements",
                "choices": [],
                "metadata": {"project_id": project["id"]},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        return RoleOutput(
            command="continue",
            response=(
                f"Project '{project.get('display_name') or project.get('name')}' is ready. "
                "What should this project do?"
            ),
            result={"project_id": project["id"]},
        )

    async def _handle_requirements(self, context: RoleContext, user_text: str) -> RoleOutput:
        conversation = context.conversation
        project_id = conversation.active_project_id
        if not project_id:
            await context.conversation_manager.set_pending_question(
                conversation.id,
                {
                    "type": "need_project_name",
                    "choices": [],
                    "metadata": {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return RoleOutput(command="continue", response="I need a project name before requirements.")

        requirements = (user_text or "").strip()
        if not requirements:
            return RoleOutput(command="continue", response="Please describe the project requirements.")

        await store.add_idea(context.db, project_id, requirements)
        await store.update_project(context.db, project_id, description=requirements)
        await context.conversation_manager.clear_pending_question(conversation.id)

        return RoleOutput(
            command="complete",
            response="Requirements captured. Igris is back in command.",
            result={"project_id": project_id, "requirements": requirements},
        )

    async def _append_idea_to_active(self, context: RoleContext, user_text: str) -> RoleOutput:
        project_id = context.conversation.active_project_id
        idea_text = (user_text or "").strip()
        if not idea_text:
            return RoleOutput(command="continue", response="What idea should I add to the active project?")

        await store.add_idea(context.db, project_id, idea_text)
        project = await store.get_project(context.db, project_id)
        name = (project or {}).get("display_name") or (project or {}).get("name") or "the active project"
        return RoleOutput(
            command="complete",
            response=f"Idea added to {name}. Igris is back in command.",
            result={"project_id": project_id, "idea_text": idea_text},
        )