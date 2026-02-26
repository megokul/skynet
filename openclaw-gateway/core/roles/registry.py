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
    """In-memory registry for conversation roles."""

    def __init__(self) -> None:
        self._roles: dict[str, Role] = {}

    def register(self, role: Role) -> None:
        self._roles[role.name] = role

    def get(self, role_name: str) -> Role:
        normalized = (role_name or "igris").strip() or "igris"
        role = self._roles.get(normalized)
        if not role:
            role = self._roles["igris"]
        return role

    def names(self) -> list[str]:
        return sorted(self._roles.keys())


def build_default_registry(*, dependencies: dict[str, Any]) -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(IgrisRole())
    registry.register(ProjectSpecialistRole())
    registry.register(CodingSpecialistRole())
    registry.register(WeatherSpecialistRole())
    registry.register(ReminderSpecialistRole())
    registry.register(ResearchSpecialistRole())
    return registry