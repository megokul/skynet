from __future__ import annotations

from skynet.project_specialist import (
    build_planner_chat_prompt,
    build_qwen_planner_context,
    build_qwen_planner_prompt,
    build_project_specialist_opening,
    build_project_specialist_system_prompt,
)


def test_planner_chat_prompt_normalizes_specialist_opening() -> None:
    template = {
        "stack": "Python 3.11+ + FastAPI or Flask + SQLAlchemy + PostgreSQL",
        "questions": [
            "What does this app do? (web service, automation, utility, other)",
            "Which framework? (FastAPI, Flask, plain Python script)",
        ],
    }
    system = build_project_specialist_system_prompt("preflight-qwen", "Python App", template)
    prompt = build_planner_chat_prompt(
        system,
        [
            {
                "role": "assistant",
                "content": build_project_specialist_opening("preflight-qwen", "Python App", template),
            },
            {"role": "user", "content": "It should show a popup and beep on Windows."},
        ],
    )

    assert "I'm your Project Specialist." not in prompt
    assert "Project: <b>preflight-qwen</b>" not in prompt
    assert "What does this app do? (web service, automation, utility, other)" in prompt
    assert "Do not ask what the user wants to work on." in prompt


def test_qwen_planner_prompt_is_short_and_task_directed() -> None:
    messages = [
        {"role": "assistant", "content": "What does this app do? (web service, automation, utility, other)"},
        {"role": "user", "content": "A small Windows terminal script that shows a popup and beeps."},
    ]
    prompt = build_qwen_planner_prompt(messages)
    context = build_qwen_planner_context("System instructions go here.")

    assert "Latest user message:" in prompt
    assert "Conversation history JSON" not in prompt
    assert "Ignore the working directory" in context
