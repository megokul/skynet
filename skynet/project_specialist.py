from __future__ import annotations

import json
import re
from html import unescape
from typing import Any


_HTML_TAG_RE = re.compile(r"<[^>]+>")


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


def build_qwen_planner_context(system: str) -> str:
    return (
        "Planner chat behavior for Qwen Code:\n"
        "- This is an in-progress Telegram requirements conversation.\n"
        "- Treat the system instructions below as the authoritative project-planning contract.\n"
        "- Ignore the working directory, filesystem state, and any empty-workspace context.\n"
        "- Do not mention the workspace, files, or directory.\n"
        "- Do not say you are ready to assist.\n"
        "- Do not ask what the user wants to work on.\n"
        "- Return only the assistant reply text.\n"
        "- Only reply with 'I have everything I need. Send /plan to generate your project plan.' after all key requirements have been covered or the prompt explicitly tells you to finalize.\n\n"
        f"System instructions:\n{system}\n"
    )


def build_qwen_planner_prompt(messages: list[dict[str, Any]]) -> str:
    normalized_history = normalize_planner_history(messages)
    latest_user_message = ""
    latest_assistant_message = ""
    for item in normalized_history:
        if item["role"] == "assistant":
            latest_assistant_message = item["content"]
        elif item["role"] == "user":
            latest_user_message = item["content"]
    lowered_user = latest_user_message.lower()
    if "generate the full project plan now" in lowered_user or lowered_user.strip() == "/plan":
        return (
            "The user has explicitly requested the full project plan now.\n"
            "Generate the full project plan immediately using the exact format required by QWEN.md.\n"
            "Do not ask follow-up questions.\n"
            "Return only the plan text."
        )
    return (
        "Conversation status:\n"
        f"- Latest assistant message: {latest_assistant_message}\n"
        f"- Latest user message: {latest_user_message}\n"
        "- The latest user message is the current source of truth for the next reply.\n"
        "- Continue the requirements conversation without restarting it.\n"
        "- If important requirements are still missing, ask the next concise follow-up question.\n"
        "- If the current message already provides enough information to proceed, reply exactly: I have everything I need. Send /plan to generate your project plan.\n\n"
        "Write the next assistant reply only."
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
