"""Conversation mode selection and tool-policy enforcement.

Purpose:
- Map classified intents to runtime interaction modes.
- Gate which tools are callable under each mode.
- Restrict providers for modes that require specific tool-result formats.

How it works:
- Selects mode with confidence thresholds plus contextual overrides.
- Filters declared tools through allowlist policies per mode.
- Exposes provider allowlist hints for execution/recovery compatibility.

Why this exists:
- Prevents over-permissive tool access in conversational flows.
- Encapsulates policy in one place so behavior changes stay auditable.
- Reduces runtime failures from incompatible provider/tool protocols."""

from __future__ import annotations

import logging
from enum import Enum

from bot.intent import ClassifiedIntent
from bot.session import Session

logger = logging.getLogger(__name__)


class Mode(str, Enum):
    """
    Mode.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `Mode`.
    """

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
    """
    Select mode.
    
    Purpose:
    - Implement `select_mode` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `intent`: input used by this function to compute or route work.
    - `session`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `Mode` when available; otherwise side effects only.
    """

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
    """
    ToolPolicyGate.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `ToolPolicyGate`.
    """

    def filter(self, mode: Mode, all_tools: list[dict]) -> list[dict]:
        """
        Filter.
        
        Purpose:
        - Implement `filter` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `mode`: input used by this function to compute or route work.
        - `all_tools`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `list[dict]` when available; otherwise side effects only.
        """

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
