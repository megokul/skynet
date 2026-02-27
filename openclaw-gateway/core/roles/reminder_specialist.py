"""
Reminder specialist role.

This role extracts reminder payloads, manages missing-field follow-up prompts,
and persists reminders into conversation-linked storage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.dev_trace import (
    DevTracePhase,
    trace_control_flow,
    trace_decision,
    trace_output,
    trace_role_enter,
)
from core.prompt_library import load_prompt
from core.roles.base import Role, RoleContext, RoleOutput
from db import store


class ReminderSpecialistRole(Role):
    """
    ReminderSpecialistRole.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `ReminderSpecialistRole`.
    """

    name = "reminder_specialist"
    _payload_extract_instruction = load_prompt("core/roles/reminder_payload_extract_instruction.md")

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Reminder role state machine.

        - If waiting for missing title, consume title and save reminder.
        - Otherwise extract structured payload and either save or ask follow-up.
        """
        conversation = context.conversation
        pending = conversation.pending_question or {}
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        trace_role_enter(DevTracePhase.SPECIALIST, self.name)
        trace_output(DevTracePhase.SPECIALIST, key="pending_type", value=str(pending.get("type") or ""))
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
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "pending reminder title continuation",
                    "selected_action": "complete",
                    "reasoning": "title provided after follow-up question",
                },
            )
            trace_output(DevTracePhase.SPECIALIST, key="reminder_id", value=reminder["id"])
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
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "payload confidence gate",
                    "classifier_confidence": confidence,
                    "selected_action": "ask_for_title",
                    "reasoning": "title missing or confidence below threshold",
                },
            )
            await context.conversation_manager.set_pending_question(
                conversation.id,
                {
                    "type": "need_reminder_title",
                    "choices": [],
                    "metadata": {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
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
        trace_decision(
            DevTracePhase.SPECIALIST,
            {
                "routing_rule": "payload accepted",
                "classifier_confidence": confidence,
                "selected_action": "create reminder and complete",
            },
        )
        trace_output(DevTracePhase.SPECIALIST, key="reminder_id", value=reminder["id"])
        trace_output(DevTracePhase.SPECIALIST, key="reminder_due_at", value=due_at or "")

        when = reminder.get("due_at") or "no due time"
        return RoleOutput(
            command="complete",
            response=f"Reminder created: {reminder['title']} (due: {when}).",
            result={"reminder_id": reminder["id"]},
        )

    async def _extract_payload(self, context: RoleContext, user_text: str) -> dict:
        """
        Extract reminder payload with confidence score.

        Fallback behavior always returns a dict so caller logic remains simple.
        """
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
