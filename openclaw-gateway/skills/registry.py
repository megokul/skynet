"""
SKYNET — Skill Registry

Central registry for all available skills. Provides role-filtered
tool discovery and tool-to-skill routing.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseSkill, SkillContext

logger = logging.getLogger("skynet.skills.registry")


class SkillRegistry:
    """Central registry for all available skills."""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill
        logger.debug("Registered skill: %s (%d tools)", skill.name, len(skill.get_tool_names()))

    def get_tools_for_role(self, role: str) -> list[dict[str, Any]]:
        """Return combined tool definitions for an agent role."""
        tools = []
        for skill in self._skills.values():
            if not skill.allowed_roles or role in skill.allowed_roles:
                tools.extend(skill.get_tools())
        return tools

    def get_all_tools(self) -> list[dict[str, Any]]:
        """Return all tool definitions (for backward compatibility)."""
        tools = []
        for skill in self._skills.values():
            tools.extend(skill.get_tools())
        return tools

    def get_skill_for_tool(self, tool_name: str) -> BaseSkill | None:
        """Find which skill handles a given tool name."""
        for skill in self._skills.values():
            if tool_name in skill.get_tool_names():
                return skill
        return None

    def is_plan_auto_approved(self, tool_name: str) -> bool:
        """Check if a tool is auto-approved when plan is approved."""
        skill = self.get_skill_for_tool(tool_name)
        return skill is not None and tool_name in skill.plan_auto_approved

    def requires_approval(self, tool_name: str) -> bool:
        """Check if a tool always requires Telegram approval."""
        skill = self.get_skill_for_tool(tool_name)
        return skill is not None and tool_name in skill.requires_approval

    def list_skills(self) -> list[dict[str, Any]]:
        """Return summary of all registered skills (for /skills command)."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "tools": sorted(s.get_tool_names()),
                "allowed_roles": s.allowed_roles or ["all"],
            }
            for s in self._skills.values()
        ]

    @property
    def skill_count(self) -> int:
        return len(self._skills)


def build_default_registry() -> SkillRegistry:
    """Build the default skill registry with all built-in skills."""
    from .filesystem import FilesystemSkill
    from .git import GitSkill
    from .build import BuildSkill
    from .search import SearchSkill
    from .ide import IDESkill
    from .docker import DockerSkill
    from .skynet_delegate import SkynetDelegateSkill

    registry = SkillRegistry()
    registry.register(FilesystemSkill())
    registry.register(GitSkill())
    registry.register(BuildSkill())
    registry.register(SearchSkill())
    registry.register(IDESkill())
    registry.register(DockerSkill())
    registry.register(SkynetDelegateSkill())

    logger.info(
        "Skill registry ready: %d skills, %d total tools",
        registry.skill_count,
        sum(len(s.get_tool_names()) for s in registry._skills.values()),
    )
    return registry
