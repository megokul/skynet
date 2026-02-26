from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from core.prompt_library import load_prompt
from core.roles.base import Role, RoleContext, RoleOutput
from core.trace import trace_flow
from core.tracing import trace
from db import store

logger = logging.getLogger("skynet.core.roles.reminder")

class ReminderSpecialistRole(Role):
    name = "reminder_specialist"
    _payload_extract_instruction = load_prompt("core/roles/reminder_payload_extract_instruction.md")

    @trace(role="reminder_specialist", step_name="reminder_specialist_handle")
    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        conversation = context.conversation
        pending = conversation.pending_question or {}
        trace_flow(
            "role.reminder.handle.start",
            conversation_id=conversation.id,
            pending_type=str(pending.get("type") or ""),
            text=user_text,
        )
        if str(pending.get("type") or "") == "need_reminder_title":
            title = (user_text or "").strip()
            if not title:
                return RoleOutput(command="continue", response="What reminder title should I save?")
            reminder = await store.create_reminder(
                context.db,
                user_id=conversation.user_id,
                conversation_id=conversation.id,
                title=title,
                due_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                notes="",
            )
            await context.conversation_manager.clear_pending_question(conversation.id)
            trace_flow(
                "role.reminder.saved_from_pending",
                conversation_id=conversation.id,
                reminder_id=reminder["id"],
                title=title,
            )
            return RoleOutput(
                command="complete",
                response=f"Reminder saved: {reminder['title']}.",
                result={"reminder_id": reminder["id"]},
            )

        payload = await self._extract_payload(context, user_text)
        title = str(payload.get("title") or "").strip()
        due_at = str(payload.get("due_at") or "").strip() or None
        notes = str(payload.get("notes") or "").strip()
        confidence = float(payload.get("confidence") or 0.0)

        if not title or confidence < 0.45:
            await context.conversation_manager.set_pending_question(
                conversation.id,
                {
                    "type": "need_reminder_title",
                    "choices": [],
                    "metadata": {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            trace_flow(
                "role.reminder.ask_title",
                conversation_id=conversation.id,
                confidence=confidence,
                extracted_title=title,
            )
            return RoleOutput(command="continue", response="What reminder should I create?")

        reminder = await store.create_reminder(
            context.db,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            title=title,
            due_at=due_at,
            notes=notes,
        )
        await context.conversation_manager.clear_pending_question(conversation.id)
        trace_flow(
            "role.reminder.created",
            conversation_id=conversation.id,
            reminder_id=reminder["id"],
            title=title,
            due_at=due_at or "",
        )

        when = reminder.get("due_at") or "no due time"
        return RoleOutput(
            command="complete",
            response=f"Reminder created: {reminder['title']} (due: {when}).",
            result={"reminder_id": reminder["id"]},
        )

    @trace(
        role="reminder_specialist",
        prompt="prompts/core/roles/reminder_payload_extract_instruction.md",
        step_name="extract_reminder_payload",
    )
    async def _extract_payload(self, context: RoleContext, user_text: str) -> dict:
        extractor = context.intent_extractor
        if extractor is None:
            return {"title": (user_text or "").strip(), "confidence": 0.5}

        payload = await extractor.extract_payload(
            user_text,
            {"title": "", "due_at": "", "notes": "", "confidence": 0.0},
            instruction=self._payload_extract_instruction,
        )
        if not payload:
            return {"title": (user_text or "").strip(), "confidence": 0.3}
        return payload
