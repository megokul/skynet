"""
SKYNET - Main Persona Agent

Defines high-level interaction and delegation policy for Telegram chat.
"""

from __future__ import annotations

import re

from core.prompt_library import load_prompt


class MainPersonaAgent:
    """Policy helper for main agent behavior."""

    DELEGATE_PATTERNS = (
        r"\bimplement\b",
        r"\bbuild\b",
        r"\bdeploy\b",
        r"\brefactor\b",
        r"\bintegrat(?:e|ion)\b",
        r"\bwrite tests?\b",
        r"\bcreate (?:an?|the)? ?(?:project|repo|service|pipeline)\b",
    )
    _POLICY_PROMPT = load_prompt("agents/main_persona_policy.md")

    def compose_system_prompt(self, base_prompt: str, *, profile_context: str = "") -> str:
        """
        Compose system prompt.
        
        Purpose:
        - Implement `compose_system_prompt` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `base_prompt`: input used by this function to compute or route work.
        - `profile_context`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        policy = self._POLICY_PROMPT
        if profile_context.strip():
            return f"{base_prompt}\n\n{policy}\n\n[User Profile]\n{profile_context.strip()}"
        return f"{base_prompt}\n\n{policy}"

    def should_delegate(self, text: str) -> bool:
        """
        Should delegate.
        
        Purpose:
        - Implement `should_delegate` within this module's workflow.
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

        lowered = (text or "").strip().lower()
        if len(lowered) >= 240:
            return True
        for pattern in self.DELEGATE_PATTERNS:
            if re.search(pattern, lowered):
                return True
        return False

    def compose_final_response(self, answer: str, *, task_report_summary: str = "") -> str:
        """
        Compose final response.
        
        Purpose:
        - Implement `compose_final_response` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `answer`: input used by this function to compute or route work.
        - `task_report_summary`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        answer = (answer or "").strip()
        if not task_report_summary.strip():
            return answer
        return f"{answer}\n\nTask report summary: {task_report_summary.strip()}"
