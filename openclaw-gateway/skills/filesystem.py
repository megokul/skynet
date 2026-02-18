"""SKYNET — Filesystem Skill (file_write, file_read, list_directory, create_directory)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class FilesystemSkill(BaseSkill):
    name = "filesystem"
    description = "File and directory operations on the laptop agent"
    allowed_roles = []  # All agents can use filesystem
    plan_auto_approved = {"file_write", "file_read", "list_directory", "create_directory"}

    def get_tools(self) -> list[dict[str, Any]]:
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
        return await context.send_to_agent(tool_name, tool_input)
