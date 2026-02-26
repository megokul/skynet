from __future__ import annotations

from typing import Any

from core.conversation_manager import ConversationManager
from db import store


class WriteIntentHandlers:
    def __init__(self, db, conversation_manager: ConversationManager):
        self.db = db
        self.conversation_manager = conversation_manager

    async def execute(
        self,
        intent: str,
        payload: dict[str, Any],
        *,
        conversation_id: str,
    ) -> str:
        conversation = await self.conversation_manager.get_conversation(conversation_id)
        if not conversation:
            return "ERROR: conversation not found."

        if intent == "propose_idea":
            return await self.handle_propose_idea(conversation, payload)
        if intent == "add_note":
            return await self.handle_add_note(conversation, payload)
        if intent == "add_task":
            return await self.handle_add_task(conversation, payload)
        return f"ERROR: unsupported write intent '{intent}'."

    async def handle_propose_idea(self, conversation, payload: dict[str, Any]) -> str:
        project_id = conversation.active_project_id
        if not project_id:
            return "ERROR: no active project in this conversation."

        idea_text = str(payload.get("idea_text") or "").strip()
        if not idea_text:
            return "ERROR: idea_text is required."

        project = await store.get_project(self.db, project_id)
        if not project:
            return "ERROR: active project not found."

        await store.add_idea(self.db, project_id, idea_text)
        ideas = await store.get_ideas(self.db, project_id)
        await self.conversation_manager.add_message(
            conversation.conversation_id,
            role="assistant",
            content=f"Idea added to '{project.get('display_name')}'. Total ideas: {len(ideas)}.",
            metadata={"intent": "propose_idea"},
        )
        return f"Idea added to '{project.get('display_name')}'."

    async def handle_add_note(self, conversation, payload: dict[str, Any]) -> str:
        note_text = str(payload.get("note_text") or "").strip()
        if not note_text:
            return "ERROR: note_text is required."
        await self.conversation_manager.add_message(
            conversation.conversation_id,
            role="assistant",
            content=f"Note saved: {note_text}",
            metadata={"intent": "add_note", "note": True},
        )
        return "Note saved."

    async def handle_add_task(self, conversation, payload: dict[str, Any]) -> str:
        project_id = conversation.active_project_id
        if not project_id:
            return "ERROR: no active project in this conversation."

        title = str(payload.get("title") or "").strip()
        if not title:
            return "ERROR: task title is required."

        plan = await store.get_active_plan(self.db, project_id)
        if not plan:
            return "ERROR: no active plan; generate a plan first."

        await store.create_tasks(
            self.db,
            project_id=project_id,
            plan_id=int(plan["id"]),
            tasks=[{"milestone": payload.get("milestone", "General"), "title": title, "description": payload.get("description", "")}],
        )
        return "Task added."
