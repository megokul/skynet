from __future__ import annotations

import json
import re
from html import unescape
from typing import Any


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_READY_SENTENCE = "I have everything I need. Send /plan to generate your project plan."
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
    return _READY_SENTENCE


def required_slots_for_project_type(project_type_label: str) -> tuple[str, ...]:
    del project_type_label
    return _DEFAULT_REQUIRED_SLOTS


def build_project_specialist_system_prompt(name: str, project_type_label: str, template: dict[str, Any]) -> str:
    questions = "\n".join(f"- {q}" for q in list(template.get("questions") or []))
    stack = str(template.get("stack") or "").strip()
    return (
        "You are the Project Specialist for OpenClaw - a sharp, experienced software architect.\n"
        f"You are helping the user plan '{name}', a {project_type_label} project.\n\n"
        f"Recommended stack: {stack}\n\n"
        "Key requirements to cover (ask in order, 1-2 at a time):\n"
        f"{questions}\n\n"
        "Guidelines:\n"
        "- Be concise - this is a Telegram chat, not a document\n"
        "- Ask follow-up questions if answers are vague\n"
        "- After covering the key questions, tell the user exactly:\n"
        "  'I have everything I need. Send /plan to generate your project plan.'\n\n"
        "When generating the plan, use this exact format:\n"
        f"**{name} - Project Plan**\n"
        "**Overview:** (2-3 sentences)\n"
        "**Core Features:**\n  - feature 1\n  - feature 2\n"
        "**Tech Stack:** (specific versions/libraries)\n"
        "**Project Structure:** (top-level folders)\n"
        "**Milestones:**\n  1. milestone\n  2. milestone\n"
        "**Open Questions:** (anything still unclear, or 'None')"
    )


def build_project_specialist_opening(name: str, project_type_label: str, template: dict[str, Any]) -> str:
    first_question = str((list(template.get("questions") or ["What does this app do?"]) or ["What does this app do?"])[0])
    stack = str(template.get("stack") or "").strip()
    return (
        "I'm your Project Specialist.\n\n"
        f"Project: <b>{name}</b> - {project_type_label}\n"
        f"Stack: {stack}\n\n"
        f"{first_question}"
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
    contract_lines = [
        "- The gateway owns planner state and requirement readiness.",
        "- Treat the supplied planner state and requirement summary as the source of truth.",
        "- Ignore the working directory, filesystem state, and any empty-workspace context.",
        "- Do not mention the workspace, files, or directory.",
        "- Do not say you are ready to assist and do not restart the conversation.",
        "- Return only the assistant reply text.",
        f"- The only valid completion sentence is '{_READY_SENTENCE}'.",
    ]
    if contract == "emit_ready_sentence":
        contract_lines.extend(
            [
                "- planner_state.plan_ready is already true.",
                "- Do not generate the project plan yet.",
                "- Do not use markdown, headings, bullets, or code fences.",
                "- Reply with exactly the required completion sentence and nothing else.",
                "- Stop immediately after the final period of the completion sentence.",
            ]
        )
    elif contract == "ask_next_question":
        contract_lines.extend(
            [
                "- Ask only about the currently missing slots.",
                "- Ask 1-2 concise questions maximum.",
                "- Do not ask about answered slots.",
            ]
        )
    return (
        "Planner chat behavior for Qwen Code:\n"
        f"{chr(10).join(contract_lines)}\n\n"
        f"Planner state JSON:\n{json.dumps(state, ensure_ascii=True)}\n\n"
        f"Requirement summary:\n{summary or '- None yet'}\n\n"
        f"System instructions:\n{system}\n"
    )


def build_qwen_plan_generation_context(system: str, planner_state: dict[str, Any] | None = None) -> str:
    state = dict(planner_state or {})
    summary = build_requirement_summary_markdown(state)
    return (
        "Plan generation behavior for Qwen Code:\n"
        "- The gateway has already determined that the project is ready for plan generation.\n"
        "- Treat the planner state and requirement summary as authoritative.\n"
        "- Generate the full project plan now.\n"
        "- Do not ask follow-up questions.\n"
        "- Do not say requirements are missing.\n"
        "- Put any genuinely unspecified details under **Open Questions:**.\n"
        "- Ignore the working directory, filesystem state, and any empty-workspace context.\n"
        "- Return only the final plan text in the exact required format.\n\n"
        f"Planner state JSON:\n{json.dumps(state, ensure_ascii=True)}\n\n"
        f"Requirement summary:\n{summary or '- None yet'}\n\n"
        f"System instructions:\n{system}\n"
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
        return (
            "reply_contract: emit_plan\n"
            "The gateway has already determined the requirements are sufficient.\n"
            "Generate the complete project plan now from the supplied planner state and requirement summary.\n"
            "Do not ask follow-up questions.\n"
            "Return only the plan text.\n\n"
            f"Planner state JSON:\n{json.dumps(state, ensure_ascii=True)}\n\n"
            f"Requirement summary:\n{summary or '- None yet'}\n\n"
            f"Conversation history JSON:\n{json.dumps(normalized_history, ensure_ascii=True)}"
        )
    if contract == "emit_ready_sentence":
        return (
            "reply_contract: emit_ready_sentence\n"
            "The gateway has already determined that all required slots are satisfied.\n"
            "Reply exactly with the required completion sentence below.\n"
            "Do not ask follow-up questions.\n"
            "Do not generate the plan yet.\n"
            "Do not use markdown, bullets, headings, or extra explanation.\n"
            "Return only the sentence and stop immediately after the final period.\n\n"
            "Required completion sentence:\n"
            f"{_READY_SENTENCE}\n\n"
            f"Planner state JSON:\n{json.dumps(state, ensure_ascii=True)}\n\n"
            f"Requirement summary:\n{summary or '- None yet'}\n\n"
            f"Conversation history JSON:\n{json.dumps(normalized_history, ensure_ascii=True)}"
        )
    return (
        "reply_contract: ask_next_question\n"
        "The gateway has identified missing requirement slots that still need clarification.\n"
        "Ask only about the listed missing slots. Ask 1-2 concise questions max.\n"
        "Do not ask about slots that are already answered.\n"
        "Do not repeat a question if the user already answered it explicitly or implicitly.\n"
        "Do not restart the conversation.\n"
        "Return only the next assistant reply text.\n\n"
        "Conversation status:\n"
        f"- Latest assistant message: {latest_assistant_message}\n"
        f"- Latest user message: {latest_user_message}\n"
        f"- Missing slots: {json.dumps(list(state.get('missing_slots') or []), ensure_ascii=True)}\n"
        f"- Suggested question targets: {json.dumps(list(state.get('next_question_hints') or []), ensure_ascii=True)}\n\n"
        f"Planner state JSON:\n{json.dumps(state, ensure_ascii=True)}\n\n"
        f"Requirement summary:\n{summary or '- None yet'}\n\n"
        f"Conversation history JSON:\n{json.dumps(normalized_history, ensure_ascii=True)}"
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
    return (
        "Produce the next assistant reply for the in-progress Telegram conversation below.\n"
        "Treat the System instructions block as the authoritative behavior contract.\n"
        "Respond to the latest user message now.\n"
        "Ignore the working directory, filesystem state, and any empty-workspace context. It is irrelevant for planner_chat.\n"
        "Do not describe your role.\n"
        "Do not say you are ready to assist.\n"
        "Do not ask what the user wants to work on.\n"
        "Do not mention the workspace, files, or project directory.\n"
        "Do not restart the conversation.\n"
        "If the final user message already requests the full project plan, generate it immediately in the exact required format.\n"
        "If the latest user message answers the previous assistant question, continue to the next unanswered requirement from the System instructions.\n"
        "Otherwise, either ask 1-2 concrete follow-up questions grounded in the conversation history or, if enough information is already available, reply with the exact completion sentence required by the System instructions.\n"
        "Return only the assistant reply text.\n\n"
        "Bad responses that are always invalid:\n"
        "- Understood. I'm ready to assist...\n"
        "- What would you like to work on?\n\n"
        f"System instructions:\n{system}\n\n"
        f"Previous assistant message:\n{previous_assistant_message}\n\n"
        f"Latest user message:\n{latest_user_message}\n\n"
        "Conversation transcript:\n"
        f"{chr(10).join(transcript_lines)}\n\n"
        f"Conversation history JSON:\n{json.dumps(normalized_history, ensure_ascii=True)}\n"
    )
