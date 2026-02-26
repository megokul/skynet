"""SKYNET — Filesystem Skill (file_write, file_read, list_directory, create_directory)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class FilesystemSkill(BaseSkill):
    """
    FilesystemSkill.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `FilesystemSkill`.
    """

    name = "filesystem"
    description = "File and directory operations on the laptop agent"
    allowed_roles = []  # All agents can use filesystem
    plan_auto_approved = {"file_write", "file_read", "list_directory", "create_directory"}

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
                "name": "file_write",
                "description": (
                    "Create or overwrite a file with the given content. "
                    "Parent directories are created automatically. "
                    "The file path must be an absolute path within the project directory."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Absolute file path"},
                        "content": {"type": "string", "description": "Complete file content"},
                    },
                    "required": ["file", "content"],
                },
            },
            {
                "name": "file_read",
                "description": "Read the contents of a file. Returns file content as text (max 64 KB).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Absolute file path"},
                    },
                    "required": ["file"],
                },
            },
            {
                "name": "list_directory",
                "description": "List files and subdirectories. Returns names with [DIR] prefix for dirs.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Absolute directory path"},
                        "recursive": {
                            "type": "boolean",
                            "description": "List recursively (max depth 3). Default: false.",
                        },
                    },
                    "required": ["directory"],
                },
            },
            {
                "name": "create_directory",
                "description": "Create a directory and any missing parent directories.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Absolute directory path"},
                    },
                    "required": ["directory"],
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
