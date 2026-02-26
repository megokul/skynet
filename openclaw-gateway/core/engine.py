from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.conversation_manager import Conversation, ConversationManager
from core.inbox import InboxManager, InboxMessage
from core.router import Router, WRITE_INTENTS
from db import store
from intents.classifier import IntentClassifier, PayloadExtractor
from intents.handlers import WriteIntentHandlers


@dataclass
class EngineResult:
    conversation_id: str
    text: str


class ConversationEngine:
    """
    Enterprise-style conversation engine:
    - explicit conversation boundary
    - deterministic router before LLM reasoning
    - per-conversation inbox queue
    """

    def __init__(self, db, provider_router):
        self.db = db
        self.provider_router = provider_router
        self.conversation_manager = ConversationManager(db)
        self.intent_classifier = IntentClassifier()
        self.extractor = PayloadExtractor(provider_router)
        self.write_handlers = WriteIntentHandlers(db, self.conversation_manager)
        self.router = Router(execute_write_intent=self._execute_write_intent)
        self.inbox = InboxManager(self._process_batch)

    async def process_user_message(
        self,
        *,
        telegram_user_id: int,
        text: str,
        user_profile: dict[str, Any] | None = None,
    ) -> EngineResult:
        profile = user_profile or {}
        user = await store.ensure_user(
            self.db,
            telegram_user_id=int(telegram_user_id),
            username=str(profile.get("username") or ""),
            first_name=str(profile.get("first_name") or ""),
            last_name=str(profile.get("last_name") or ""),
        )
        conversation = await self.conversation_manager.get_or_create_active_conversation(user["id"])
        reply = await self.inbox.append_message(
            conversation.conversation_id,
            text,
            metadata={"telegram_user_id": int(telegram_user_id)},
        )
        return EngineResult(conversation_id=conversation.conversation_id, text=reply)

    async def start_new_conversation(self, *, telegram_user_id: int, title: str | None = None) -> Conversation:
        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        return await self.conversation_manager.create_conversation(user["id"], title=title)

    async def list_user_conversations(self, *, telegram_user_id: int) -> list[Conversation]:
        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        return await self.conversation_manager.list_conversations(user["id"])

    async def switch_conversation(self, *, telegram_user_id: int, conversation_id: str) -> Conversation | None:
        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        target = await self.conversation_manager.get_conversation(conversation_id)
        if not target or target.user_id != user["id"]:
            return None
        await self.conversation_manager.set_active_conversation(user["id"], conversation_id)
        return target

    async def _process_batch(self, conversation_id: str, batch: list[InboxMessage]) -> str:
        text = "\n".join(msg.message for msg in batch)
        conversation = await self.conversation_manager.get_conversation(conversation_id)
        if not conversation:
            return "ERROR: conversation not found."

        await self.conversation_manager.add_message(conversation_id, role="user", content=text)
        response = await self._route_and_respond(conversation, text)
        await self.conversation_manager.add_message(conversation_id, role="assistant", content=response)
        return response

    async def _route_and_respond(self, conversation: Conversation, text: str) -> str:
        pending_question = conversation.pending_question or {}
        if self._is_pending_question_active(pending_question):
            handled = await self._handle_pending_question(conversation, text, pending_question)
            if handled is not None:
                return handled

        intent = self.intent_classifier.classify_intent(text)
        project_scope = self.router.resolve_project_scope(conversation=conversation, user_text=text)

        payload = await self._extract_payload(intent, text)
        decision = self.router.route_intent(
            conversation=conversation,
            intent=intent,
            user_text=text,
            payload=payload,
        )

        if decision.requires_question:
            question = self._build_question(decision.question_type)
            await self.conversation_manager.set_pending_question(
                conversation.conversation_id,
                {
                    "type": decision.question_type,
                    "choices": self._question_choices(decision.question_type),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                    "metadata": {"intent": intent, "raw_text": text},
                },
            )
            return question

        if intent in WRITE_INTENTS and decision.execute_now:
            if intent == "propose_idea":
                confidence = float(payload.get("confidence") or 0.0)
                if confidence < 0.6:
                    await self.conversation_manager.set_pending_question(
                        conversation.conversation_id,
                        {
                            "type": "need_idea_text",
                            "choices": [],
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                            "metadata": {"intent": intent},
                        },
                    )
                    return "What should I add as the idea?"

            await self.conversation_manager.clear_pending_question(conversation.conversation_id)
            return await self.router.execute_write_intent(
                intent=intent,
                payload=payload,
                context={"conversation_id": conversation.conversation_id},
            )

        # Reasoning layer
        return await self._reason(conversation, intent, text, project_scope)

    async def _execute_write_intent(self, intent: str, payload: dict[str, Any], context: Any) -> str:
        return await self.write_handlers.execute(
            intent,
            payload,
            conversation_id=context["conversation_id"],
        )

    async def _reason(
        self,
        conversation: Conversation,
        intent: str,
        user_text: str,
        project_scope,
    ) -> str:
        system = (
            "You are a planning/reasoning assistant. "
            "Do not modify project data directly. "
            "Provide guidance based on current conversation context."
        )
        prompt = (
            f"Conversation ID: {conversation.conversation_id}\n"
            f"Intent: {intent}\n"
            f"Active Project ID: {project_scope.active_project_id}\n"
            f"User: {user_text}"
        )
        try:
            response = await self.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system,
                max_tokens=600,
                task_type="general",
                allowed_providers=None,
            )
            return (response.text or "").strip() or "I need a bit more detail to help."
        except Exception:
            return "I could not complete reasoning right now. Please try again."

    async def _extract_payload(self, intent: str, text: str) -> dict[str, Any]:
        if intent == "propose_idea":
            schema = {"idea_text": "string", "confidence": 0.0}
            data = await self.extractor.extract_payload(text, schema)
            idea_text = str(data.get("idea_text") or text).strip()
            confidence = float(data.get("confidence") or (0.7 if idea_text else 0.0))
            return {"idea_text": idea_text, "confidence": max(0.0, min(confidence, 1.0))}
        if intent == "add_note":
            schema = {"note_text": "string", "confidence": 0.0}
            data = await self.extractor.extract_payload(text, schema)
            return {"note_text": str(data.get("note_text") or text).strip(), "confidence": float(data.get("confidence") or 0.7)}
        if intent == "add_task":
            schema = {"title": "string", "description": "string"}
            data = await self.extractor.extract_payload(text, schema)
            return {"title": str(data.get("title") or "").strip(), "description": str(data.get("description") or "").strip()}
        return {}

    async def _handle_pending_question(
        self,
        conversation: Conversation,
        text: str,
        question: dict[str, Any],
    ) -> str | None:
        qtype = str(question.get("type") or "")
        lowered = (text or "").strip().lower()

        if qtype == "choose_project":
            if lowered in {"new", "new one"}:
                await self.conversation_manager.set_pending_question(
                    conversation.conversation_id,
                    {
                        "type": "need_project_name",
                        "choices": [],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                        "metadata": {},
                    },
                )
                return "What should I name the new project?"
            if lowered in {"existing", "same", "current"} and conversation.active_project_id:
                await self.conversation_manager.clear_pending_question(conversation.conversation_id)
                return "Okay, using the active project. What should I add as the idea?"
            return None

        if qtype == "need_idea_text":
            await self.conversation_manager.clear_pending_question(conversation.conversation_id)
            return await self._execute_write_intent(
                "propose_idea",
                {"idea_text": text.strip(), "confidence": 1.0},
                {"conversation_id": conversation.conversation_id},
            )

        if qtype == "need_project_name":
            name = text.strip()
            if not name:
                return "Please provide a project name."
            project = await store.create_project(self.db, name=name, display_name=name, local_path="")
            await self.conversation_manager.set_active_project(conversation.conversation_id, project["id"])
            await self.conversation_manager.clear_pending_question(conversation.conversation_id)
            return f"Project '{name}' created and set as active for this conversation."

        return None

    def _is_pending_question_active(self, question: dict[str, Any]) -> bool:
        if not question:
            return False
        expires = question.get("expires_at")
        if not expires:
            return True
        try:
            dt = datetime.fromisoformat(str(expires))
        except (TypeError, ValueError):
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc)

    def _build_question(self, qtype: str | None) -> str:
        if qtype == "choose_project":
            return "Should I use the active project or create a new project?"
        if qtype == "need_payload":
            return "I need more detail before I can save that. What should I add exactly?"
        return "Could you clarify your request?"

    def _question_choices(self, qtype: str | None) -> list[str]:
        if qtype == "choose_project":
            return ["existing", "new"]
        return []
