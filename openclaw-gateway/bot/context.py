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
    Mode.CONVERSATION: """\
## Mode: Conversation
Keep this lightweight and conversational.
- Answer clearly in plain language.
- Ask one focused follow-up question when key details are missing.
- Avoid jumping into execution unless the user asks for concrete implementation work.
""",
    Mode.PLANNING: """\
## Mode: Planning
Capture requirements and shape a concrete plan.
- Gather product goals, users, constraints, and preferred stack.
- Convert concrete requirements into project ideas.
- Offer documentation or plan generation only when the user signals readiness.
- Keep scope explicit and avoid hidden assumptions.
""",
    Mode.EXECUTION: """\
## Mode: Execution
The user expects implementation progress.
- Use available tools to inspect, change, and verify code.
- Report what changed and what remains.
- If blocked, surface the exact blocker and the next concrete action.
- Do not claim completion without evidence from tool output.
""",
    Mode.REVIEW: """\
## Mode: Review
Focus on validation and quality checks.
- Inspect diffs, tests, and project status before conclusions.
- Prioritize correctness, regressions, and missing coverage.
- Keep findings concrete and actionable.
""",
    Mode.RECOVERY: """\
## Mode: Recovery
Resume work after interruption or drift.
- Reconstruct current state from recent context and project status.
- Reconfirm user intent if continuation direction is ambiguous.
- Propose the smallest safe next step to regain momentum.
""",
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
                    skill_guidance = (
                        "\n\n[External Skill Guidance]\n"
                        "Use the following skill guidance if it helps solve the request:\n\n"
                        f"{prompt_context}"
                    )
        except Exception:
            logger.exception("Failed to inject external skill guidance")

        system_parts = [
            state._PERSONALITY_PROMPT,
            f"## Classified Intent\nIntent: {intent.intent}\nConfidence: {intent.confidence:.2f}",
            MODE_INSTRUCTIONS[mode],
            "[User Profile]\n" + (profile_context.strip() or "None"),
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
            return "[Project Context]\nNo active project."

        project = session.project
        name = _project_display(project)
        status = str(project.get("status") or "unknown")
        lines = ["[Project Context]", f"Name: {name}", f"Status: {status}"]

        if mode == Mode.CONVERSATION:
            tech_stack = project.get("tech_stack")
            if isinstance(tech_stack, str):
                try:
                    parsed = json.loads(tech_stack)
                    tech_stack = parsed
                except Exception:
                    pass
            lines.append(f"Tech Stack: {tech_stack if tech_stack else 'unknown'}")
            return "\n".join(lines)

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
            lines.append("Ideas:")
            if ideas:
                for idea in ideas[:20]:
                    if isinstance(idea, dict):
                        text = idea.get("message_text") or idea.get("text") or ""
                    else:
                        text = str(idea)
                    lines.append(f"- {(text or '').strip()[:180]}")
            else:
                lines.append("- None captured yet.")
            return "\n".join(lines)

        working_dir = str(project.get("local_path") or "unknown")
        lines.append(f"Working Directory: {working_dir}")
        return "\n".join(lines)
