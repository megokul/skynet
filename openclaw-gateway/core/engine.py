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
from core.dev_trace import (
    DevTracePhase,
    clear_trace_session,
    get_current_trace_session,
    start_trace_session,
    trace_control_flow,
    trace_context,
    trace_data_flow,
    trace_decision,
    trace_output,
    trace_role_enter,
)
from core.inbox import InboxManager, InboxMessage
from core.intent_extractor import IntentExtractor
from core.roles.base import RoleContext, RoleOutput
from core.roles.registry import build_default_registry
from core.router import Router
from core.scheduler import BackgroundScheduler
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
        trace_origin: dict[str, Any] | None = None,
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
        reply = await self.inbox.append(
            conversation.id,
            text,
            metadata={
                "telegram_user_id": int(telegram_user_id),
                "entrypoint": entrypoint,
                "trace_origin": dict(trace_origin or {}),
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
        trace_origin: dict[str, Any] = {}
        if batch and isinstance(batch[0].metadata, dict):
            telegram_user_id = str(batch[0].metadata.get("telegram_user_id") or "")
            entrypoint = str(batch[0].metadata.get("entrypoint") or "handle_text()")
            maybe_trace_origin = batch[0].metadata.get("trace_origin")
            if isinstance(maybe_trace_origin, dict):
                trace_origin = dict(maybe_trace_origin)

        trace_session, trace_token = start_trace_session(
            trace_id=conversation_id,
            user_id=telegram_user_id or "unknown",
            user_input=str(trace_origin.get("original_message") or merged_text),
        )
        trace_session.set_header_field("conversation_id", conversation_id)
        if telegram_user_id:
            trace_session.set_header_field("telegram_user_id", telegram_user_id)
        source_file = str(trace_origin.get("source_file") or "")
        source_line = int(trace_origin.get("source_line") or 0)
        source_function = str(trace_origin.get("source_function") or "")
        source_node_id: int | None = None
        if source_file and source_line and source_function:
            source_node_id = trace_control_flow(
                DevTracePhase.ENTRY,
                file=source_file,
                line=source_line,
                function=source_function,
                params={"text": str(trace_origin.get("original_message") or merged_text)},
                stack_depth=2,
            )
            trace_session.mark_source_node(source_node_id)
        trace_control_flow(DevTracePhase.ENTRY, stack_depth=2)
        trace_data_flow(
            DevTracePhase.ENTRY,
            source_name="user_text.normalized",
            source_value=str(trace_origin.get("normalized_message") or merged_text),
            target_name="inbox.merged_text",
            target_value=merged_text,
        )
        trace_output(DevTracePhase.ENTRY, key="conversation_id", value=conversation_id)
        trace_output(DevTracePhase.ENTRY, key="entrypoint", value=entrypoint)
        trace_decision(
            DevTracePhase.ENTRY,
            {
                "batch_size": len(batch),
                "reasoning": "coalesced inbox messages into a single turn",
            },
        )
        try:
            conversation = await self.conversation_manager.get_conversation(conversation_id)
            if not conversation:
                trace_output(
                    DevTracePhase.RESPONSE,
                    key="error",
                    value="conversation_not_found",
                )
                return "ERROR: conversation not found."

            trace_session.set_header_field("active_role", conversation.active_role or "igris")
            message_count_before = await self.conversation_manager.count_messages(conversation_id)
            trace_session.set_header_field("message_count_before", message_count_before)
            trace_role_enter(DevTracePhase.ROUTING, conversation.active_role or "igris")
            incoming_message_id = await self.conversation_manager.add_message(
                conversation_id,
                role="user",
                content=merged_text,
                metadata={"source": "telegram"},
            )
            if source_node_id is not None:
                trace_context(
                    DevTracePhase.ENTRY,
                    node_id=source_node_id,
                    values={
                        "conversation_id": conversation_id,
                        "incoming_message_id": incoming_message_id,
                    },
                )

            # Core role execution cycle.
            response_text = await self.run(conversation, merged_text)

            await self.conversation_manager.add_message(
                conversation_id,
                role="assistant",
                content=response_text,
                metadata={"source": "engine", "active_role": conversation.active_role},
            )
            trace_output(DevTracePhase.RESPONSE, key="assistant_response", value=response_text)
            trace_output(DevTracePhase.RESPONSE, key="active_role", value=conversation.active_role)
            message_count_after = await self.conversation_manager.count_messages(conversation_id)
            trace_session.set_header_field("message_count_after", message_count_after)
            trace_session.set_header_field("turn", message_count_after)
            final_conversation = await self.conversation_manager.get_conversation(conversation_id)
            if final_conversation is not None:
                trace_session.set_header_field("final_active_role", final_conversation.active_role)
            return response_text
        finally:
            # Always close trace even if role execution raises.
            trace_session.end()
            clear_trace_session(trace_token)

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

        run_node_id = trace_control_flow(
            DevTracePhase.ROUTING,
            params={
                "conversation_id": conversation.id,
                "active_role": conversation.active_role or "igris",
            },
            stack_depth=2,
        )
        session = get_current_trace_session()
        if session is not None:
            session.mark_run_node(run_node_id)
        trace_data_flow(
            DevTracePhase.ROUTING,
            source_name="conversation.active_role",
            source_value=conversation.active_role or "igris",
            target_name="engine.selected_role",
            target_value=conversation.active_role or "igris",
        )
        response = await self._run_role_cycle(conversation, user_text)
        trace_output(DevTracePhase.ROUTING, key="response", value=response, node_id=run_node_id)
        return response

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

        trace_control_flow(DevTracePhase.ROUTING, stack_depth=2)
        selected = self.role_registry.get(role_name)
        trace_output(DevTracePhase.ROUTING, key="selected_role", value=selected.name)
        return selected

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
        trace_control_flow(DevTracePhase.ROUTING, stack_depth=2)
        trace_role_enter(DevTracePhase.ROUTING, role_name)
        trace_decision(
            DevTracePhase.ROUTING,
            {
                "routing_rule": "conversation active role",
                "conversation_id": conversation.id,
                "selected_role": role_name,
                "reasoning": "active role owns this turn",
            },
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
        trace_output(DevTracePhase.ROUTING, key="role_output.command", value=output.command)
        trace_output(DevTracePhase.ROUTING, key="role_output.target_role", value=output.target_role or "")
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
        trace_control_flow(DevTracePhase.ROUTING, stack_depth=2)
        trace_decision(
            DevTracePhase.ROUTING,
            {
                "command": command,
                "target_role": output.target_role or "",
                "routing_rule": "RoleOutput command protocol",
            },
        )

        if command == "respond":
            text = (output.response or "").strip()
            trace_output(DevTracePhase.RESPONSE, key="response_text", value=text or "Igris acknowledged.")
            return text or "Igris acknowledged."

        if command == "delegate":
            target_role = (output.target_role or "igris").strip() or "igris"
            trace_role_enter(DevTracePhase.SPECIALIST, target_role)
            trace_decision(
                DevTracePhase.ROUTING,
                {
                    "routing_rule": "delegate to specialist",
                    "from_role": conversation.active_role or "igris",
                    "to_role": target_role,
                    "reasoning": "igris selected specialist for intent handling",
                },
            )
            await self.conversation_manager.set_active_role(conversation.id, target_role)
            refreshed = await self.conversation_manager.get_conversation(conversation.id)
            if not refreshed:
                trace_output(
                    DevTracePhase.RESPONSE,
                    key="error",
                    value="state_lost_after_delegate",
                )
                return "ERROR: conversation state lost."

            specialist_output = await self.execute_specialist(refreshed, target_role, user_text)
            trace_output(DevTracePhase.SPECIALIST, key="specialist.command", value=specialist_output.command)
            return await self._apply_specialist_output(refreshed, specialist_output)

        if command == "continue":
            text = (output.response or "").strip()
            trace_output(DevTracePhase.RESPONSE, key="response_text", value=text or "Please continue.")
            return text or "Please continue."

        if command == "complete":
            trace_decision(
                DevTracePhase.RESTORATION,
                {
                    "routing_rule": "complete -> restore igris",
                    "from_role": conversation.active_role or "unknown",
                    "to_role": "igris",
                },
            )
            await self.conversation_manager.set_active_role(conversation.id, "igris")
            if output.result and output.result.get("active_project_id"):
                await self.conversation_manager.set_active_project(
                    conversation.id,
                    str(output.result["active_project_id"]),
                )
            text = (output.response or "").strip()
            trace_output(DevTracePhase.RESPONSE, key="response_text", value=text or "Task complete. Igris resumed control.")
            return text or "Task complete. Igris resumed control."

        trace_output(DevTracePhase.RESPONSE, key="response_text", value="Igris could not interpret the role response.")
        return "Igris could not interpret the role response."

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

        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        trace_role_enter(DevTracePhase.SPECIALIST, target_role)
        trace_data_flow(
            DevTracePhase.SPECIALIST,
            source_name="delegate.target_role",
            source_value=target_role,
            target_name="specialist.execution_role",
            target_value=target_role,
        )
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
        result = await specialist.handle_message(spec_context, user_text)
        trace_output(DevTracePhase.SPECIALIST, key="specialist_result.command", value=result.command)
        return result

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

        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        trace_decision(
            DevTracePhase.SPECIALIST,
            {
                "command": output.command,
                "target_role": output.target_role or "",
                "result": output.result or {},
            },
        )
        if output.command == "complete":
            trace_decision(
                DevTracePhase.RESTORATION,
                {
                    "routing_rule": "specialist complete -> restore igris",
                    "from_role": conversation.active_role or "unknown",
                    "to_role": "igris",
                },
            )
            await self.conversation_manager.set_active_role(conversation.id, "igris")
            if output.result and output.result.get("active_project_id"):
                await self.conversation_manager.set_active_project(
                    conversation.id,
                    str(output.result["active_project_id"]),
                )
            text = (output.response or "Task complete. Igris resumed control.").strip()
            trace_output(DevTracePhase.RESPONSE, key="response_text", value=text)
            return text

        if output.command == "continue":
            text = (output.response or "Please continue.").strip()
            trace_output(DevTracePhase.RESPONSE, key="response_text", value=text)
            return text

        if output.command == "delegate":
            # Allow specialist-to-specialist delegation but keep deterministic
            # single-hop behavior per user turn.
            target = (output.target_role or "igris").strip() or "igris"
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "specialist delegation",
                    "from_role": conversation.active_role or "unknown",
                    "to_role": target,
                },
            )
            await self.conversation_manager.set_active_role(conversation.id, target)
            text = f"Delegating to {target}."
            trace_output(DevTracePhase.RESPONSE, key="response_text", value=text)
            return text

        text = (output.response or "Igris resumed control.").strip()
        trace_output(DevTracePhase.RESPONSE, key="response_text", value=text)
        return text
