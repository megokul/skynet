"""Primary LLM-first orchestrator for Telegram conversation handling.

Purpose:
- Coordinate session loading, intent classification, context building, and tool execution.
- Manage per-user/per-conversation inbox batching and sequential processing.
- Enforce write gating, pending questions/actions, and persistence of conversation turns.

How it works:
- Queues inbound messages and drains them in deterministic worker loops.
- Resolves scope/invariants before executing write intents.
- Runs iterative model/tool rounds, persists outcomes, and updates session metadata.

Why this exists:
- Central orchestration keeps safety rules and side effects consistent.
- Inbox serialization prevents race conditions in rapid multi-message bursts.
- Explicit state transitions make production debugging and audits practical."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import re
import time
from typing import Any
import uuid

import bot_config as cfg
from bot import state
from bot.context import ContextBuilder, ContextPackage
from bot.intent import ClassifiedIntent, IntentClassifier
from bot.invariants import (
    PendingAction,
    PendingQuestion,
    RoutingDecision,
    ScopeResolution,
    detect_switch_intent,
    enforce_continuity,
    resolve_scope,
)
from bot.memory import GapTier, _append_user_conversation, _load_recent_conversation_messages
from bot.mode import Mode, ToolPolicyGate, select_mode
from bot.session import Session, SessionLoader, get_pending_action, get_pending_question
from core.prompt_library import load_prompt, render_prompt
from skills.base import SkillContext

logger = logging.getLogger(__name__)

WRITE_INTENTS: set[str] = {"propose_idea"}
IDEA_EXTRACT_CONFIDENCE_THRESHOLD = 0.60
COALESCE_WINDOW_SECONDS = 0.8
INBOX_IDLE_TIMEOUT_SECONDS = 300
PENDING_QUESTION_TTL = timedelta(minutes=15)
PENDING_ACTION_TTL = timedelta(hours=24)
_EMPTY_REPLY_SENTINEL = ""
_IDEA_EXTRACT_PROMPT = "bot/orchestrator/idea_extract_user.md"
_FORCE_SUMMARY_USER_PROMPT = load_prompt("bot/orchestrator/force_summary_user.md")

_APPROVE_PLAN_RE = re.compile(r"\b(yes|approve|approved|go ahead|start|begin|proceed|do it)\b", re.IGNORECASE)
_REJECT_PLAN_RE = re.compile(r"\b(no|cancel|stop|not now|don't|do not)\b", re.IGNORECASE)
_APPROVAL_PROMPT_RE = re.compile(r"\b(approve|approval|tap|start)\b.*\b(plan|execution|coding)\b", re.IGNORECASE)
_INVALID_PROJECT_NAME_HINTS: set[str] = {
    "today",
    "tomorrow",
    "tonight",
    "now",
    "later",
    "soon",
    "project",
    "app",
    "application",
    "new",
    "different",
    "this",
    "that",
    "it",
    "one",
    "something",
    "anything",
    "here",
    "there",
}


@dataclass
class _ExecutionResult:
    """
    ExecutionResult.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_ExecutionResult`.
    """

    text: str
    tool_outcomes: list[dict[str, str]] = field(default_factory=list)


@dataclass
class _InboundMessage:
    """
    InboundMessage.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_InboundMessage`.
    """

    update: Any
    text: str
    future: asyncio.Future[str]
    received_at: float
    is_command: bool


@dataclass
class _UserInbox:
    """
    UserInbox.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_UserInbox`.
    """

    queue: deque[_InboundMessage] = field(default_factory=deque)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_activity: float = field(default_factory=time.monotonic)


class Orchestrator:
    """
    Orchestrator.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `Orchestrator`.
    """

    def __init__(
        self,
        db,
        project_manager,
        provider_router,
        skill_registry,
        gateway_api_url,
        chat_provider_allowlist,
    ):
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
        - `project_manager`: input used by this function to compute or route work.
        - `provider_router`: input used by this function to compute or route work.
        - `skill_registry`: input used by this function to compute or route work.
        - `gateway_api_url`: input used by this function to compute or route work.
        - `chat_provider_allowlist`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.db = db
        self.provider_router = provider_router
        self.skill_registry = skill_registry
        self.gateway_api_url = gateway_api_url
        self.chat_provider_allowlist = chat_provider_allowlist
        self.session_loader = SessionLoader(db, project_manager)
        self.intent_classifier = IntentClassifier(provider_router)
        self.context_builder = ContextBuilder(db, skill_registry, chat_provider_allowlist)
        self.tool_gate = ToolPolicyGate()

        self._user_inboxes: dict[str, _UserInbox] = {}
        self._user_workers: dict[str, asyncio.Task[None]] = {}
        self._inbox_map_lock = asyncio.Lock()
        self._coalesce_window_seconds = COALESCE_WINDOW_SECONDS

    async def handle(self, update, text: str) -> str:
        """
        Handle.
        
        Purpose:
        - Implement `handle` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        user_id = str(update.effective_user.id)
        inbox_key = await self._resolve_inbox_key(update)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        inbound = _InboundMessage(
            update=update,
            text=text,
            future=future,
            received_at=time.monotonic(),
            is_command=(text or "").strip().startswith("/"),
        )

        inbox = await self._ensure_user_inbox(inbox_key)
        async with inbox.lock:
            inbox.queue.append(inbound)
            inbox.last_activity = time.monotonic()
            inbox.event.set()

        try:
            return await future
        except Exception as exc:
            logger.exception("Failed waiting for inbox response user=%s: %s", user_id, exc)
            return "Something went wrong. Please try again."

    async def _resolve_inbox_key(self, update) -> str:
        """
        Resolve inbox key.
        
        Purpose:
        - Implement `_resolve_inbox_key` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        conversation_id = await self._get_or_create_active_conversation_id(update)
        if conversation_id:
            return f"conv:{conversation_id}"
        return f"user:{update.effective_user.id}"

    async def _get_or_create_active_conversation_id(self, update) -> str | None:
        """
        Get or create active conversation id.
        
        Purpose:
        - Implement `_get_or_create_active_conversation_id` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str | None` when available; otherwise side effects only.
        """

        if self.db is None:
            return None

        user = getattr(update, "effective_user", None)
        if not user:
            return None

        try:
            from db import store

            user_row = await store.ensure_user(
                self.db,
                telegram_user_id=int(user.id),
                username=str(getattr(user, "username", "") or ""),
                first_name=str(getattr(user, "first_name", "") or ""),
                last_name=str(getattr(user, "last_name", "") or ""),
            )
            active_id = await store.get_user_active_conversation(
                self.db,
                user_id=int(user_row["id"]),
            )
            if active_id:
                session = await store.get_conversation_session(self.db, conversation_id=active_id)
                if session:
                    return active_id

            title = datetime.now(timezone.utc).strftime("Conversation %Y-%m-%d %H:%M")
            created = await store.create_conversation_session(
                self.db,
                user_id=int(user_row["id"]),
                title=title,
            )
            await store.set_user_active_conversation(
                self.db,
                user_id=int(user_row["id"]),
                conversation_id=created["conversation_id"],
            )
            return str(created["conversation_id"])
        except Exception:
            logger.exception("Failed resolving active conversation for user=%s", getattr(user, "id", "unknown"))
            return None

    async def _ensure_user_inbox(self, user_id: str) -> _UserInbox:
        """
        Ensure user inbox.
        
        Purpose:
        - Implement `_ensure_user_inbox` within this module's workflow.
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
        - Return value typed as `_UserInbox` when available; otherwise side effects only.
        """

        async with self._inbox_map_lock:
            inbox = self._user_inboxes.get(user_id)
            if inbox is None:
                inbox = _UserInbox()
                self._user_inboxes[user_id] = inbox

            worker = self._user_workers.get(user_id)
            if worker is None or worker.done():
                self._user_workers[user_id] = asyncio.create_task(
                    self._drain_user_inbox(user_id, inbox),
                    name=f"orchestrator-inbox-{user_id}",
                )
            return inbox

    async def _drain_user_inbox(self, user_id: str, inbox: _UserInbox) -> None:
        """
        Drain user inbox.
        
        Purpose:
        - Implement `_drain_user_inbox` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `user_id`: input used by this function to compute or route work.
        - `inbox`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        this_task = asyncio.current_task()
        try:
            while True:
                try:
                    await asyncio.wait_for(inbox.event.wait(), timeout=INBOX_IDLE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    async with inbox.lock:
                        if inbox.queue:
                            continue
                        inbox.event.clear()
                    break

                while True:
                    batch = await self._take_batch(inbox)
                    if not batch:
                        break

                    merged_text = "\n".join(item.text for item in batch)
                    try:
                        response = await self._handle_internal(batch[0].update, merged_text)
                    except Exception as exc:
                        logger.exception("Unhandled inbox worker failure user=%s: %s", user_id, exc)
                        response = "Something went wrong. Please try again."

                    self._resolve_future(batch[0].future, response)
                    for item in batch[1:]:
                        self._resolve_future(item.future, _EMPTY_REPLY_SENTINEL)

        finally:
            async with self._inbox_map_lock:
                worker = self._user_workers.get(user_id)
                if worker is this_task:
                    self._user_workers.pop(user_id, None)
                active_inbox = self._user_inboxes.get(user_id)
                if active_inbox is inbox:
                    async with inbox.lock:
                        if not inbox.queue:
                            self._user_inboxes.pop(user_id, None)

    async def _take_batch(self, inbox: _UserInbox) -> list[_InboundMessage]:
        """
        Take batch.
        
        Purpose:
        - Implement `_take_batch` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `inbox`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `list[_InboundMessage]` when available; otherwise side effects only.
        """

        async with inbox.lock:
            if not inbox.queue:
                inbox.event.clear()
                return []
            first = inbox.queue.popleft()
            batch = [first]
            deadline = first.received_at + self._coalesce_window_seconds
            while inbox.queue and inbox.queue[0].received_at <= deadline:
                if not self._can_coalesce(batch[-1], inbox.queue[0]):
                    break
                batch.append(inbox.queue.popleft())
            if not inbox.queue:
                inbox.event.clear()

        now = time.monotonic()
        if now < deadline:
            await asyncio.sleep(deadline - now)
            async with inbox.lock:
                while inbox.queue and inbox.queue[0].received_at <= deadline:
                    if not self._can_coalesce(batch[-1], inbox.queue[0]):
                        break
                    batch.append(inbox.queue.popleft())
                if not inbox.queue:
                    inbox.event.clear()
        return batch

    def _can_coalesce(self, previous: _InboundMessage, current: _InboundMessage) -> bool:
        """
        Can coalesce.
        
        Purpose:
        - Implement `_can_coalesce` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `previous`: input used by this function to compute or route work.
        - `current`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

        if previous.is_command or current.is_command:
            return False
        if detect_switch_intent(previous.text) or detect_switch_intent(current.text):
            return False
        if not previous.text.strip() or not current.text.strip():
            return False
        return True

    def _resolve_future(self, future: asyncio.Future[str], value: str) -> None:
        """
        Resolve future.
        
        Purpose:
        - Implement `_resolve_future` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `future`: input used by this function to compute or route work.
        - `value`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if future.done():
            return
        future.set_result(value)
    async def _handle_internal(self, update, text: str) -> str:
        """
        Handle internal.
        
        Purpose:
        - Implement `_handle_internal` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        session = await self.session_loader.load(update.effective_user.id)
        pending_question = get_pending_question(session)
        pending_action = get_pending_action(session)
        last_bot_turn = await self._load_last_assistant_text(update)
        scope_resolution = resolve_scope(session, text, last_bot_turn)

        action_reply = await self._maybe_handle_pending_action(update, session, text, pending_action)
        if action_reply is not None:
            return action_reply

        if pending_question and pending_question.type == "choose_project_scope":
            scope_reply = await self._maybe_handle_scope_question_answer(
                update,
                session,
                text,
                pending_question,
                scope_resolution,
            )
            if scope_reply is not None:
                return scope_reply

        if pending_question and pending_question.type == "need_project_name":
            name_reply = await self._maybe_handle_project_name_answer(update, session, text)
            if name_reply is not None:
                return name_reply

        if pending_question and pending_question.type == "need_idea_text":
            idea_reply = await self._maybe_handle_need_idea_text(update, session, text, pending_question)
            if idea_reply is not None:
                return idea_reply

        recent = await self._load_recent_for_classifier(update)
        intent = await self.intent_classifier.classify(text, session, recent)

        # Deterministic fallback: feature-like text with active project is propose_idea.
        if (
            intent.intent in {"casual_conversation", "unclear"}
            and session.project_id
            and self._looks_like_idea_text(text)
            and not detect_switch_intent(text)
        ):
            intent = ClassifiedIntent(
                intent="propose_idea",
                confidence=max(intent.confidence, 0.8),
                secondary_intents=intent.secondary_intents,
                entities=intent.entities,
                requires_tools=True,
                is_continuation=True,
            )

        scope_resolution = resolve_scope(session, text, last_bot_turn)
        decision = enforce_continuity(intent, scope_resolution, session)

        logger.info(
            "INTENT: intent=%s conf=%.2f mode_prev=%s project=%s scope=%s pending_q=%s text=%s",
            intent.intent,
            intent.confidence,
            session.last_mode,
            session.project_id,
            scope_resolution.scope,
            pending_question.type if pending_question else "",
            text[:80],
        )

        if intent.intent == "greeting":
            return await self._handle_greeting(update, session, text)

        if intent.intent in WRITE_INTENTS:
            return await self._handle_write_intent(
                update=update,
                text=text,
                session=session,
                intent=intent,
                decision=decision,
                scope_resolution=scope_resolution,
            )

        project_name = str(intent.entities.get("project_name") or "").strip()
        if project_name:
            try:
                from bot.nl_intent import _resolve_project

                project, _ = await _resolve_project(
                    project_name,
                    active_project_id=session.project_id,
                )
                if project:
                    session.project = project
                    await self.session_loader.update(
                        session,
                        project_id=project["id"],
                        conversation_phase=str(project.get("status") or session.conversation_phase),
                    )
            except Exception:
                logger.exception("Failed resolving project entity: %s", project_name)

        mode = select_mode(intent, session)
        filtered_tools = self.tool_gate.filter(mode, self.skill_registry.get_all_tools())

        ctx = await self.context_builder.build(
            mode,
            session,
            intent,
            update,
            filtered_tools,
            user_text=text,
        )
        requested_allowlist = ctx.allowed_providers
        ctx.allowed_providers = self._resolve_allowed_providers(
            mode=mode,
            tools=ctx.tools,
            requested_allowlist=requested_allowlist,
        )

        await self._persist_message(update, role="user", content=text)

        try:
            execution = await self._execute(text, ctx, session, mode)
            response = execution.text
        except Exception as exc:
            logger.exception("Orchestrator execution failed: %s", exc)
            execution = _ExecutionResult(text="Something went wrong. Please try again.")
            response = execution.text

        response = (response or "").strip() or "I could not generate a reply right now."
        response = state._main_persona_agent.compose_final_response(response)
        if len(response) > 3800:
            response = response[:3800] + "\n\n... (truncated)"

        await self._persist_message(update, role="assistant", content=response)

        metadata_update = self._build_metadata_update(intent, mode, response, session)
        pending_action_update = self._build_pending_action_update(intent, execution, response, session)
        if pending_action_update is not None:
            metadata_update["pending_action"] = pending_action_update

        try:
            await self.session_loader.update(
                session,
                last_intent=intent.intent,
                last_mode=mode.value,
                last_message_at=datetime.now(timezone.utc).isoformat(),
                conversation_phase=self._infer_phase(intent, mode, session),
                session_metadata=metadata_update,
            )
        except Exception as exc:
            logger.exception("Failed to update session for user %s: %s", session.user_id, exc)

        return response

    async def _handle_write_intent(
        self,
        *,
        update,
        text: str,
        session: Session,
        intent: ClassifiedIntent,
        decision: RoutingDecision,
        scope_resolution: ScopeResolution,
    ) -> str:
        """
        Handle write intent.
        
        Purpose:
        - Implement `_handle_write_intent` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `intent`: input used by this function to compute or route work.
        - `decision`: input used by this function to compute or route work.
        - `scope_resolution`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        if decision.ask_question and decision.question_type == "need_project_name":
            hinted_name = self._extract_project_name_hint(text)
            if hinted_name:
                return await self._maybe_handle_project_name_answer(update, session, hinted_name)
            question = "What would you like to name the new project?"
            pending = self._build_pending_question(
                q_type="need_project_name",
                choices=[],
                payload={"source": intent.intent, "original_text": text},
            )
            return await self._reply_with_pending_question(
                update,
                session,
                text,
                question,
                pending,
                intent_name=intent.intent,
                mode=Mode.PLANNING,
            )

        if decision.ask_question and decision.question_type in {"choose_project_scope", "resolve_project_reference"}:
            question = "Should I use your current project or start a new project for this idea?"
            pending = self._build_pending_question(
                q_type="choose_project_scope",
                choices=["existing", "new"],
                payload={"source": intent.intent, "original_text": text},
            )
            return await self._reply_with_pending_question(
                update,
                session,
                text,
                question,
                pending,
                intent_name=intent.intent,
                mode=Mode.PLANNING,
            )

        project_id = decision.target_project_id or scope_resolution.project_id or session.project_id
        if not project_id:
            question = "Which project should I add this idea to?"
            pending = self._build_pending_question(
                q_type="choose_project_scope",
                choices=["existing", "new"],
                payload={"source": intent.intent, "original_text": text},
            )
            return await self._reply_with_pending_question(
                update,
                session,
                text,
                question,
                pending,
                intent_name=intent.intent,
                mode=Mode.PLANNING,
            )

        extraction = await self._extract_idea_payload(text)
        idea_text = extraction.get("idea_text", "").strip()
        confidence = float(extraction.get("confidence") or 0.0)

        if not idea_text or confidence < IDEA_EXTRACT_CONFIDENCE_THRESHOLD:
            question = "What should I add as the idea?"
            pending = self._build_pending_question(
                q_type="need_idea_text",
                choices=[],
                payload={"project_id": project_id},
            )
            return await self._reply_with_pending_question(
                update,
                session,
                text,
                question,
                pending,
                intent_name=intent.intent,
                mode=Mode.PLANNING,
            )

        await self._persist_message(update, role="user", content=text)
        result = await self._execute_deterministic_tool(
            "project_add_idea",
            {"project_id": project_id, "idea": idea_text},
            session,
        )
        response = state._main_persona_agent.compose_final_response((result or "").strip())
        await self._persist_message(update, role="assistant", content=response)

        await self.session_loader.update(
            session,
            last_intent=intent.intent,
            last_mode=Mode.PLANNING.value,
            last_message_at=datetime.now(timezone.utc).isoformat(),
            conversation_phase=self._infer_phase(intent, Mode.PLANNING, session),
            session_metadata={
                "last_mode": Mode.PLANNING.value,
                "pending_question": None,
                "last_response_preview": response[:240],
            },
            )
        return response

    async def _handle_greeting(self, update, session: Session, user_text: str) -> str:
        """
        Handle greeting.
        
        Purpose:
        - Implement `_handle_greeting` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        await self._persist_message(update, role="user", content=user_text)
        project_name = (session.project or {}).get("display_name") or (session.project or {}).get("name")
        if project_name:
            response = f"Hi! Ready to continue {project_name}. What should we do next?"
        else:
            response = "Hi! How can I help today?"
        response = state._main_persona_agent.compose_final_response(response)
        await self._persist_message(update, role="assistant", content=response)
        await self.session_loader.update(
            session,
            last_intent="greeting",
            last_mode=Mode.CONVERSATION.value,
            last_message_at=datetime.now(timezone.utc).isoformat(),
            conversation_phase=session.conversation_phase or "discovery",
            session_metadata={
                "last_mode": Mode.CONVERSATION.value,
                "last_response_preview": response[:240],
            },
        )
        return response

    async def _maybe_handle_need_idea_text(
        self,
        update,
        session: Session,
        text: str,
        pending_question: PendingQuestion,
    ) -> str | None:
        """
        Maybe handle need idea text.
        
        Purpose:
        - Implement `_maybe_handle_need_idea_text` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        - `pending_question`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str | None` when available; otherwise side effects only.
        """

        idea_text = (text or "").strip()
        if not idea_text:
            return None

        project_id = str((pending_question.payload or {}).get("project_id") or session.project_id or "").strip()
        if not project_id:
            return None

        await self._persist_message(update, role="user", content=text)
        result = await self._execute_deterministic_tool(
            "project_add_idea",
            {"project_id": project_id, "idea": idea_text},
            session,
        )
        response = state._main_persona_agent.compose_final_response((result or "").strip())
        await self._persist_message(update, role="assistant", content=response)

        await self.session_loader.update(
            session,
            last_intent="propose_idea",
            last_mode=Mode.PLANNING.value,
            last_message_at=datetime.now(timezone.utc).isoformat(),
            conversation_phase=self._infer_phase(ClassifiedIntent("propose_idea", 1.0), Mode.PLANNING, session),
            session_metadata={
                "last_mode": Mode.PLANNING.value,
                "pending_question": None,
                "last_response_preview": response[:240],
            },
        )
        return response
    async def _maybe_handle_scope_question_answer(
        self,
        update,
        session: Session,
        text: str,
        pending_question: PendingQuestion,
        scope_resolution: ScopeResolution,
    ) -> str | None:
        """
        Maybe handle scope question answer.
        
        Purpose:
        - Implement `_maybe_handle_scope_question_answer` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        - `pending_question`: input used by this function to compute or route work.
        - `scope_resolution`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str | None` when available; otherwise side effects only.
        """

        if scope_resolution.scope_answer == "new":
            question = "What would you like to name the new project?"
            pending = self._build_pending_question(
                q_type="need_project_name",
                choices=[],
                payload=pending_question.payload or {},
            )
            return await self._reply_with_pending_question(
                update,
                session,
                text,
                question,
                pending,
                intent_name="propose_idea",
                mode=Mode.PLANNING,
            )

        if scope_resolution.scope_answer == "existing":
            question = "What should I add as the idea?"
            pending = self._build_pending_question(
                q_type="need_idea_text",
                choices=[],
                payload={"project_id": session.project_id},
            )
            return await self._reply_with_pending_question(
                update,
                session,
                text,
                question,
                pending,
                intent_name="propose_idea",
                mode=Mode.PLANNING,
            )

        return None

    async def _maybe_handle_project_name_answer(
        self,
        update,
        session: Session,
        text: str,
    ) -> str | None:
        """
        Maybe handle project name answer.
        
        Purpose:
        - Implement `_maybe_handle_project_name_answer` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str | None` when available; otherwise side effects only.
        """

        project_name = (text or "").strip()
        if not project_name:
            return None

        await self._persist_message(update, role="user", content=text)
        create_result = await self._execute_deterministic_tool(
            "project_create",
            {"name": project_name},
            session,
        )
        response = (create_result or "").strip()
        creation_failed = (
            not response
            or response.lower().startswith("error")
            or not session.project_id
        )
        if creation_failed:
            pending = self._build_pending_question(
                q_type="need_project_name",
                choices=[],
                payload={"source": "propose_idea"},
            )
            guidance = "Please provide a specific project name (for example: task-tracker)."
            full_response = state._main_persona_agent.compose_final_response(
                f"{response}\n\n{guidance}".strip() if response else guidance
            )
            await self._persist_message(update, role="assistant", content=full_response)
            await self.session_loader.update(
                session,
                last_intent="propose_idea",
                last_mode=Mode.PLANNING.value,
                last_message_at=datetime.now(timezone.utc).isoformat(),
                conversation_phase=self._infer_phase(ClassifiedIntent("propose_idea", 1.0), Mode.PLANNING, session),
                session_metadata={
                    "last_mode": Mode.PLANNING.value,
                    "pending_question": {
                        "type": pending.type,
                        "choices": pending.choices,
                        "turn_id": pending.turn_id,
                        "expires_at": pending.expires_at,
                        "payload": pending.payload,
                        "created_at": pending.created_at,
                    },
                    "last_response_preview": full_response[:240],
                },
            )
            return full_response

        follow_up = "What should I add as the idea?"
        pending = self._build_pending_question(
            q_type="need_idea_text",
            choices=[],
            payload={"project_id": session.project_id},
        )
        full_response = state._main_persona_agent.compose_final_response(f"{response}\n\n{follow_up}".strip())
        await self._persist_message(update, role="assistant", content=full_response)

        await self.session_loader.update(
            session,
            last_intent="propose_idea",
            last_mode=Mode.PLANNING.value,
            last_message_at=datetime.now(timezone.utc).isoformat(),
            conversation_phase=self._infer_phase(ClassifiedIntent("propose_idea", 1.0), Mode.PLANNING, session),
            session_metadata={
                "last_mode": Mode.PLANNING.value,
                "pending_question": {
                    "type": pending.type,
                    "choices": pending.choices,
                    "turn_id": pending.turn_id,
                    "expires_at": pending.expires_at,
                    "payload": pending.payload,
                    "created_at": pending.created_at,
                },
                "last_response_preview": full_response[:240],
            },
        )
        return full_response

    async def _maybe_handle_pending_action(
        self,
        update,
        session: Session,
        text: str,
        pending_action: PendingAction | None,
    ) -> str | None:
        """
        Maybe handle pending action.
        
        Purpose:
        - Implement `_maybe_handle_pending_action` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        - `pending_action`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str | None` when available; otherwise side effects only.
        """

        if not pending_action or pending_action.type != "approve_plan":
            return None

        if _APPROVE_PLAN_RE.search(text or ""):
            await self._persist_message(update, role="user", content=text)
            result = await self._execute_deterministic_tool(
                "project_approve_start",
                {"project_id": pending_action.project_id},
                session,
            )
            response = state._main_persona_agent.compose_final_response((result or "").strip())
            await self._persist_message(update, role="assistant", content=response)
            await self.session_loader.update(
                session,
                last_intent="approve_plan",
                last_mode=Mode.EXECUTION.value,
                last_message_at=datetime.now(timezone.utc).isoformat(),
                conversation_phase=self._infer_phase(ClassifiedIntent("approve_plan", 1.0), Mode.EXECUTION, session),
                session_metadata={
                    "pending_action": None,
                    "pending_question": None,
                    "last_mode": Mode.EXECUTION.value,
                    "last_response_preview": response[:240],
                },
            )
            return response

        if _REJECT_PLAN_RE.search(text or ""):
            response = "Okay, I will not start execution yet."
            await self._persist_message(update, role="user", content=text)
            await self._persist_message(update, role="assistant", content=response)
            await self.session_loader.update(
                session,
                last_intent="request_stop",
                last_mode=Mode.CONVERSATION.value,
                last_message_at=datetime.now(timezone.utc).isoformat(),
                conversation_phase=session.conversation_phase,
                session_metadata={
                    "pending_action": None,
                    "last_mode": Mode.CONVERSATION.value,
                    "last_response_preview": response[:240],
                },
            )
            return response

        return None

    async def _reply_with_pending_question(
        self,
        update,
        session: Session,
        user_text: str,
        assistant_text: str,
        pending_question: PendingQuestion,
        *,
        intent_name: str,
        mode: Mode,
    ) -> str:
        """
        Reply with pending question.
        
        Purpose:
        - Implement `_reply_with_pending_question` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `user_text`: input used by this function to compute or route work.
        - `assistant_text`: input used by this function to compute or route work.
        - `pending_question`: input used by this function to compute or route work.
        - `intent_name`: input used by this function to compute or route work.
        - `mode`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        await self._persist_message(update, role="user", content=user_text)
        response = state._main_persona_agent.compose_final_response((assistant_text or "").strip())
        await self._persist_message(update, role="assistant", content=response)

        await self.session_loader.update(
            session,
            last_intent=intent_name,
            last_mode=mode.value,
            last_message_at=datetime.now(timezone.utc).isoformat(),
            conversation_phase=self._infer_phase(ClassifiedIntent(intent_name, 1.0), mode, session),
            session_metadata={
                "last_mode": mode.value,
                "pending_question": {
                    "type": pending_question.type,
                    "choices": pending_question.choices,
                    "turn_id": pending_question.turn_id,
                    "expires_at": pending_question.expires_at,
                    "payload": pending_question.payload,
                    "created_at": pending_question.created_at,
                },
                "last_response_preview": response[:240],
            },
        )
        return response
    async def _execute_deterministic_tool(self, tool_name: str, tool_input: dict[str, Any], session: Session) -> str:
        """
        Execute deterministic tool.
        
        Purpose:
        - Implement `_execute_deterministic_tool` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `tool_name`: input used by this function to compute or route work.
        - `tool_input`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        skill = self.skill_registry.get_skill_for_tool(tool_name)
        if not skill:
            return f"Unknown tool: {tool_name}"
        normalized_input = tool_input if isinstance(tool_input, dict) else {}
        skill_context = self._build_skill_context(session)
        try:
            return await skill.execute(tool_name, normalized_input, skill_context)
        except Exception as exc:
            logger.exception("Deterministic tool execution failed for %s: %s", tool_name, exc)
            return f"ERROR: tool {tool_name} failed: {exc}"

    async def _extract_idea_payload(self, user_text: str) -> dict[str, Any]:
        """
        Extract idea payload.
        
        Purpose:
        - Implement `_extract_idea_payload` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `user_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        if self._looks_like_idea_text(user_text) and not detect_switch_intent(user_text):
            fallback = {
                "idea_text": user_text.strip(),
                "confidence": 0.7,
            }
        else:
            fallback = {"idea_text": "", "confidence": 0.0}

        prompt = render_prompt(
            _IDEA_EXTRACT_PROMPT,
            user_message=user_text[:800],
        )
        try:
            response = await self.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=180,
                task_type="general",
                allowed_providers=self.chat_provider_allowlist,
            )
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(text)
            idea_text = str(data.get("idea_text") or "").strip()
            confidence = float(data.get("confidence") or 0.0)
            confidence = min(max(confidence, 0.0), 1.0)
            if not idea_text:
                return fallback
            return {
                "idea_text": idea_text,
                "confidence": confidence,
            }
        except Exception as exc:
            logger.warning("Idea extraction failed, using fallback: %s", exc)
            return fallback

    def _looks_like_idea_text(self, text: str) -> bool:
        """
        Looks like idea text.
        
        Purpose:
        - Implement `_looks_like_idea_text` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

        value = (text or "").strip().lower()
        if not value:
            return False
        if len(value.split()) < 4:
            return False
        if value in {"new", "existing", "yes", "no", "ok", "sure"}:
            return False
        if value.startswith("/"):
            return False
        return True

    def _extract_project_name_hint(self, text: str) -> str:
        """
        Extract project name hint.
        
        Purpose:
        - Implement `_extract_project_name_hint` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        value = (text or "").strip()
        if not value:
            return ""

        patterns = (
            re.compile(r"\b(?:called|named)\s+([a-zA-Z0-9][a-zA-Z0-9_\- ]{1,63})", re.IGNORECASE),
            re.compile(
                r"\b(?:start|create|make|begin)\s+(?:a\s+)?(?:new\s+)?(?:project|app|application)\s+([a-zA-Z0-9][a-zA-Z0-9_\-]{1,63})",
                re.IGNORECASE,
            ),
        )
        for pattern in patterns:
            match = pattern.search(value)
            if not match:
                continue
            name = match.group(1).strip().strip(".!,?;:")
            if not name:
                continue
            lowered = name.lower()
            if lowered in _INVALID_PROJECT_NAME_HINTS:
                continue
            return name
        return ""

    def _build_pending_question(
        self,
        *,
        q_type: str,
        choices: list[str],
        payload: dict[str, Any] | None,
    ) -> PendingQuestion:
        """
        Build pending question.
        
        Purpose:
        - Implement `_build_pending_question` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `q_type`: input used by this function to compute or route work.
        - `choices`: input used by this function to compute or route work.
        - `payload`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `PendingQuestion` when available; otherwise side effects only.
        """

        now = datetime.now(timezone.utc)
        return PendingQuestion(
            type=q_type,
            choices=choices,
            turn_id=uuid.uuid4().hex[:12],
            expires_at=(now + PENDING_QUESTION_TTL).isoformat(),
            payload=payload,
            created_at=now.isoformat(),
        )

    def _build_pending_action_update(
        self,
        intent: ClassifiedIntent,
        execution: _ExecutionResult,
        response_text: str,
        session: Session,
    ) -> dict[str, Any] | None:
        """
        Build pending action update.
        
        Purpose:
        - Implement `_build_pending_action_update` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `intent`: input used by this function to compute or route work.
        - `execution`: input used by this function to compute or route work.
        - `response_text`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
        """

        if intent.intent in {"approve_plan", "approve_execution", "request_stop"}:
            return None

        generated_plan = any(
            out.get("name") == "project_generate_plan"
            and not (out.get("result") or "").lower().startswith("error")
            for out in execution.tool_outcomes
        )
        if not generated_plan:
            return None

        if not _APPROVAL_PROMPT_RE.search(response_text or ""):
            return None

        project_id = session.project_id
        if not project_id:
            return None

        now = datetime.now(timezone.utc)
        pending = PendingAction(
            type="approve_plan",
            project_id=project_id,
            created_at=now.isoformat(),
            expires_at=(now + PENDING_ACTION_TTL).isoformat(),
        )
        return {
            "type": pending.type,
            "project_id": pending.project_id,
            "created_at": pending.created_at,
            "expires_at": pending.expires_at,
        }

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

        try:
            session = await self.session_loader.load(int(user_id))
            await self.session_loader.clear_pending_action(session)
        except Exception:
            logger.exception("Failed clearing pending action for user=%s", user_id)
    def _resolve_allowed_providers(
        self,
        *,
        mode: Mode,
        tools: list[dict],
        requested_allowlist: list[str] | None,
    ) -> list[str] | None:
        """
        Resolve allowed providers.
        
        Purpose:
        - Implement `_resolve_allowed_providers` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `mode`: input used by this function to compute or route work.
        - `tools`: input used by this function to compute or route work.
        - `requested_allowlist`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `list[str] | None` when available; otherwise side effects only.
        """

        require_tools = bool(tools)
        requested = self._dedupe_provider_names(requested_allowlist)
        requested_candidates = self._available_provider_names(
            allowed_providers=requested,
            require_tools=require_tools,
        )
        if requested_candidates:
            logger.info(
                "Provider allowlist: mode=%s requested=%s candidates=%s",
                mode.value,
                requested,
                requested_candidates,
            )
            return requested

        if mode in {Mode.EXECUTION, Mode.RECOVERY}:
            fallback_allowlist = self._dedupe_provider_names(
                ["anthropic", "claude"] + list(self.chat_provider_allowlist or []) + ["ollama"]
            )
            fallback_candidates = self._available_provider_names(
                allowed_providers=fallback_allowlist,
                require_tools=require_tools,
            )
            if fallback_candidates:
                logger.warning(
                    "Provider allowlist fallback: mode=%s requested=%s fallback=%s candidates=%s",
                    mode.value,
                    requested,
                    fallback_allowlist,
                    fallback_candidates,
                )
                return fallback_allowlist
            logger.warning(
                "Provider allowlist fallback failed: mode=%s requested=%s fallback=%s",
                mode.value,
                requested,
                fallback_allowlist,
            )
            return None

        if requested is not None:
            logger.warning(
                "Provider allowlist unavailable: mode=%s requested=%s",
                mode.value,
                requested,
            )
        return requested

    def _available_provider_names(
        self,
        *,
        allowed_providers: list[str] | None,
        require_tools: bool,
    ) -> list[str]:
        """
        Available provider names.
        
        Purpose:
        - Implement `_available_provider_names` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `allowed_providers`: input used by this function to compute or route work.
        - `require_tools`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `list[str]` when available; otherwise side effects only.
        """

        try:
            if hasattr(self.provider_router, "available_provider_names"):
                return self.provider_router.available_provider_names(
                    allowed_providers=allowed_providers,
                    require_tools=require_tools,
                    task_type="general",
                )
        except Exception:
            logger.exception("Failed provider availability introspection")
        return []

    def _dedupe_provider_names(self, names: list[str] | None) -> list[str] | None:
        """
        Dedupe provider names.
        
        Purpose:
        - Implement `_dedupe_provider_names` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `names`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `list[str] | None` when available; otherwise side effects only.
        """

        if names is None:
            return None
        deduped: list[str] = []
        for name in names:
            normalized = str(name).strip().lower()
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped

    async def _execute(self, text: str, ctx: ContextPackage, session: Session, mode: Mode) -> _ExecutionResult:
        """
        Execute.
        
        Purpose:
        - Implement `_execute` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `text`: input used by this function to compute or route work.
        - `ctx`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `mode`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `_ExecutionResult` when available; otherwise side effects only.
        """

        skill_context = self._build_skill_context(session)
        messages = ctx.messages + [{"role": "user", "content": text}]
        rounds = 0
        tool_outcomes: list[dict[str, str]] = []

        while rounds < ctx.max_rounds:
            try:
                response = await self.provider_router.chat(
                    messages,
                    tools=ctx.tools,
                    system=ctx.system_prompt,
                    max_tokens=ctx.max_tokens,
                    task_type="general",
                    allowed_providers=ctx.allowed_providers,
                )
            except Exception as exc:
                logger.exception(
                    "Provider failure in %s mode (round %d/%d): %s",
                    mode.value,
                    rounds + 1,
                    ctx.max_rounds,
                    exc,
                )
                return _ExecutionResult(text=self._map_provider_failure(exc), tool_outcomes=tool_outcomes)

            if not response.tool_calls:
                return _ExecutionResult(text=(response.text or "").strip(), tool_outcomes=tool_outcomes)

            tool_results = []
            for tc in response.tool_calls:
                tool_input = tc.input if isinstance(tc.input, dict) else {}
                if not isinstance(tc.input, dict):
                    logger.warning(
                        "Malformed tool input in %s mode for tool %s: %s",
                        mode.value,
                        tc.name,
                        type(tc.input).__name__,
                    )
                skill = self.skill_registry.get_skill_for_tool(tc.name)
                if skill:
                    try:
                        result = await skill.execute(tc.name, tool_input, skill_context)
                    except Exception as exc:
                        logger.exception(
                            "Tool execution failed in %s mode for %s: %s",
                            mode.value,
                            tc.name,
                            exc,
                        )
                        result = f"ERROR: tool {tc.name} failed: {exc}"
                else:
                    logger.warning("Blocked tool call in %s mode: %s", mode.value, tc.name)
                    result = f"Unknown tool: {tc.name}"

                tool_outcomes.append({"name": tc.name, "result": str(result)})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    }
                )

            assistant_content = self._build_assistant_content(response) or ""
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            rounds += 1

        summary = await self._force_summary(messages, ctx.system_prompt)
        return _ExecutionResult(text=summary, tool_outcomes=tool_outcomes)

    def _map_provider_failure(self, error: Exception) -> str:
        """
        Map provider failure.
        
        Purpose:
        - Implement `_map_provider_failure` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `error`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        message = str(error).lower()
        if (
            "no ai providers available" in message
            or "all ai providers failed" in message
            or "agent not connected" in message
        ):
            return (
                "No AI provider is currently available. Connect the CHATHAN agent "
                "for Ollama or configure a cloud API key (Gemini/Anthropic), then try again."
            )
        return "The AI provider encountered an error. Please try again."
    def _make_set_active_project_callback(self, session: Session):
        """
        Make set active project callback.
        
        Purpose:
        - Implement `_make_set_active_project_callback` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        async def _set_active_project(project_id: str, phase: str):
            """
            Set active project.
            
            Purpose:
            - Implement `_set_active_project` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `project_id`: input used by this function to compute or route work.
            - `phase`: input used by this function to compute or route work.
            
            Returns:
            - Function-specific value or side effects consumed by upstream callers.
            """

            session.project_id = project_id
            session.conversation_phase = phase
            from db import store

            project = await store.get_project(self.db, project_id)
            session.project = project
            await self.session_loader.update(
                session,
                project_id=project_id,
                conversation_phase=phase,
            )

        return _set_active_project

    def _build_skill_context(self, session: Session) -> SkillContext:
        """
        Build skill context.
        
        Purpose:
        - Implement `_build_skill_context` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `SkillContext` when available; otherwise side effects only.
        """

        from bot.commands import request_worker_approval

        project_id = session.project_id or "telegram_chat"
        project_path = (
            str((session.project or {}).get("local_path") or "").strip()
            or cfg.PROJECT_BASE_DIR
            or cfg.DEFAULT_WORKING_DIR
        )
        return SkillContext(
            project_id=project_id,
            project_path=project_path,
            gateway_api_url=self.gateway_api_url,
            searcher=state._searcher,
            request_approval=request_worker_approval,
            set_active_project=self._make_set_active_project_callback(session),
        )

    def _build_assistant_content(self, response) -> object:
        """
        Build assistant content.
        
        Purpose:
        - Implement `_build_assistant_content` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `response`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `object` when available; otherwise side effects only.
        """

        parts: list[dict[str, Any]] = []
        if response.text:
            parts.append({"type": "text", "text": response.text})
        for tc in response.tool_calls or []:
            parts.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
            )
        return parts if parts else response.text

    async def _force_summary(self, messages: list[dict], system_prompt: str) -> str:
        """
        Force summary.
        
        Purpose:
        - Implement `_force_summary` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `messages`: input used by this function to compute or route work.
        - `system_prompt`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        try:
            summary = await self.provider_router.chat(
                messages
                + [
                    {
                        "role": "user",
                        "content": _FORCE_SUMMARY_USER_PROMPT,
                    }
                ],
                tools=[],
                system=system_prompt,
                max_tokens=700,
                task_type="general",
                allowed_providers=self.chat_provider_allowlist,
            )
            return (summary.text or "").strip()
        except Exception:
            return ""

    def _infer_phase(self, intent: ClassifiedIntent, mode: Mode, session: Session) -> str:
        """
        Infer phase.
        
        Purpose:
        - Implement `_infer_phase` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `intent`: input used by this function to compute or route work.
        - `mode`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        if not session.project_id:
            return "discovery"
        if mode == Mode.PLANNING:
            return "planning"
        if mode in {Mode.EXECUTION, Mode.RECOVERY, Mode.REVIEW}:
            return "coding"
        if intent.intent in {"request_stop"}:
            return "paused"
        return session.conversation_phase or "discovery"

    def _build_metadata_update(
        self,
        intent: ClassifiedIntent,
        mode: Mode,
        response: str,
        session: Session,
    ) -> dict[str, Any]:
        """
        Build metadata update.
        
        Purpose:
        - Implement `_build_metadata_update` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `intent`: input used by this function to compute or route work.
        - `mode`: input used by this function to compute or route work.
        - `response`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        metadata: dict[str, Any] = {"last_mode": mode.value}
        if intent.intent in {"approve_plan", "approve_execution"}:
            metadata["waiting_for"] = ""
        if response:
            metadata["last_response_preview"] = response[:240]
        return metadata

    async def _persist_message(self, update, *, role: str, content: str) -> None:
        """
        Persist message.
        
        Purpose:
        - Implement `_persist_message` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `role`: input used by this function to compute or route work.
        - `content`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if role not in {"user", "assistant"}:
            return

        conversation_id = await self._get_or_create_active_conversation_id(update)
        if conversation_id and self.db is not None:
            try:
                from db import store

                await store.add_session_message(
                    self.db,
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    metadata={"channel": "orchestrator"},
                )
            except Exception:
                logger.exception("Failed persisting conversation message conversation_id=%s", conversation_id)

        await _append_user_conversation(
            update,
            role=role,
            content=content,
            metadata={"channel": "orchestrator"},
        )
        state._chat_history.append({"role": role, "content": content})
        from bot.helpers import _trim_chat_history

        _trim_chat_history()

    async def _load_recent_for_classifier(self, update) -> list[dict]:
        """
        Load recent for classifier.
        
        Purpose:
        - Implement `_load_recent_for_classifier` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `list[dict]` when available; otherwise side effects only.
        """

        history = await _load_recent_conversation_messages(update, gap_tier=GapTier.ACTIVE)
        return history[-2:]

    async def _load_last_assistant_text(self, update) -> str:
        """
        Load last assistant text.
        
        Purpose:
        - Implement `_load_last_assistant_text` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        history = await _load_recent_conversation_messages(update, gap_tier=GapTier.ACTIVE)
        for msg in reversed(history):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                return " ".join(part for part in text_parts if part).strip()
        return ""
