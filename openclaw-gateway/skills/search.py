"""SKYNET — Search Skill (web_search — handled locally on EC2)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class SearchSkill(BaseSkill):
    name = "search"
    description = "Web search for programming resources and documentation"
    allowed_roles = []

    def get_tools(self) -> list[dict[str, Any]]:
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
        if tool_name == "web_search" and context.searcher:
            return await context.searcher.search(
                tool_input.get("query", ""),
                tool_input.get("num_results", 5),
            )
        return "Web search is not available."
