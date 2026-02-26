"""
Role registration and lookup utilities.

This module builds the default in-process role set used by the conversation
engine and provides stable fallback behavior when unknown role names appear.
"""

from __future__ import annotations

from typing import Any

from core.roles.base import Role
from core.roles.coding_specialist import CodingSpecialistRole
from core.roles.igris import IgrisRole
from core.roles.project_specialist import ProjectSpecialistRole
from core.roles.reminder_specialist import ReminderSpecialistRole
from core.roles.research_specialist import ResearchSpecialistRole
from core.roles.weather_specialist import WeatherSpecialistRole


class RoleRegistry:
    """
    In-memory role lookup map.

    Registry is process-local and initialized once at engine startup.
    """

    def __init__(self) -> None:
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
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
        - Return value typed as `None` when available; otherwise side effects only.
        """

        self._roles: dict[str, Role] = {}

    def register(self, role: Role) -> None:
        """Register/replace a role by its declared name."""
        self._roles[role.name] = role

    def get(self, role_name: str) -> Role:
        """
        Resolve role by name with safe fallback to commander (`igris`).

        Fallback prevents runtime failures when stale role names are seen in DB.
        """
        normalized = (role_name or "igris").strip() or "igris"
        role = self._roles.get(normalized)
        if not role:
            role = self._roles["igris"]
        return role

    def names(self) -> list[str]:
        """Return sorted list of registered role names."""
        return sorted(self._roles.keys())


def build_default_registry(*, dependencies: dict[str, Any]) -> RoleRegistry:
    """
    Build default runtime registry.

    `dependencies` is accepted for forward compatibility with role constructors
    that may require injected services later.
    """
    del dependencies
    registry = RoleRegistry()
    registry.register(IgrisRole())
    registry.register(ProjectSpecialistRole())
    registry.register(CodingSpecialistRole())
    registry.register(WeatherSpecialistRole())
    registry.register(ReminderSpecialistRole())
    registry.register(ResearchSpecialistRole())
    return registry
