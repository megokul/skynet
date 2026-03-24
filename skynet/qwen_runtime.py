from __future__ import annotations

from typing import Any

from skynet.qwen_cli import (
    QWEN_TASK_MODES,
    classify_qwen_output_contract,
    discover_qwen_context_paths,
    parse_qwen_json_output,
    qwen_profile_for_task_mode,
)


def normalize_qwen_task_mode(task_mode: str) -> str:
    normalized = str(task_mode or "").strip().lower()
    if not normalized:
        raise ValueError("Missing required parameter: 'task_mode'")
    if normalized not in QWEN_TASK_MODES:
        allowed = ", ".join(sorted(QWEN_TASK_MODES))
        raise ValueError(f"Unsupported qwen task_mode '{normalized}'. Allowed: {allowed}")
    return normalized


def normalize_qwen_result(
    result: dict[str, Any],
    *,
    task_mode: str,
    policy: dict[str, Any],
    working_dir: str | None,
    reply_contract: str = "",
    planner_state: dict[str, Any] | None = None,
    requirement_summary_md: str = "",
) -> dict[str, Any]:
    profile = qwen_profile_for_task_mode(policy, task_mode)
    output_format = str(profile.get("output_format") or "").strip().lower()
    raw_stdout = str(result.get("stdout") or "")
    parsed: dict[str, Any]

    if output_format == "json":
        try:
            parsed = parse_qwen_json_output(raw_stdout)
            parse_error = ""
        except Exception as exc:
            parsed = {
                "assistant_text": "",
                "session_id": str(result.get("session_key") or "").strip(),
                "model": str(profile.get("model") or "").strip(),
                "tool_use_names": [],
                "raw_result_tail": raw_stdout[-2000:],
            }
            parse_error = f"invalid_json_output:{type(exc).__name__}"
    else:
        parsed = {
            "assistant_text": raw_stdout.strip(),
            "session_id": str(result.get("session_key") or "").strip(),
            "model": str(profile.get("model") or "").strip(),
            "tool_use_names": [],
            "raw_result_tail": raw_stdout[-2000:],
        }
        parse_error = ""

    assistant_text = str(parsed.get("assistant_text") or "").strip()
    tool_use_names = [str(name).strip() for name in list(parsed.get("tool_use_names") or []) if str(name).strip()]
    output_contract = (
        parse_error
        if parse_error
        else classify_qwen_output_contract(
            task_mode=task_mode,
            assistant_text=assistant_text,
            tool_use_names=tool_use_names,
            reply_contract=reply_contract,
            planner_state=planner_state,
            requirement_summary_md=requirement_summary_md,
        )
    )
    context_files = (
        discover_qwen_context_paths(str(working_dir or ""))
        if bool(policy.get("context_diagnostics", True))
        else []
    )

    result["assistant_text"] = assistant_text
    result["session_id"] = str(parsed.get("session_id") or result.get("session_key") or "").strip()
    result["model"] = str(parsed.get("model") or profile.get("model") or "").strip()
    result["auth_type"] = str(policy.get("auth_type") or "").strip()
    result["qwen_provider_profile"] = str(dict(policy.get("provider") or {}).get("profile_name") or "").strip()
    result["output_contract"] = output_contract
    result["raw_result_tail"] = str(parsed.get("raw_result_tail") or raw_stdout[-2000:])
    result["qwen_task_mode"] = task_mode
    result["qwen_context_files"] = context_files
    result["qwen_tool_use_names"] = tool_use_names
    result["stdout"] = assistant_text or raw_stdout

    if output_contract != "ok":
        detail = f"QWEN_CONTRACT_VIOLATION: {output_contract}"
        stderr = str(result.get("stderr") or "").strip()
        result["stderr"] = f"{stderr}\n{detail}".strip() if stderr else detail
        if int(result.get("returncode", 0) or 0) == 0:
            result["returncode"] = 1
    return result
