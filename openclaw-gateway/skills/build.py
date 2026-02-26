"""SKYNET — Build Skill (run_tests, install_dependencies, lint_project, build_project)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class BuildSkill(BaseSkill):
    """
    BuildSkill.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `BuildSkill`.
    """

    name = "build"
    description = "Build, test, lint, and dependency management"
    allowed_roles = []
    plan_auto_approved = {"run_tests", "lint_project", "build_project", "install_dependencies"}

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

        return await context.send_to_agent(tool_name, tool_input)
