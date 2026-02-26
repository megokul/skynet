from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from bot import state
from bot.helpers import _build_gap_system_context, _project_display
from bot.intent import ClassifiedIntent
from bot.memory import GapTier, _compute_gap_tier, _load_recent_conversation_messages, _profile_prompt_context
from bot.message_utils import strip_tool_messages
from bot.mode import MODE_PROVIDER_ALLOWLIST, Mode
from bot.session import Session
from core.prompt_library import load_prompt, render_prompt

logger = logging.getLogger(__name__)


HISTORY_DEPTH: dict[Mode, dict] = {
    Mode.CONVERSATION: {"with_summary": True, "raw_messages": 6, "include_tool_results": False},
    Mode.PLANNING: {"with_summary": True, "raw_messages": 10, "include_tool_results": False},
    Mode.EXECUTION: {"with_summary": False, "raw_messages": 8, "include_tool_results": True},
    Mode.REVIEW: {"with_summary": False, "raw_messages": 6, "include_tool_results": True},
    Mode.RECOVERY: {"with_summary": True, "raw_messages": 12, "include_tool_results": True},
}

MODE_MAX_TOKENS: dict[Mode, int] = {
    Mode.CONVERSATION: 600,
    Mode.PLANNING: 1500,
    Mode.EXECUTION: 2000,
    Mode.REVIEW: 1000,
    Mode.RECOVERY: 1500,
}

MODE_MAX_ROUNDS: dict[Mode, int] = {
    Mode.CONVERSATION: 3,
    Mode.PLANNING: 5,
    Mode.EXECUTION: 12,
    Mode.REVIEW: 4,
    Mode.RECOVERY: 8,
}

MODE_INSTRUCTIONS: dict[Mode, str] = {
    Mode.CONVERSATION: load_prompt("bot/context/mode_conversation.md"),
    Mode.PLANNING: load_prompt("bot/context/mode_planning.md"),
    Mode.EXECUTION: load_prompt("bot/context/mode_execution.md"),
    Mode.REVIEW: load_prompt("bot/context/mode_review.md"),
    Mode.RECOVERY: load_prompt("bot/context/mode_recovery.md"),
}


def compute_session_gap(time_gap: timedelta | None) -> GapTier:
    seconds = None if time_gap is None else max(0.0, float(time_gap.total_seconds()))
    return _compute_gap_tier(seconds)


@dataclass
class ContextPackage:
    system_prompt: str
    messages: list[dict]
    tools: list[dict]
    max_tokens: int
    max_rounds: int
    allowed_providers: list[str] | None


class ContextBuilder:
    def __init__(self, db, skill_registry, chat_provider_allowlist: list[str] | None):
        self.db = db
        self.skill_registry = skill_registry
        self.chat_provider_allowlist = chat_provider_allowlist

    async def build(
        self,
        mode: Mode,
        session: Session,
        intent: ClassifiedIntent,
        update,
        filtered_tools: list[dict],
        *,
        user_text: str = "",
    ) -> ContextPackage:
        depth_cfg = HISTORY_DEPTH[mode]
        gap_tier = compute_session_gap(session.time_gap)

        project_name = _project_display(session.project) if session.project else ""
        gap_context = _build_gap_system_context(gap_tier, project_name)

        profile_context = await _profile_prompt_context(update)
        project_context = await self._build_project_context(mode, session)

        skill_guidance = ""
        try:
            registry = state._skill_registry or self.skill_registry
            if registry:
                prompt_context = registry.get_prompt_skill_context(
                    user_text or intent.intent,
                    role="chat",
                )
                if prompt_context:
                    skill_guidance = render_prompt(
                        "bot/context/external_skill_guidance_block.md",
                        skill_guidance=prompt_context,
                    )
        except Exception:
            logger.exception("Failed to inject external skill guidance")

        system_parts = [
            state._PERSONALITY_PROMPT,
            render_prompt(
                "bot/context/classified_intent_header.md",
                intent=intent.intent,
                confidence=f"{intent.confidence:.2f}",
            ),
            MODE_INSTRUCTIONS[mode],
            render_prompt(
                "bot/context/user_profile_block.md",
                profile_context=profile_context.strip() or "None",
            ),
            project_context,
            gap_context,
            skill_guidance.strip(),
        ]
        base_system_prompt = "\n\n".join(part for part in system_parts if part)
        system_prompt = state._main_persona_agent.compose_system_prompt(base_system_prompt)

        history = await _load_recent_conversation_messages(update, gap_tier=gap_tier)
        if not depth_cfg["with_summary"]:
            history = [
                m for m in history
                if not (
                    m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].startswith("[Conversation summary")
                )
            ]
        if not depth_cfg["include_tool_results"]:
            history = strip_tool_messages(history)
        if depth_cfg["raw_messages"] > 0 and len(history) > depth_cfg["raw_messages"]:
            history = history[-depth_cfg["raw_messages"]:]

        mode_allowlist = MODE_PROVIDER_ALLOWLIST.get(mode)
        allowed_providers = mode_allowlist if mode_allowlist is not None else self.chat_provider_allowlist

        return ContextPackage(
            system_prompt=system_prompt,
            messages=history,
            tools=filtered_tools,
            max_tokens=MODE_MAX_TOKENS[mode],
            max_rounds=MODE_MAX_ROUNDS[mode],
            allowed_providers=allowed_providers,
        )

    async def _build_project_context(self, mode: Mode, session: Session) -> str:
        if not session.project:
            return load_prompt("bot/context/project_none.md")

        project = session.project
        name = _project_display(project)
        status = str(project.get("status") or "unknown")
        if mode == Mode.CONVERSATION:
            tech_stack = project.get("tech_stack")
            if isinstance(tech_stack, str):
                try:
                    parsed = json.loads(tech_stack)
                    tech_stack = parsed
                except Exception:
                    pass
            return render_prompt(
                "bot/context/project_conversation.md",
                name=name,
                status=status,
                tech_stack=tech_stack if tech_stack else "unknown",
            )

        if mode == Mode.PLANNING:
            ideas = project.get("ideas")
            if not isinstance(ideas, list):
                ideas = []
                try:
                    from db import store

                    if self.db is not None and project.get("id"):
                        ideas = await store.get_ideas(self.db, project["id"])
                except Exception:
                    ideas = []
            lines: list[str] = [
                render_prompt(
                    "bot/context/project_planning_intro.md",
                    name=name,
                    status=status,
                ).strip()
            ]
            if ideas:
                for idea in ideas[:20]:
                    if isinstance(idea, dict):
                        text = idea.get("message_text") or idea.get("text") or ""
                    else:
                        text = str(idea)
                    lines.append(
                        render_prompt(
                            "bot/context/project_planning_idea_item.md",
                            idea_text=(text or "").strip()[:180],
                        ).strip()
                    )
            else:
                lines.append(load_prompt("bot/context/project_planning_no_ideas.md"))
            return "\n".join(lines)

        working_dir = str(project.get("local_path") or "unknown")
        return render_prompt(
            "bot/context/project_execution.md",
            name=name,
            status=status,
            working_dir=working_dir,
        )
