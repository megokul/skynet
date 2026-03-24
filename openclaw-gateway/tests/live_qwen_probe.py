from __future__ import annotations

from typing import Any

from bot.project_templates import get_template
from skynet.project_specialist import (
    build_planner_state,
    build_project_specialist_opening,
    build_project_specialist_system_prompt,
    build_qwen_plan_generation_context,
    build_qwen_planner_context,
    build_qwen_planner_prompt,
    build_requirement_summary_markdown,
    ready_sentence,
)


def qwen_probe_requests() -> list[dict[str, Any]]:
    template = get_template("Python App")
    system = build_project_specialist_system_prompt("preflight-qwen", "Python App", template)
    conversation = [
        {
            "role": "assistant",
            "content": build_project_specialist_opening("preflight-qwen", "Python App", template),
        },
        {
            "role": "user",
            "content": (
                "I'm building a small Windows Python script that runs from the terminal. "
                "When executed, it should show a popup saying \"hi\" and play a short beep sound. "
                "Use only Python standard library, include tests, and add a valid skynet_run.json."
            ),
        },
    ]
    planner_state = build_planner_state(
        project_name="preflight-qwen",
        project_type_label="Python App",
        messages=conversation,
    )
    requirement_summary_md = build_requirement_summary_markdown(planner_state)
    plan_messages = list(conversation) + [
        {"role": "user", "content": "Generate the full project plan now based on everything we discussed."}
    ]
    return [
        {
            "probe_kind": "planner_ready",
            "task_mode": "planner_chat",
            "reply_contract": "emit_ready_sentence",
            "prompt": build_qwen_planner_prompt(
                conversation,
                planner_state=planner_state,
                reply_contract="emit_ready_sentence",
            ),
            "qwen_context_text": build_qwen_planner_context(
                system,
                planner_state,
                reply_contract="emit_ready_sentence",
            ),
            "planner_state_json": planner_state,
            "requirement_summary_md": requirement_summary_md,
            "expected_text": ready_sentence(),
        },
        {
            "probe_kind": "plan_generation",
            "task_mode": "plan_generation",
            "reply_contract": "emit_plan",
            "prompt": build_qwen_planner_prompt(
                plan_messages,
                planner_state=planner_state,
                reply_contract="emit_plan",
            ),
            "qwen_context_text": build_qwen_plan_generation_context(system, planner_state),
            "planner_state_json": planner_state,
            "requirement_summary_md": requirement_summary_md,
            "expected_text": "",
        },
    ]


def validate_qwen_probe_result(*, probe: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    probe_kind = str(probe.get("probe_kind") or "unknown")
    inner = result.get("result", result)
    returncode = int(inner.get("returncode", inner.get("exit_code", 0)) or 0)
    assistant_text = str(inner.get("assistant_text") or inner.get("stdout") or "").strip()
    output_contract = str(inner.get("output_contract") or "")
    expected_text = str(probe.get("expected_text") or "").strip()
    if expected_text and assistant_text != expected_text:
        raise AssertionError(
            f"PREFLIGHT_QWEN_PROVIDER_CAPABILITY_FAILED: probe={probe_kind} detail=unexpected_text:{assistant_text[:180]}"
        )
    if returncode != 0 or output_contract != "ok" or not assistant_text:
        detail = str(
            inner.get("stderr")
            or assistant_text
            or inner.get("raw_result_tail")
            or "empty qwen probe response"
        )
        raise AssertionError(
            f"PREFLIGHT_QWEN_PROVIDER_CAPABILITY_FAILED: probe={probe_kind} detail={detail[:220]}"
        )
    return {
        "probe_kind": probe_kind,
        "returncode": returncode,
        "output_contract": output_contract,
        "assistant_preview": assistant_text[:200],
        "model": str(inner.get("model") or ""),
        "session_id": str(inner.get("session_id") or ""),
        "context_files": list(inner.get("qwen_context_files") or []),
    }
