from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from core.conversation_manager import Conversation, ConversationManager
from core.inbox import InboxManager, InboxMessage
from core.intent_extractor import IntentExtractor
from core.roles.base import RoleContext, RoleOutput
from core.roles.registry import build_default_registry
from core.router import Router
from core.scheduler import BackgroundScheduler
from core.trace import trace_flow
from core.tracing import clear_current_trace, trace, trace_manager
from db import store

logger = logging.getLogger("skynet.core.engine")


@dataclass(slots=True)
class EngineResult:
    conversation_id: str
    text: str


class ConversationEngine:
    """Commander-based multi-role conversation engine."""

    def __init__(self, db, provider_router, *, project_manager: Any | None = None):
        self.db = db
        self.provider_router = provider_router
        self.project_manager = project_manager

        self.conversation_manager = ConversationManager(db)
        self.intent_extractor = IntentExtractor(provider_router)
        self.scheduler = BackgroundScheduler(project_manager=project_manager)
        self.role_registry = build_default_registry(
            dependencies={
                "db": db,
                "provider_router": provider_router,
                "project_manager": project_manager,
                "scheduler": self.scheduler,
            }
        )
        self.router = Router(self.conversation_manager)
        self.inbox = InboxManager(self._process_batch)

    async def process_user_message(
        self,
        *,
        telegram_user_id: int,
        text: str,
        user_profile: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        entrypoint: str = "handle_text()",
    ) -> EngineResult:
        profile = user_profile or {}
        user = await store.ensure_user(
            self.db,
            telegram_user_id=int(telegram_user_id),
            username=str(profile.get("username") or ""),
            first_name=str(profile.get("first_name") or ""),
            last_name=str(profile.get("last_name") or ""),
        )
        conversation = await self.router.resolve_conversation_scope(
            user_id=int(user["id"]),
            requested_conversation_id=conversation_id,
        )
        trace_flow(
            "engine.user_message.received",
            telegram_user_id=telegram_user_id,
            conversation_id=conversation.id,
            text=text,
            requested_conversation_id=conversation_id,
        )
        reply = await self.inbox.append(
            conversation.id,
            text,
            metadata={
                "telegram_user_id": int(telegram_user_id),
                "entrypoint": entrypoint,
            },
        )
        return EngineResult(conversation_id=conversation.id, text=reply)

    async def start_new_conversation(self, *, telegram_user_id: int, title: str | None = None) -> Conversation:
        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        return await self.conversation_manager.create_conversation(int(user["id"]), title=title)

    async def list_user_conversations(self, *, telegram_user_id: int) -> list[Conversation]:
        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        return await self.conversation_manager.list_conversations(int(user["id"]))

    async def switch_conversation(self, *, telegram_user_id: int, conversation_id: str) -> Conversation | None:
        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        target = await self.conversation_manager.get_conversation(conversation_id)
        if not target or int(target.user_id) != int(user["id"]):
            return None
        await self.conversation_manager.set_active_conversation(int(user["id"]), target.id)
        return target

    async def clear_pending_action_for_user(self, user_id: str) -> None:
        user = await store.get_user_by_telegram_id(self.db, int(user_id))
        if not user:
            return
        conversation = await self.conversation_manager.get_or_create_active_conversation(int(user["id"]))
        await self.conversation_manager.clear_pending_action(conversation.id)

    async def shutdown(self) -> None:
        await self.scheduler.stop()

    async def _process_batch(self, conversation_id: str, batch: list[InboxMessage]) -> str:
        merged_text = "\n".join(item.text for item in batch)
        telegram_user_id = ""
        entrypoint = "handle_text()"
        if batch and isinstance(batch[0].metadata, dict):
            telegram_user_id = str(batch[0].metadata.get("telegram_user_id") or "")
            entrypoint = str(batch[0].metadata.get("entrypoint") or "handle_text()")

        trace_logger, trace_token = trace_manager.start(
            trace_id=conversation_id,
            user_id=telegram_user_id or "unknown",
            entrypoint=entrypoint,
            input_text=merged_text,
        )
        trace_flow(
            "engine.batch.start",
            conversation_id=conversation_id,
            batch_size=len(batch),
            merged_text=merged_text,
        )
        try:
            conversation = await self.conversation_manager.get_conversation(conversation_id)
            if not conversation:
                trace_flow(
                    "engine.batch.error",
                    conversation_id=conversation_id,
                    reason="conversation_not_found",
                )
                return "ERROR: conversation not found."

            await self.conversation_manager.add_message(
                conversation_id,
                role="user",
                content=merged_text,
                metadata={"source": "telegram"},
            )

            response_text = await self.run(conversation, merged_text)

            await self.conversation_manager.add_message(
                conversation_id,
                role="assistant",
                content=response_text,
                metadata={"source": "engine", "active_role": conversation.active_role},
            )
            trace_flow(
                "engine.batch.complete",
                conversation_id=conversation_id,
                response=response_text,
                active_role=conversation.active_role,
            )
            return response_text
        finally:
            trace_logger.end()
            clear_current_trace(trace_token)

    @trace(role="engine", step_name="run")
    async def run(self, conversation: Conversation, user_text: str) -> str:
        return await self._run_role_cycle(conversation, user_text)

    @trace(role="engine", step_name="select_role")
    def select_role(self, role_name: str):
        return self.role_registry.get(role_name)

    async def _run_role_cycle(self, conversation: Conversation, user_text: str) -> str:
        role_name = conversation.active_role or "igris"
        role = self.select_role(role_name)
        trace_flow(
            "engine.role_cycle.start",
            conversation_id=conversation.id,
            role=role_name,
            text=user_text,
        )

        context = RoleContext(
            db=self.db,
            provider_router=self.provider_router,
            conversation_manager=self.conversation_manager,
            conversation=conversation,
            project_manager=self.project_manager,
            scheduler=self.scheduler,
            intent_extractor=self.intent_extractor,
        )

        output = await role.handle_message(context, user_text)
        trace_flow(
            "engine.role_cycle.output",
            conversation_id=conversation.id,
            role=role_name,
            command=output.command,
            target_role=output.target_role,
            response=output.response or "",
        )
        return await self._apply_role_output(conversation, user_text, output)

    async def _apply_role_output(self, conversation: Conversation, user_text: str, output: RoleOutput) -> str:
        command = output.command
        trace_flow(
            "engine.role_output.apply",
            conversation_id=conversation.id,
            command=command,
            target_role=output.target_role,
        )

        if command == "respond":
            text = (output.response or "").strip()
            return text or "Igris acknowledged."

        if command == "delegate":
            target_role = (output.target_role or "igris").strip() or "igris"
            trace_flow(
                "engine.role_output.delegate",
                conversation_id=conversation.id,
                target_role=target_role,
                from_role=conversation.active_role,
                text=user_text,
            )
            await self.conversation_manager.set_active_role(conversation.id, target_role)
            refreshed = await self.conversation_manager.get_conversation(conversation.id)
            if not refreshed:
                trace_flow(
                    "engine.role_output.error",
                    conversation_id=conversation.id,
                    reason="state_lost_after_delegate",
                )
                return "ERROR: conversation state lost."

            specialist_output = await self.execute_specialist(refreshed, target_role, user_text)
            trace_flow(
                "engine.specialist.output",
                conversation_id=conversation.id,
                specialist_role=target_role,
                command=specialist_output.command,
                response=specialist_output.response or "",
            )
            return await self._apply_specialist_output(refreshed, specialist_output)

        if command == "continue":
            text = (output.response or "").strip()
            return text or "Please continue."

        if command == "complete":
            await self.conversation_manager.set_active_role(conversation.id, "igris")
            if output.result and output.result.get("active_project_id"):
                await self.conversation_manager.set_active_project(
                    conversation.id,
                    str(output.result["active_project_id"]),
                )
            trace_flow(
                "engine.role_output.complete",
                conversation_id=conversation.id,
                result=output.result or {},
            )
            text = (output.response or "").strip()
            return text or "Task complete. Igris resumed control."

        return "Igris could not interpret the role response."

    @trace(role="engine", step_name="execute_specialist")
    async def execute_specialist(
        self,
        conversation: Conversation,
        target_role: str,
        user_text: str,
    ) -> RoleOutput:
        specialist = self.select_role(target_role)
        spec_context = RoleContext(
            db=self.db,
            provider_router=self.provider_router,
            conversation_manager=self.conversation_manager,
            conversation=conversation,
            project_manager=self.project_manager,
            scheduler=self.scheduler,
            intent_extractor=self.intent_extractor,
        )
        return await specialist.handle_message(spec_context, user_text)

    async def _apply_specialist_output(self, conversation: Conversation, output: RoleOutput) -> str:
        trace_flow(
            "engine.specialist_output.apply",
            conversation_id=conversation.id,
            command=output.command,
            target_role=output.target_role,
            result=output.result or {},
        )
        if output.command == "complete":
            await self.conversation_manager.set_active_role(conversation.id, "igris")
            if output.result and output.result.get("active_project_id"):
                await self.conversation_manager.set_active_project(
                    conversation.id,
                    str(output.result["active_project_id"]),
                )
            return (output.response or "Task complete. Igris resumed control.").strip()

        if output.command == "continue":
            return (output.response or "Please continue.").strip()

        if output.command == "delegate":
            # Allow specialist-to-specialist delegation but keep deterministic single hop.
            target = (output.target_role or "igris").strip() or "igris"
            await self.conversation_manager.set_active_role(conversation.id, target)
            return f"Delegating to {target}."

        return (output.response or "Igris resumed control.").strip()
