from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping

from skynet.project_specialist import build_planner_state


NAME_KEY = "project_name"
TYPE_KEY = "project_type"
REQS_HISTORY_KEY = "project_reqs_history"
PLAN_KEY = "project_plan"
PLANNER_STATE_KEY = "project_planner_state"


@dataclass(slots=True)
class ProjectConversationSession:
    user_data: MutableMapping[str, Any]
    max_history_turns: int = 30

    def project_name(self, default: str = "Untitled") -> str:
        return str(self.user_data.get(NAME_KEY, default) or default)

    def set_project_name(self, value: str) -> None:
        self.user_data[NAME_KEY] = str(value or "").strip()

    def project_type(self, default: str = "Other") -> str:
        return str(self.user_data.get(TYPE_KEY, default) or default)

    def set_project_type(self, value: str) -> None:
        self.user_data[TYPE_KEY] = str(value or "").strip()

    def history(self) -> list[dict[str, Any]]:
        raw = self.user_data.get(REQS_HISTORY_KEY, [])
        return list(raw) if isinstance(raw, list) else []

    def set_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        max_messages = self.max_history_turns * 2
        trimmed = list(history or [])
        if len(trimmed) > max_messages:
            trimmed = trimmed[-max_messages:]
        self.user_data[REQS_HISTORY_KEY] = trimmed
        return trimmed

    def append_history(self, role: str, content: str) -> list[dict[str, Any]]:
        history = self.history()
        history.append({"role": str(role or "").strip(), "content": str(content or "").strip()})
        return self.set_history(history)

    def seed_opening(
        self,
        *,
        opening: str,
        project_name: str,
        project_type_label: str,
    ) -> dict[str, Any]:
        self.set_history([{"role": "assistant", "content": str(opening or "").strip()}])
        return self.refresh_planner_state(project_name=project_name, project_type_label=project_type_label)

    def plan(self, default: str = "") -> str:
        return str(self.user_data.get(PLAN_KEY, default) or default)

    def set_plan(self, plan: str) -> None:
        self.user_data[PLAN_KEY] = str(plan or "")

    def planner_state(self) -> dict[str, Any]:
        raw = self.user_data.get(PLANNER_STATE_KEY, {})
        return dict(raw) if isinstance(raw, dict) else {}

    def refresh_planner_state(
        self,
        *,
        project_name: str,
        project_type_label: str,
    ) -> dict[str, Any]:
        state = build_planner_state(
            project_name=str(project_name or "").strip(),
            project_type_label=str(project_type_label or "").strip(),
            messages=self.history(),
        )
        self.user_data[PLANNER_STATE_KEY] = state
        return state

    def clear(self) -> None:
        for key in (NAME_KEY, TYPE_KEY, REQS_HISTORY_KEY, PLAN_KEY, PLANNER_STATE_KEY):
            self.user_data.pop(key, None)
