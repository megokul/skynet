"""SKYNET — Build Skill (run_tests, install_dependencies, lint_project, build_project)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class BuildSkill(BaseSkill):
    name = "build"
    description = "Build, test, lint, and dependency management"
    allowed_roles = []
    plan_auto_approved = {"run_tests", "lint_project", "build_project", "install_dependencies"}

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "run_tests",
                "description": "Run the project test suite. Returns stdout, stderr, and exit code.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Project directory"},
                        "runner": {
                            "type": "string",
                            "enum": ["pytest", "npm"],
                            "description": "Test runner (default: pytest)",
                        },
                    },
                    "required": ["working_dir"],
                },
            },
            {
                "name": "install_dependencies",
                "description": "Install project dependencies from requirements.txt (pip) or package.json (npm).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Project directory"},
                        "manager": {
                            "type": "string",
                            "enum": ["pip", "npm"],
                            "description": "Package manager (default: pip)",
                        },
                    },
                    "required": ["working_dir"],
                },
            },
            {
                "name": "lint_project",
                "description": "Run linting on the project.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Project directory"},
                        "linter": {
                            "type": "string",
                            "enum": ["ruff", "eslint"],
                            "description": "Linter (default: ruff)",
                        },
                    },
                    "required": ["working_dir"],
                },
            },
            {
                "name": "build_project",
                "description": "Build the project.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Project directory"},
                        "build_tool": {
                            "type": "string",
                            "enum": ["npm", "python"],
                            "description": "Build tool (default: npm)",
                        },
                    },
                    "required": ["working_dir"],
                },
            },
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any], context: SkillContext) -> str:
        return await context.send_to_agent(tool_name, tool_input)
