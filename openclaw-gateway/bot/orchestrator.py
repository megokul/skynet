from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from weakref import WeakValueDictionary

import bot_config as cfg
from bot import state
from bot.context import ContextBuilder, ContextPackage
from bot.intent import IntentClassifier
from bot.memory import GapTier, _append_user_conversation, _load_recent_conversation_messages
from bot.mode import Mode, ToolPolicyGate, select_mode
from bot.session import Session, SessionLoader
from skills.base import SkillContext

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        db,
        project_manager,
        provider_router,
        skill_registry,
        gateway_api_url,
        chat_provider_allowlist,
    ):
        self.db = db
        self.provider_router = provider_router
        self.skill_registry = skill_registry
        self.gateway_api_url = gateway_api_url
        self.chat_provider_allowlist = chat_provider_allowlist
        self.session_loader = SessionLoader(db, project_manager)
        self.intent_classifier = IntentClassifier(provider_router)
        self.context_builder = ContextBuilder(db, skill_registry, chat_provider_allowlist)
        self.tool_gate = ToolPolicyGate()
        # WeakValueDictionary: locks for inactive users are garbage-collected.
        # A plain dict would leak one Lock per user_id forever.
        self._execution_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def handle(self, update, text: str) -> str:
        user_id = str(update.effective_user.id)

        # CRITICAL: Explicit get-or-create, NOT setdefault().
        # WeakValueDictionary doesn't hold strong references. setdefault() can
        # create a Lock that gets GC'd before the local variable captures it,
        # allowing concurrent messages to bypass the lock entirely.
        lock = self._execution_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._execution_locks[user_id] = lock

        if lock.locked():
            return "Still working on your previous request."

        async with lock:
            return await self._handle_internal(update, text)

    async def _handle_internal(self, update, text: str) -> str:
        # 1. Load session
        session = await self.session_loader.load(update.effective_user.id)

        # 2. Classify intent
        recent = await self._load_recent_for_classifier(update)
        intent = await self.intent_classifier.classify(text, session, recent)

        logger.info(
            "INTENT: intent=%s conf=%.2f mode_prev=%s project=%s text=%s",
            intent.intent,
            intent.confidence,
            session.last_mode,
            session.project_id,
            text[:80],
        )

        # Optional entity-driven project resolution.
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

        # 3. Select mode
        mode = select_mode(intent, session)

        # 4. Filter tools -- single source of truth
        filtered_tools = self.tool_gate.filter(
            mode,
            self.skill_registry.get_all_tools(),
        )

        # 5. Build context
        ctx = await self.context_builder.build(
            mode,
            session,
            intent,
            update,
            filtered_tools,
            user_text=text,
        )

        # 6. Persist user message BEFORE execution (crash safety)
        await self._persist_message(update, role="user", content=text)

        # 7. Execute
        try:
            response = await self._execute(text, ctx, session, mode)
        except Exception as e:
            logger.exception("Orchestrator execution failed: %s", e)
            response = "Something went wrong. Please try again."

        response = (response or "").strip() or "I could not generate a reply right now."
        response = state._main_persona_agent.compose_final_response(response)
        if len(response) > 3800:
            response = response[:3800] + "\n\n... (truncated)"

        # 8. Persist assistant response AFTER execution
        await self._persist_message(update, role="assistant", content=response)

        # 9. Update session
        await self.session_loader.update(
            session,
            last_intent=intent.intent,
            last_mode=mode.value,
            last_message_at=datetime.utcnow().isoformat(),
            conversation_phase=self._infer_phase(intent, mode, session),
            session_metadata=self._build_metadata_update(intent, mode, response, session),
        )

        return response

    async def _execute(self, text: str, ctx: ContextPackage, session: Session, mode: Mode) -> str:
        skill_context = self._build_skill_context(session)
        messages = ctx.messages + [{"role": "user", "content": text}]
        rounds = 0

        while rounds < ctx.max_rounds:
            # Wrap provider call -- failures must not kill the loop
            try:
                response = await self.provider_router.chat(
                    messages,
                    tools=ctx.tools,
                    system=ctx.system_prompt,
                    max_tokens=ctx.max_tokens,
                    task_type="general",
                    allowed_providers=ctx.allowed_providers,
                )
            except Exception as e:
                logger.exception(
                    "Provider failure in %s mode (round %d/%d): %s",
                    mode.value,
                    rounds + 1,
                    ctx.max_rounds,
                    e,
                )
                return "The AI provider encountered an error. Please try again."

            if not response.tool_calls:
                return (response.text or "").strip()

            # Execute tools
            tool_results = []
            for tc in response.tool_calls:
                skill = self.skill_registry.get_skill_for_tool(tc.name)
                if skill:
                    result = await skill.execute(tc.name, tc.input, skill_context)
                else:
                    logger.warning("Blocked tool call in %s mode: %s", mode.value, tc.name)
                    result = f"Unknown tool: {tc.name}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "name": tc.name,
                    "content": result,
                })

            # Guard against empty assistant content
            assistant_content = self._build_assistant_content(response) or ""
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            rounds += 1

        return await self._force_summary(messages, ctx.system_prompt)

    def _make_set_active_project_callback(self, session: Session):
        async def _set_active_project(project_id: str, phase: str):
            session.project_id = project_id
            session.conversation_phase = phase
            # Load full project row immediately so same-turn reads see it
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
        parts: list[dict] = []
        if response.text:
            parts.append({"type": "text", "text": response.text})
        for tc in response.tool_calls or []:
            parts.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        return parts if parts else response.text

    async def _force_summary(self, messages: list[dict], system_prompt: str) -> str:
        try:
            summary = await self.provider_router.chat(
                messages + [{
                    "role": "user",
                    "content": "Summarize the result and next step in plain language.",
                }],
                tools=[],
                system=system_prompt,
                max_tokens=700,
                task_type="general",
                allowed_providers=self.chat_provider_allowlist,
            )
            return (summary.text or "").strip()
        except Exception:
            return ""

    def _infer_phase(self, intent, mode: Mode, session: Session) -> str:
        if not session.project_id:
            return "discovery"
        if mode == Mode.PLANNING:
            return "planning"
        if mode in {Mode.EXECUTION, Mode.RECOVERY, Mode.REVIEW}:
            return "coding"
        if intent.intent in {"request_stop"}:
            return "paused"
        return session.conversation_phase or "discovery"

    def _build_metadata_update(self, intent, mode: Mode, response: str, session: Session) -> dict:
        metadata: dict[str, str] = {"last_mode": mode.value}
        if intent.intent in {"request_plan", "propose_idea", "change_direction"}:
            metadata["waiting_for"] = "plan_approval"
        elif intent.intent in {"approve_plan", "approve_execution"}:
            metadata["waiting_for"] = ""
        elif mode == Mode.REVIEW:
            metadata["waiting_for"] = "review_feedback"
        elif mode == Mode.CONVERSATION and not session.project_id:
            metadata["waiting_for"] = "project_clarification"
        if response:
            metadata["last_response_preview"] = response[:240]
        return metadata

    async def _persist_message(self, update, *, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            return
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
        history = await _load_recent_conversation_messages(update, gap_tier=GapTier.ACTIVE)
        return history[-2:]
