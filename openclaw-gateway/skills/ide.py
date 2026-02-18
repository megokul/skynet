"""SKYNET — IDE Skill (open_in_vscode)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class IDESkill(BaseSkill):
    name = "ide"
    description = "IDE integration (VS Code)"
    allowed_roles = ["frontend", "backend", "devops"]
    plan_auto_approved = {"open_in_vscode"}

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "open_in_vscode",
                "description": "Open a project directory or file in VS Code on the laptop.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to open in VS Code"},
                    },
                    "required": ["path"],
                },
            },
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any], context: SkillContext) -> str:
        return await context.send_to_agent(tool_name, tool_input)
