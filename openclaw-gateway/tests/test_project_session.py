from __future__ import annotations

from bot.handlers.project_session import (
    NAME_KEY,
    PLANNER_STATE_KEY,
    REQS_HISTORY_KEY,
    TYPE_KEY,
    ProjectConversationSession,
)
from skynet.project_specialist import build_project_specialist_opening


_PROJECT_TEMPLATE = {
    "stack": "Python 3.11+ + FastAPI or Flask + SQLAlchemy + PostgreSQL",
    "questions": [
        "What does this app do? (web service, automation, utility, other)",
        "Any other constraints?",
    ],
}


def test_project_session_trims_history_and_refreshes_planner_state() -> None:
    session = ProjectConversationSession({}, max_history_turns=2)
    session.set_project_name("demo")
    session.set_project_type("Python App")

    session.append_history("assistant", build_project_specialist_opening("demo", "Python App", _PROJECT_TEMPLATE))
    session.append_history("user", "A Windows terminal script with popup and beep.")
    session.append_history("assistant", _PROJECT_TEMPLATE["questions"][1])
    history = session.append_history("user", "Use only the standard library and include tests.")
    planner_state = session.refresh_planner_state(project_name="demo", project_type_label="Python App")

    assert len(history) == 4
    assert planner_state["plan_ready"] is True
    assert planner_state["missing_slots"] == []
    assert "standard library" in planner_state["requirement_summary"].lower()


def test_project_session_clear_removes_all_project_keys() -> None:
    user_data = {
        NAME_KEY: "demo",
        TYPE_KEY: "Python App",
        REQS_HISTORY_KEY: [{"role": "assistant", "content": build_project_specialist_opening("demo", "Python App", _PROJECT_TEMPLATE)}],
        PLANNER_STATE_KEY: {"plan_ready": False},
        "unrelated": "keep-me",
    }
    session = ProjectConversationSession(user_data, max_history_turns=2)

    session.clear()

    assert NAME_KEY not in user_data
    assert TYPE_KEY not in user_data
    assert REQS_HISTORY_KEY not in user_data
    assert PLANNER_STATE_KEY not in user_data
    assert user_data["unrelated"] == "keep-me"
