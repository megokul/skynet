"""SKYNET — Search Skill (web_search — executed by the laptop worker agent)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class SearchSkill(BaseSkill):
    """
    SearchSkill.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `SearchSkill`.
    """

    name = "search"
    description = "Web search for programming resources and documentation"
    allowed_roles = []

    def get_tools(self) -> list[dict[str, Any]]:
        """
        Get tools.
        
        Purpose:
        - Implement `get_tools` within this module's workflow.
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
        - Return value typed as `list[dict[str, Any]]` when available; otherwise side effects only.
        """

        return [
            {
                "name": "web_search",
                "description": (
                    "Search the web for programming resources, library documentation, "
                    "API references, or implementation examples."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "num_results": {
                            "type": "integer",
                            "description": "Number of results (default: 5, max: 10)",
                        },
                    },
                    "required": ["query"],
                },
            },
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any], context: SkillContext) -> str:
        """
        Execute.
        
        Purpose:
        - Implement `execute` within this module's workflow.
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
        - `context`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        if tool_name != "web_search":
            return f"Unknown search tool: {tool_name}"
        if not isinstance(tool_input, dict):
            tool_input = {}
        result = await context.send_to_agent("web_search", {
            "query": tool_input.get("query", ""),
            "num_results": tool_input.get("num_results", 5),
        }, confirmed=True, include_exit_code=False)
        if result.startswith("ERROR:"):
            return (
                "Web search is temporarily unavailable right now. "
                "Continue with a best-effort response and clearly state uncertainty."
            )
        return result
