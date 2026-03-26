from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

from skynet.prompt_library import load_prompt, render_prompt


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_DEFAULT_REQUIRED_SLOTS = (
    "project_kind",
    "framework_or_script",
    "runtime_mode",
    "storage",
    "integrations",
    "constraints",
)
_SLOT_QUESTION_HINTS: dict[str, str] = {
    "project_kind": "What does this app do? (web service, automation, utility, or something else?)",
    "framework_or_script": "Will this be a plain Python script, FastAPI app, or Flask app?",
    "runtime_mode": "Will it run on-demand, as a background service, or on a schedule?",
    "storage": "Does it need to store data or use a database? If so, what kind?",
    "integrations": "Any external APIs, services, or system integrations needed?",
    "constraints": "Any important constraints like platform, tests, packaging, or library limits?",
}
_SLOT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "project_kind": ("what does", "app do", "web service", "automation", "utility", "script", "service", "tool"),
    "framework_or_script": ("framework", "fastapi", "flask", "plain python", "python script", "script"),
    "runtime_mode": ("background", "service", "scheduled", "schedule", "cron", "on-demand", "run"),
    "storage": ("database", "store", "persist", "sqlite", "postgres", "mysql", "data", "storage"),
    "integrations": ("api", "integrat", "external", "telegram", "webhook"),
    "constraints": ("windows", "linux", "mac", "stdlib", "standard library", "tests", "test", "json", "popup", "beep"),
}
_PERSISTENCE_TERMS = ("database", "store", "persist", "sqlite", "postgres", "mysql", "table", "record")
_INTEGRATION_TERMS = ("integrat", "external", "telegram", "slack", "discord", "webhook", "http")
_LOCAL_SCRIPT_TERMS = ("script", "terminal", "command line", "cli", "popup", "beep", "local", "utility")


def ready_sentence() -> str:
    return load_prompt("gateway/planning/ready_sentence.txt")


def required_slots_for_project_type(project_type_label: str) -> tuple[str, ...]:
    del project_type_label
    return _DEFAULT_REQUIRED_SLOTS


def build_project_specialist_system_prompt(name: str, project_type_label: str, template: dict[str, Any]) -> str:
    questions = "\n".join(f"- {q}" for q in list(template.get("questions") or []))
    stack = str(template.get("stack") or "").strip()
    return render_prompt(
        "gateway/planning/project_specialist_system.md",
        project_name=name,
        project_type_label=project_type_label,
        stack=stack,
        questions_block=questions or "- What does this app do?",
        ready_sentence=ready_sentence(),
    )


def build_project_specialist_opening(name: str, project_type_label: str, template: dict[str, Any]) -> str:
    first_question = str((list(template.get("questions") or ["What does this app do?"]) or ["What does this app do?"])[0])
    stack = str(template.get("stack") or "").strip()
    return render_prompt(
        "gateway/planning/project_specialist_opening.md",
        project_name=name,
        project_type_label=project_type_label,
        stack=stack,
        first_question=first_question,
    )


def _strip_html(text: str) -> str:
    return unescape(_HTML_TAG_RE.sub("", str(text or "")))


def _normalize_assistant_history(content: str) -> str:
    text = _strip_html(content)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    lowered = text.lower()
    if lowered.startswith("i'm your project specialist.") or lowered.startswith("im your project specialist."):
        for line in reversed(lines):
            if "?" in line:
                return line
        return lines[-1]
    return "\n".join(lines)


def normalize_planner_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in messages:
        role = str(item.get("role") or "").strip().lower()
        if role not in {"assistant", "user", "system"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        normalized_content = _normalize_assistant_history(content) if role == "assistant" else content
        normalized_content = str(normalized_content or "").strip()
        if normalized_content:
            normalized.append({"role": role, "content": normalized_content})
    return normalized


def _user_history_lines(messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in normalize_planner_history(messages):
        if item["role"] != "user":
            continue
        content = str(item["content"] or "").strip()
        if not content:
            continue
        lowered = content.lower()
        if lowered == "/plan" or lowered.startswith("generate the full project plan now"):
            continue
        lines.append(content)
    return lines


def _unique_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def _infer_constraints(blob: str) -> list[str]:
    constraints: list[str] = []
    if "windows" in blob:
        constraints.append("Target platform: Windows")
    if "linux" in blob:
        constraints.append("Target platform: Linux")
    if "mac" in blob or "macos" in blob:
        constraints.append("Target platform: macOS")
    if "standard library" in blob or "stdlib" in blob:
        constraints.append("Use Python standard library only")
    if "test" in blob:
        constraints.append("Include automated tests")
    if "skynet_run.json" in blob:
        constraints.append("Include a valid skynet_run.json")
    if "popup" in blob:
        constraints.append('Show a popup saying "hi" on execution')
    if "beep" in blob or "sound" in blob:
        constraints.append("Play a short beep sound on execution")
    return _unique_preserve(constraints)


def _slot_labels(slots: list[str]) -> list[str]:
    labels: list[str] = []
    for slot in slots:
        hint = _SLOT_QUESTION_HINTS.get(str(slot or "").strip().lower(), "")
        if hint:
            labels.append(hint)
    return labels


def build_planner_state(
    *,
    project_name: str,
    project_type_label: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    user_lines = _user_history_lines(messages)
    user_blob = " ".join(user_lines).lower()
    required_slots = list(required_slots_for_project_type(project_type_label))
    facts: dict[str, Any] = {}
    answered_slots: list[str] = []

    is_local_script = any(term in user_blob for term in _LOCAL_SCRIPT_TERMS)
    if "fastapi" in user_blob:
        facts["framework_or_script"] = "FastAPI"
        answered_slots.append("framework_or_script")
    elif "flask" in user_blob:
        facts["framework_or_script"] = "Flask"
        answered_slots.append("framework_or_script")
    elif (
        "plain python" in user_blob
        or "python script" in user_blob
        or "standard library" in user_blob
        or "stdlib" in user_blob
        or is_local_script
    ):
        facts["framework_or_script"] = "plain Python script"
        answered_slots.append("framework_or_script")

    if is_local_script:
        facts["project_kind"] = "local terminal utility script"
        answered_slots.append("project_kind")
    elif any(term in user_blob for term in ("web service", "api", "web app", "http service")):
        facts["project_kind"] = "web service"
        answered_slots.append("project_kind")
    elif "automation" in user_blob:
        facts["project_kind"] = "automation tool"
        answered_slots.append("project_kind")

    if any(term in user_blob for term in ("background service", "background", "daemon", "always running")):
        facts["runtime_mode"] = "background service"
        answered_slots.append("runtime_mode")
    elif any(term in user_blob for term in ("scheduled", "schedule", "cron")):
        facts["runtime_mode"] = "scheduled execution"
        answered_slots.append("runtime_mode")
    elif is_local_script:
        facts["runtime_mode"] = "on-demand local execution"
        answered_slots.append("runtime_mode")

    if any(term in user_blob for term in _PERSISTENCE_TERMS):
        facts["storage"] = "storage/database required"
        answered_slots.append("storage")
    elif is_local_script and not any(term in user_blob for term in _PERSISTENCE_TERMS):
        facts["storage"] = "no persistent storage required"
        answered_slots.append("storage")

    if any(term in user_blob for term in _INTEGRATION_TERMS):
        facts["integrations"] = "external integrations required"
        answered_slots.append("integrations")
    elif is_local_script and ("standard library" in user_blob or "stdlib" in user_blob):
        facts["integrations"] = "no external integrations required"
        answered_slots.append("integrations")

    constraints = _infer_constraints(user_blob)
    if constraints:
        facts["constraints"] = constraints
        answered_slots.append("constraints")

    answered_unique = [slot for slot in required_slots if slot in set(answered_slots)]
    missing_slots = [slot for slot in required_slots if slot not in set(answered_unique)]

    summary_lines: list[str] = []
    for slot in required_slots:
        value = facts.get(slot)
        if isinstance(value, list):
            for item in value:
                summary_lines.append(f"- {item}")
        elif value:
            label = slot.replace("_", " ").title()
            summary_lines.append(f"- {label}: {value}")
    if user_lines:
        summary_lines.extend(f"- User requirement: {line}" for line in user_lines[-4:])
    summary_lines = _unique_preserve(summary_lines)

    next_question_targets = missing_slots[:2]
    return {
        "project_name": str(project_name or "").strip(),
        "project_type": str(project_type_label or "").strip(),
        "facts": facts,
        "answered_slots": answered_unique,
        "missing_slots": missing_slots,
        "requirement_summary": "\n".join(summary_lines),
        "plan_ready": not missing_slots,
        "next_question_targets": next_question_targets,
        "next_question_hints": _slot_labels(next_question_targets),
    }


def build_requirement_summary_markdown(planner_state: dict[str, Any] | None) -> str:
    state = dict(planner_state or {})
    summary = str(state.get("requirement_summary") or "").strip()
    if summary:
        return summary
    facts = dict(state.get("facts") or {})
    lines: list[str] = []
    for key, value in facts.items():
        label = str(key or "").replace("_", " ").title()
        if isinstance(value, list):
            for item in value:
                lines.append(f"- {item}")
        elif value:
            lines.append(f"- {label}: {value}")
    return "\n".join(_unique_preserve(lines)).strip()


def is_qwen_plan_generation_request(messages: list[dict[str, Any]]) -> bool:
    normalized_history = normalize_planner_history(messages)
    latest_user_message = ""
    for item in normalized_history:
        if item["role"] == "user":
            latest_user_message = item["content"]
    lowered_user = latest_user_message.lower()
    return "generate the full project plan now" in lowered_user or lowered_user.strip() == "/plan"


def should_qwen_finalize_planner_chat(messages: list[dict[str, Any]]) -> bool:
    normalized_history = normalize_planner_history(messages)
    latest_user_message = ""
    for item in normalized_history:
        if item["role"] == "user":
            latest_user_message = item["content"]
    lowered = latest_user_message.lower()
    if not lowered:
        return False

    app_signals = (
        "script",
        "terminal",
        "command line",
        "cli",
        "utility",
        "automation",
        "bot",
        "web service",
        "api",
        "app",
    )
    implementation_signals = (
        "standard library",
        "plain python",
        "python script",
        "fastapi",
        "flask",
        "database",
        "postgres",
        "sqlalchemy",
    )
    delivery_signals = (
        "test",
        "windows",
        "linux",
        "mac",
        "json",
        "popup",
        "beep",
        "notification",
        "scheduled",
        "background",
        "on-demand",
    )
    app_score = sum(1 for item in app_signals if item in lowered)
    implementation_score = sum(1 for item in implementation_signals if item in lowered)
    delivery_score = sum(1 for item in delivery_signals if item in lowered)
    return app_score >= 1 and implementation_score >= 1 and delivery_score >= 1


def build_qwen_planner_context(
    system: str,
    planner_state: dict[str, Any] | None = None,
    *,
    reply_contract: str = "",
) -> str:
    state = dict(planner_state or {})
    summary = build_requirement_summary_markdown(state)
    contract = str(reply_contract or "").strip().lower()
    template_ref = "gateway/planning/qwen_planner_context_default.md"
    if contract == "emit_ready_sentence":
        template_ref = "gateway/planning/qwen_planner_context_ready.md"
    elif contract == "ask_next_question":
        template_ref = "gateway/planning/qwen_planner_context_question.md"
    return render_prompt(
        template_ref,
        ready_sentence=ready_sentence(),
        planner_state_json=json.dumps(state, ensure_ascii=True),
        requirement_summary=summary or "- None yet",
        system=system,
    )


def build_qwen_plan_generation_context(system: str, planner_state: dict[str, Any] | None = None) -> str:
    state = dict(planner_state or {})
    summary = build_requirement_summary_markdown(state)
    return render_prompt(
        "gateway/planning/qwen_plan_generation_context.md",
        planner_state_json=json.dumps(state, ensure_ascii=True),
        requirement_summary=summary or "- None yet",
        system=system,
    )


def build_qwen_planner_prompt(
    messages: list[dict[str, Any]],
    *,
    planner_state: dict[str, Any] | None = None,
    reply_contract: str = "",
) -> str:
    normalized_history = normalize_planner_history(messages)
    state = dict(planner_state or {})
    summary = build_requirement_summary_markdown(state)
    contract = str(reply_contract or "").strip().lower()
    if not contract:
        if is_qwen_plan_generation_request(messages):
            contract = "emit_plan"
        elif bool(state.get("plan_ready", False)) or should_qwen_finalize_planner_chat(messages):
            contract = "emit_ready_sentence"
        else:
            contract = "ask_next_question"
    latest_user_message = ""
    latest_assistant_message = ""
    for item in normalized_history:
        if item["role"] == "assistant":
            latest_assistant_message = item["content"]
        elif item["role"] == "user":
            latest_user_message = item["content"]
    if contract == "emit_plan":
        return render_prompt(
            "gateway/planning/qwen_planner_emit_plan.md",
            planner_state_json=json.dumps(state, ensure_ascii=True),
            requirement_summary=summary or "- None yet",
            conversation_history_json=json.dumps(normalized_history, ensure_ascii=True),
        )
    if contract == "emit_ready_sentence":
        return render_prompt(
            "gateway/planning/qwen_planner_ready.md",
            ready_sentence=ready_sentence(),
            planner_state_json=json.dumps(state, ensure_ascii=True),
            requirement_summary=summary or "- None yet",
            conversation_history_json=json.dumps(normalized_history, ensure_ascii=True),
        )
    return render_prompt(
        "gateway/planning/qwen_planner_question.md",
        latest_assistant_message=latest_assistant_message,
        latest_user_message=latest_user_message,
        missing_slots_json=json.dumps(list(state.get("missing_slots") or []), ensure_ascii=True),
        question_targets_json=json.dumps(list(state.get("next_question_hints") or []), ensure_ascii=True),
        planner_state_json=json.dumps(state, ensure_ascii=True),
        requirement_summary=summary or "- None yet",
        conversation_history_json=json.dumps(normalized_history, ensure_ascii=True),
    )


def build_planner_chat_prompt(system: str, messages: list[dict[str, Any]]) -> str:
    normalized_history = normalize_planner_history(messages)
    transcript_lines = [f"{item['role'].title()}: {item['content']}" for item in normalized_history]
    latest_user_message = ""
    previous_assistant_message = ""
    for item in normalized_history:
        if item["role"] == "assistant":
            previous_assistant_message = item["content"]
        elif item["role"] == "user":
            latest_user_message = item["content"]
    return render_prompt(
        "gateway/planning/planner_chat.md",
        system=system,
        previous_assistant_message=previous_assistant_message,
        latest_user_message=latest_user_message,
        transcript=chr(10).join(transcript_lines),
        conversation_history_json=json.dumps(normalized_history, ensure_ascii=True),
    )
