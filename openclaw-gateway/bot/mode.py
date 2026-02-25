from __future__ import annotations

import logging
from enum import Enum

from bot.intent import ClassifiedIntent
from bot.session import Session

logger = logging.getLogger(__name__)


class Mode(str, Enum):
    CONVERSATION = "conversation"
    PLANNING = "planning"
    EXECUTION = "execution"
    REVIEW = "review"
    RECOVERY = "recovery"


INTENT_MODE_MAP: dict[str, Mode] = {
    "greeting": Mode.CONVERSATION,
    "casual_conversation": Mode.CONVERSATION,
    "ask_question": Mode.CONVERSATION,
    "request_explanation": Mode.CONVERSATION,
    "provide_feedback": Mode.CONVERSATION,
    "unclear": Mode.CONVERSATION,
    "propose_idea": Mode.PLANNING,
    "request_plan": Mode.PLANNING,
    "reject_plan": Mode.PLANNING,
    "change_direction": Mode.PLANNING,
    "approve_plan": Mode.EXECUTION,
    "approve_execution": Mode.EXECUTION,
    "request_execution": Mode.EXECUTION,
    "request_fix": Mode.EXECUTION,
    "request_review": Mode.REVIEW,
    "request_continue": Mode.RECOVERY,
    "request_stop": Mode.CONVERSATION,
    "memory_command": Mode.CONVERSATION,
}


def select_mode(intent: ClassifiedIntent, session: Session) -> Mode:
    if intent.confidence < 0.7:
        return Mode.CONVERSATION
    mode = INTENT_MODE_MAP.get(intent.intent, Mode.CONVERSATION)
    # Contextual overrides
    if intent.intent == "provide_feedback" and session.last_mode == "execution":
        mode = Mode.EXECUTION
    if intent.intent == "request_continue" and not session.project_id:
        mode = Mode.CONVERSATION
    if mode == Mode.EXECUTION and not session.project_id:
        mode = Mode.PLANNING
    return mode


TOOL_POLICY: dict[Mode, list[str] | str] = {
    Mode.CONVERSATION: [
        "file_read", "list_directory", "web_search",
        "project_status", "project_list",
    ],
    Mode.PLANNING: [
        "file_read", "list_directory", "web_search",
        "project_status", "project_list",
        "project_create", "project_add_idea", "project_generate_plan",
        "project_generate_docs",
    ],
    Mode.EXECUTION: "*",
    Mode.REVIEW: [
        "file_read", "list_directory", "run_tests",
        "git_status", "git_diff", "git_log",
        "project_status", "project_list",
    ],
    Mode.RECOVERY: "*",
}

# Execution/Recovery use Anthropic-style tool_result content blocks.
# Not all providers handle this. Restrict execution modes to compatible providers.
MODE_PROVIDER_ALLOWLIST: dict[Mode, list[str] | None] = {
    Mode.CONVERSATION: None,  # None = use global _CHAT_PROVIDER_ALLOWLIST
    Mode.PLANNING: None,
    Mode.EXECUTION: ["anthropic"],
    Mode.REVIEW: None,
    Mode.RECOVERY: ["anthropic"],
}


class ToolPolicyGate:
    def filter(self, mode: Mode, all_tools: list[dict]) -> list[dict]:
        allowed = TOOL_POLICY[mode]
        if allowed == "*":
            return all_tools
        allowed_set = set(allowed)
        filtered = []
        for t in all_tools:
            # Normalize: some providers use {"name": ...}, others {"function": {"name": ...}}
            name = t.get("name") or t.get("function", {}).get("name")
            if name in allowed_set:
                filtered.append(t)
        logger.debug(
            "ToolPolicyGate: mode=%s allowed=%s",
            mode.value,
            [t.get("name") or t.get("function", {}).get("name") for t in filtered],
        )
        return filtered
