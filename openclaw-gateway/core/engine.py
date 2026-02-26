"""
Top-level conversation orchestration runtime.

Flow overview:
1. Resolve user + conversation scope.
2. Queue inbound messages through per-conversation inbox workers.
3. Run role cycle (`igris` + specialists) and persist assistant output.
4. Emit trace events for each stage.
"""

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
    """
    EngineResult.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `EngineResult`.
    """

    conversation_id: str
    text: str


class ConversationEngine:
    """Commander-based multi-role conversation engine."""

    def __init__(self, db, provider_router, *, project_manager: Any | None = None):
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `db`: input used by this function to compute or route work.
        - `provider_router`: input used by this function to compute or route work.
        - `project_manager`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

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
        # Ensure user exists/up-to-date before any conversation routing so
        # conversation ownership checks remain stable.
        """
        Process user message.
        
        Purpose:
        - Implement `process_user_message` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `telegram_user_id`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        - `user_profile`: input used by this function to compute or route work.
        - `conversation_id`: input used by this function to compute or route work.
        - `entrypoint`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `EngineResult` when available; otherwise side effects only.
        """

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
        """
        Start new conversation.
        
        Purpose:
        - Implement `start_new_conversation` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `telegram_user_id`: input used by this function to compute or route work.
        - `title`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `Conversation` when available; otherwise side effects only.
        """

        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        return await self.conversation_manager.create_conversation(int(user["id"]), title=title)

    async def list_user_conversations(self, *, telegram_user_id: int) -> list[Conversation]:
        """
        List user conversations.
        
        Purpose:
        - Implement `list_user_conversations` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `telegram_user_id`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `list[Conversation]` when available; otherwise side effects only.
        """

        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        return await self.conversation_manager.list_conversations(int(user["id"]))

    async def switch_conversation(self, *, telegram_user_id: int, conversation_id: str) -> Conversation | None:
        """
        Switch conversation.
        
        Purpose:
        - Implement `switch_conversation` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `telegram_user_id`: input used by this function to compute or route work.
        - `conversation_id`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `Conversation | None` when available; otherwise side effects only.
        """

        user = await store.ensure_user(self.db, telegram_user_id=int(telegram_user_id))
        target = await self.conversation_manager.get_conversation(conversation_id)
        if not target or int(target.user_id) != int(user["id"]):
            return None
        await self.conversation_manager.set_active_conversation(int(user["id"]), target.id)
        return target

    async def clear_pending_action_for_user(self, user_id: str) -> None:
        """
        Clear pending action for user.
        
        Purpose:
        - Implement `clear_pending_action_for_user` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `user_id`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        user = await store.get_user_by_telegram_id(self.db, int(user_id))
        if not user:
            return
        conversation = await self.conversation_manager.get_or_create_active_conversation(int(user["id"]))
        await self.conversation_manager.clear_pending_action(conversation.id)

    async def shutdown(self) -> None:
        """
        Shutdown.
        
        Purpose:
        - Implement `shutdown` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        await self.scheduler.stop()

    async def _process_batch(self, conversation_id: str, batch: list[InboxMessage]) -> str:
        # Inbox may coalesce rapid messages into one batch; they are processed
        # as a single user turn in arrival order.
        """
        Process batch.
        
        Purpose:
        - Implement `_process_batch` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `conversation_id`: input used by this function to compute or route work.
        - `batch`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

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

            # Core role execution cycle.
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
            # Always close trace even if role execution raises.
            trace_logger.end()
            clear_current_trace(trace_token)

    @trace(role="engine", step_name="run")
    async def run(self, conversation: Conversation, user_text: str) -> str:
        """
        Run.
        
        Purpose:
        - Implement `run` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `conversation`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        return await self._run_role_cycle(conversation, user_text)

    @trace(role="engine", step_name="select_role")
    def select_role(self, role_name: str):
        """
        Select role.
        
        Purpose:
        - Implement `select_role` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `role_name`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        return self.role_registry.get(role_name)

    async def _run_role_cycle(self, conversation: Conversation, user_text: str) -> str:
        """
        Run role cycle.
        
        Purpose:
        - Implement `_run_role_cycle` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `conversation`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

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
        # RoleOutput is a compact command protocol:
        # - respond: assistant text directly to user
        # - delegate: transfer active role and run specialist once
        # - continue: specialist keeps control and asks follow-up
        # - complete: specialist finished; control returns to commander
        """
        Apply role output.
        
        Purpose:
        - Implement `_apply_role_output` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `conversation`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        - `output`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

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
        # Specialist execution is isolated through a fresh RoleContext snapshot.
        """
        Execute specialist.
        
        Purpose:
        - Implement `execute_specialist` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `conversation`: input used by this function to compute or route work.
        - `target_role`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `RoleOutput` when available; otherwise side effects only.
        """

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
        """
        Apply specialist output.
        
        Purpose:
        - Implement `_apply_specialist_output` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `conversation`: input used by this function to compute or route work.
        - `output`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

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
            # Allow specialist-to-specialist delegation but keep deterministic
            # single-hop behavior per user turn.
            target = (output.target_role or "igris").strip() or "igris"
            await self.conversation_manager.set_active_role(conversation.id, target)
            return f"Delegating to {target}."

        return (output.response or "Igris resumed control.").strip()
