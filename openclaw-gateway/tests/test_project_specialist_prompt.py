from __future__ import annotations

import json

from skynet.project_specialist import (
    build_planner_state,
    build_qwen_plan_generation_context,
    build_planner_chat_prompt,
    build_qwen_planner_context,
    build_qwen_planner_prompt,
    build_requirement_summary_markdown,
    build_project_specialist_opening,
    build_project_specialist_system_prompt,
    normalize_planner_history,
    ready_sentence,
    should_qwen_finalize_planner_chat,
)
from skynet.prompt_library import load_prompt, render_prompt


def _planner_template() -> dict[str, object]:
    return {
        "stack": "Python 3.11+ + FastAPI or Flask + SQLAlchemy + PostgreSQL",
        "questions": [
            "What does this app do? (web service, automation, utility, other)",
            "Which framework? (FastAPI, Flask, plain Python script)",
        ],
    }


def test_planner_chat_prompt_normalizes_specialist_opening() -> None:
    template = _planner_template()
    messages = [
        {
            "role": "assistant",
            "content": build_project_specialist_opening("preflight-qwen", "Python App", template),
        },
        {"role": "user", "content": "It should show a popup and beep on Windows."},
    ]
    system = build_project_specialist_system_prompt("preflight-qwen", "Python App", template)
    prompt = build_planner_chat_prompt(system, messages)
    normalized_history = normalize_planner_history(messages)

    assert prompt == render_prompt(
        "gateway/planning/planner_chat.md",
        system=system,
        previous_assistant_message=normalized_history[0]["content"],
        latest_user_message=normalized_history[-1]["content"],
        transcript="\n".join(f"{item['role'].title()}: {item['content']}" for item in normalized_history),
        conversation_history_json=json.dumps(normalized_history, ensure_ascii=True),
    )


def test_qwen_planner_prompt_is_short_and_task_directed() -> None:
    template = _planner_template()
    messages = [
        {
            "role": "assistant",
            "content": build_project_specialist_opening("demo", "Python App", template),
        },
        {"role": "user", "content": "A small Windows terminal script that shows a popup and beeps."},
    ]
    prompt = build_qwen_planner_prompt(messages)
    context = build_qwen_planner_context(load_prompt("testing/common/system_instructions_placeholder.txt"))
    normalized_history = normalize_planner_history(messages)

    assert prompt == render_prompt(
        "gateway/planning/qwen_planner_question.md",
        latest_assistant_message=normalized_history[0]["content"],
        latest_user_message=normalized_history[-1]["content"],
        missing_slots_json="[]",
        question_targets_json="[]",
        planner_state_json="{}",
        requirement_summary="- None yet",
        conversation_history_json=json.dumps(normalized_history, ensure_ascii=True),
    )
    assert context == render_prompt(
        "gateway/planning/qwen_planner_context_default.md",
        ready_sentence=ready_sentence(),
        planner_state_json="{}",
        requirement_summary="- None yet",
        system=load_prompt("testing/common/system_instructions_placeholder.txt"),
    )


def test_qwen_plan_generation_prompt_carries_requirement_history() -> None:
    template = _planner_template()
    messages = [
        {
            "role": "assistant",
            "content": build_project_specialist_opening("demo", "Python App", template),
        },
        {"role": "user", "content": "A small Windows terminal script that shows a popup and beeps."},
        {"role": "user", "content": load_prompt("testing/common/generate_full_project_plan_now.txt")},
    ]

    prompt = build_qwen_planner_prompt(messages)
    context = build_qwen_plan_generation_context(load_prompt("testing/common/system_instructions_placeholder.txt"))
    normalized_history = normalize_planner_history(messages)

    assert prompt == render_prompt(
        "gateway/planning/qwen_planner_emit_plan.md",
        planner_state_json="{}",
        requirement_summary="- None yet",
        conversation_history_json=json.dumps(normalized_history, ensure_ascii=True),
    )
    assert context == render_prompt(
        "gateway/planning/qwen_plan_generation_context.md",
        planner_state_json="{}",
        requirement_summary="- None yet",
        system=load_prompt("testing/common/system_instructions_placeholder.txt"),
    )


def test_qwen_planner_prompt_can_force_completion_sentence_for_detailed_request() -> None:
    template = _planner_template()
    messages = [
        {
            "role": "assistant",
            "content": build_project_specialist_opening("demo", "Python App", template),
        },
        {
            "role": "user",
            "content": (
                "A small Windows Python script that runs from the terminal, shows a popup, "
                "plays a beep, uses only the standard library, includes tests, and writes skynet_run.json."
            ),
        },
    ]

    assert should_qwen_finalize_planner_chat(messages) is True
    assert build_qwen_planner_prompt(messages) == render_prompt(
        "gateway/planning/qwen_planner_ready.md",
        ready_sentence=ready_sentence(),
        planner_state_json="{}",
        requirement_summary="- None yet",
        conversation_history_json=json.dumps(normalize_planner_history(messages), ensure_ascii=True),
    )


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
        load_prompt("testing/common/system_instructions_placeholder.txt"),
        state,
        reply_contract="emit_ready_sentence",
    )
    prompt = build_qwen_planner_prompt(
        [{"role": "user", "content": load_prompt("testing/common/next_reply_only.txt")}],
        planner_state=state,
        reply_contract="emit_ready_sentence",
    )

    assert context == render_prompt(
        "gateway/planning/qwen_planner_context_ready.md",
        ready_sentence=ready_sentence(),
        planner_state_json=json.dumps(state, ensure_ascii=True),
        requirement_summary=build_requirement_summary_markdown(state),
        system=load_prompt("testing/common/system_instructions_placeholder.txt"),
    )
    assert prompt == render_prompt(
        "gateway/planning/qwen_planner_ready.md",
        ready_sentence=ready_sentence(),
        planner_state_json=json.dumps(state, ensure_ascii=True),
        requirement_summary=build_requirement_summary_markdown(state),
        conversation_history_json=json.dumps(
            normalize_planner_history([{"role": "user", "content": load_prompt("testing/common/next_reply_only.txt")}]),
            ensure_ascii=True,
        ),
    )


def test_build_planner_state_marks_complete_windows_script_request_ready() -> None:
    template = _planner_template()
    messages = [
        {"role": "assistant", "content": build_project_specialist_opening("demo", "Python App", template)},
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
    template = _planner_template()
    messages = [
        {"role": "assistant", "content": build_project_specialist_opening("demo", "Python App", template)},
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
