"""SKYNET — Docker Skill (docker_build, docker_compose_up)."""

from __future__ import annotations
from typing import Any
from .base import BaseSkill, SkillContext


class DockerSkill(BaseSkill):
    name = "docker"
    description = "Docker container build and orchestration"
    allowed_roles = ["devops", "backend", "deployment"]
    plan_auto_approved = {"docker_build", "docker_compose_up"}

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "docker_build",
                "description": "Build a Docker image from a Dockerfile in the project directory.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Directory containing Dockerfile"},
                        "tag": {
                            "type": "string",
                            "description": "Image tag (default: chathan-build:latest)",
                        },
                    },
                    "required": ["working_dir"],
                },
            },
            {
                "name": "docker_compose_up",
                "description": "Start services defined in docker-compose.yml.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "working_dir": {"type": "string", "description": "Directory containing docker-compose.yml"},
                    },
                    "required": ["working_dir"],
                },
            },
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any], context: SkillContext) -> str:
        return await context.send_to_agent(tool_name, tool_input)
