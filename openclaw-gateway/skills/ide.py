"""SKYNET — IDE Skill (open_in_vscode)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class IDESkill(BaseSkill):
    """
    IDESkill.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `IDESkill`.
    """

    name = "ide"
    description = "IDE integration (VS Code + local coding agent CLIs)"
    allowed_roles = ["frontend", "backend", "devops"]
    plan_auto_approved = {"open_in_vscode", "check_coding_agents", "run_coding_agent"}
    requires_approval = {"configure_coding_agent"}

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
            {
                "name": "check_coding_agents",
                "description": (
                    "Check if local coding agent CLIs are installed on the laptop. "
                    "Reports Codex, Claude, and Cline availability."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "run_coding_agent",
                "description": (
                    "Run a local coding agent CLI in non-interactive mode on the laptop "
                    "(Codex, Claude, or Cline)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": ["codex", "claude", "cline"],
                            "description": "Which coding agent to run.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Task prompt to send to the coding agent.",
                        },
                        "working_dir": {
                            "type": "string",
                            "description": "Optional project directory on the laptop.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Optional timeout (30-3600, default 1800).",
                        },
                    },
                    "required": ["agent", "prompt"],
                },
            },
            {
                "name": "configure_coding_agent",
                "description": (
                    "Configure a local coding agent provider/model. "
                    "Currently supports switching Cline provider auth."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": ["cline"],
                            "description": "Coding agent to configure.",
                        },
                        "provider": {
                            "type": "string",
                            "enum": ["gemini", "deepseek", "groq", "openrouter", "openai", "anthropic"],
                            "description": "Provider to set for Cline.",
                        },
                        "model": {
                            "type": "string",
                            "description": "Optional provider model id.",
                        },
                        "base_url": {
                            "type": "string",
                            "description": "Optional base URL (mainly for OpenAI-compatible providers).",
                        },
                    },
                    "required": ["agent", "provider"],
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
