from __future__ import annotations

from skynet.project_specialist import (
    build_planner_state,
    build_qwen_plan_generation_context,
    build_planner_chat_prompt,
    build_qwen_planner_context,
    build_qwen_planner_prompt,
    build_requirement_summary_markdown,
    build_project_specialist_opening,
    build_project_specialist_system_prompt,
    ready_sentence,
    should_qwen_finalize_planner_chat,
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
    assert "Conversation history JSON" in prompt
    assert "Ignore the working directory" in context
    assert "Do not repeat a question" in prompt


def test_qwen_plan_generation_prompt_carries_requirement_history() -> None:
    messages = [
        {"role": "assistant", "content": "What does this app do? (web service, automation, utility, other)"},
        {"role": "user", "content": "A small Windows terminal script that shows a popup and beeps."},
        {"role": "user", "content": "Generate the full project plan now based on everything we discussed."},
    ]

    prompt = build_qwen_planner_prompt(messages)
    context = build_qwen_plan_generation_context("System instructions go here.")

    assert "Conversation history JSON" in prompt
    assert "A small Windows terminal script" in prompt
    assert "Do not ask follow-up questions." in prompt
    assert "Do not say requirements are missing." in context


def test_qwen_planner_prompt_can_force_completion_sentence_for_detailed_request() -> None:
    messages = [
        {"role": "assistant", "content": "What does this app do? (web service, automation, utility, other)"},
        {
            "role": "user",
            "content": (
                "A small Windows Python script that runs from the terminal, shows a popup, "
                "plays a beep, uses only the standard library, includes tests, and writes skynet_run.json."
            ),
        },
    ]

    assert should_qwen_finalize_planner_chat(messages) is True
    prompt = build_qwen_planner_prompt(messages)
    assert "Required completion sentence" in prompt
    assert ready_sentence() in prompt


def test_qwen_ready_sentence_context_forbids_inline_plan_generation() -> None:
    state = {
        "plan_ready": True,
        "missing_slots": [],
        "requirement_summary": (
            "- Project Kind: local terminal utility script\n"
            "- Constraints: show a popup saying \"hi\" and play a beep"
        ),
    }

    context = build_qwen_planner_context(
        "System instructions go here.",
        state,
        reply_contract="emit_ready_sentence",
    )
    prompt = build_qwen_planner_prompt(
        [{"role": "user", "content": "Generate the next reply only."}],
        planner_state=state,
        reply_contract="emit_ready_sentence",
    )

    assert "Do not generate the project plan yet." in context
    assert "Stop immediately after the final period" in context
    assert "Do not generate the plan yet." in prompt
    assert ready_sentence() in prompt


def test_build_planner_state_marks_complete_windows_script_request_ready() -> None:
    messages = [
        {"role": "assistant", "content": "What does this app do? (web service, automation, utility, other)"},
        {
            "role": "user",
            "content": (
                "A small Windows Python script that runs from the terminal, shows a popup saying hi, "
                "plays a short beep, uses only the standard library, includes tests, and adds skynet_run.json."
            ),
        },
    ]
    state = build_planner_state(
        project_name="demo",
        project_type_label="Python App",
        messages=messages,
    )

    assert state["plan_ready"] is True
    assert state["missing_slots"] == []
    assert state["facts"]["runtime_mode"] == "on-demand local execution"
    assert state["facts"]["storage"] == "no persistent storage required"
    assert state["facts"]["integrations"] == "no external integrations required"
    summary = build_requirement_summary_markdown(state)
    assert "Windows" in summary
    assert "standard library" in summary.lower()


def test_build_planner_state_keeps_missing_slots_for_incomplete_request() -> None:
    messages = [
        {"role": "assistant", "content": "What does this app do? (web service, automation, utility, other)"},
        {"role": "user", "content": "A FastAPI web service."},
    ]
    state = build_planner_state(
        project_name="demo",
        project_type_label="Python App",
        messages=messages,
    )

    assert state["plan_ready"] is False
    assert "storage" in state["missing_slots"]
    assert "integrations" in state["missing_slots"]
    assert "runtime_mode" in state["missing_slots"]
