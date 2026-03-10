from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable


QWEN_TASK_MODES = {
    "planner_chat",
    "plan_generation",
    "coding_implementation",
    "coding_validation",
}

_OUTPUT_FORMATS = {"text", "json", "stream-json"}
_APPROVAL_MODES = {"plan", "default", "auto-edit", "yolo"}
_CHANNELS = {"VSCode", "ACP", "SDK", "CI"}
_PLANNER_META_PATTERNS = (
    "ready to assist with your telegram product workflow planning",
    "what would you like to work on?",
    "what would you like to accomplish?",
    "what would you like to plan or work on?",
    "what would you like me to help you with today?",
    "what would you like to discuss or plan next?",
    "could you please share the conversation details",
    "i don't see any telegram conversation context",
    "i don't have access to the telegram chat history",
    "i'm ready to help with your telegram product workflow",
    "i'm ready to help plan your telegram product workflow",
    "i'm ready to continue our telegram requirements conversation",
    "approve the plan mode exit",
    "please approve the plan mode exit",
    "telegram product workflow planning",
)
_CODING_META_PATTERNS = (
    "what would you like to do with this project",
    "the workspace at",
    "the directory appears to be empty currently",
    "there isn't an existing python app",
    "the workspace looks empty",
    "if you want me to build it out",
    "what would you like to do with this project?",
    "what would you like to do with this project",
)


def _clean_text(value: str, *, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    if text in allowed:
        return text
    return default


def _bool_flag(value: bool) -> str:
    return "true" if bool(value) else "false"


def _normalize_session_id(session_id: str) -> str:
    text = str(session_id or "").strip()
    if not text:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, text))


def _profile_key(task_mode: str) -> str:
    mode = str(task_mode or "").strip().lower()
    if mode not in QWEN_TASK_MODES:
        raise ValueError(f"Unsupported qwen task_mode: {task_mode!r}")
    return "planner" if mode.startswith("plan") or mode == "planner_chat" else "coding"


def load_qwen_execution_policy(
    *,
    get_str: Callable[[str, str], str],
    get_bool: Callable[[str, bool], bool],
) -> dict[str, Any]:
    channel = _clean_text(
        get_str("SKYNET_QWEN_CHANNEL", "CI"),
        allowed=_CHANNELS,
        default="CI",
    )

    def _profile(prefix: str, *, output_default: str, recording_default: bool, strategy_default: str) -> dict[str, Any]:
        output_format = _clean_text(
            get_str(f"SKYNET_QWEN_{prefix}_OUTPUT_FORMAT", output_default),
            allowed=_OUTPUT_FORMATS,
            default=output_default,
        )
        approval_mode = _clean_text(
            get_str(f"SKYNET_QWEN_{prefix}_APPROVAL_MODE", "yolo"),
            allowed=_APPROVAL_MODES,
            default="yolo",
        )
        working_dir_strategy = str(
            get_str(f"SKYNET_QWEN_{prefix}_WORKING_DIR_STRATEGY", strategy_default) or strategy_default
        ).strip().lower() or strategy_default
        return {
            "model": str(get_str(f"SKYNET_QWEN_{prefix}_MODEL", "") or "").strip(),
            "output_format": output_format,
            "chat_recording": bool(
                get_bool(f"SKYNET_QWEN_{prefix}_CHAT_RECORDING", recording_default)
            ),
            "use_context_file": bool(
                get_bool(f"SKYNET_QWEN_{prefix}_USE_CONTEXT_FILE", prefix == "PLANNER")
            ),
            "approval_mode": approval_mode,
            "working_dir_strategy": working_dir_strategy,
        }

    return {
        "auth_type": str(get_str("SKYNET_QWEN_AUTH_TYPE", "qwen-oauth") or "qwen-oauth").strip() or "qwen-oauth",
        "channel": channel,
        "context_diagnostics": bool(get_bool("SKYNET_QWEN_CONTEXT_DIAGNOSTICS", True)),
        "planner": _profile(
            "PLANNER",
            output_default="json",
            recording_default=False,
            strategy_default="request_scoped",
        ),
        "coding": _profile(
            "CODING",
            output_default="json",
            recording_default=False,
            strategy_default="project",
        ),
    }


def qwen_profile_for_task_mode(policy: dict[str, Any], task_mode: str) -> dict[str, Any]:
    key = _profile_key(task_mode)
    profile = dict(policy.get(key) or {})
    profile["profile_key"] = key
    return profile


def build_qwen_command_args(
    *,
    binary: str,
    prompt: str,
    session_id: str,
    task_mode: str,
    policy: dict[str, Any],
) -> list[str]:
    profile = qwen_profile_for_task_mode(policy, task_mode)
    args = [binary]
    channel = str(policy.get("channel") or "").strip()
    if channel:
        args.extend(["--channel", channel])
    auth_type = str(policy.get("auth_type") or "").strip()
    if auth_type:
        args.extend(["--auth-type", auth_type])
    model = str(profile.get("model") or "").strip()
    if model:
        args.extend(["--model", model])
    output_format = str(profile.get("output_format") or "").strip()
    if output_format:
        args.extend(["--output-format", output_format])
    approval_mode = str(profile.get("approval_mode") or "").strip()
    if approval_mode:
        args.extend(["--approval-mode", approval_mode])
    args.append(f"--chat-recording={_bool_flag(bool(profile.get('chat_recording', False)))}")
    if session_id:
        args.extend(["--session-id", _normalize_session_id(session_id)])
    args.append(prompt)
    return args


def discover_qwen_context_paths(cwd: str, *, include_home: bool = True) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    def _append(candidate: Path) -> None:
        try:
            resolved = str(candidate.resolve())
        except OSError:
            resolved = str(candidate)
        if candidate.exists() and resolved not in seen:
            seen.add(resolved)
            paths.append(resolved)

    target = Path(str(cwd or "").strip())
    try:
        target = target.resolve()
    except OSError:
        pass

    if str(target).strip():
        for base in (target, *target.parents):
            _append(base / "QWEN.md")
            _append(base / ".qwen" / "settings.json")
    if include_home:
        home = Path.home() / ".qwen"
        _append(home / "QWEN.md")
        _append(home / "settings.json")
    return paths


def parse_qwen_json_output(stdout: str) -> dict[str, Any]:
    text = str(stdout or "").strip()
    if not text:
        raise ValueError("empty qwen output")
    payload = json.loads(text)
    events = payload if isinstance(payload, list) else [payload]

    assistant_text = ""
    session_id = ""
    model = ""
    tool_use_names: list[str] = []
    init_tools: list[str] = []
    result_text = ""

    for event in events:
        if not isinstance(event, dict):
            continue
        if not session_id:
            session_id = str(event.get("session_id") or event.get("sessionId") or "").strip()
        if not model:
            model = str(event.get("model") or "").strip()
        if event.get("type") == "system" and event.get("subtype") == "init":
            init_tools = [str(item).strip() for item in list(event.get("tools") or []) if str(item).strip()]
            model = model or str(event.get("model") or "").strip()
            session_id = session_id or str(event.get("session_id") or "").strip()
            continue
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            model = model or str(event.get("model") or message.get("model") or "").strip()
            content = list(message.get("content") or [])
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").strip().lower()
                if part_type == "text":
                    chunk = str(part.get("text") or "").strip()
                    if chunk:
                        assistant_text = chunk
                elif part_type == "tool_use":
                    name = str(part.get("name") or "").strip()
                    if name:
                        tool_use_names.append(name)
            continue
        if event.get("type") == "result":
            result_text = str(event.get("result") or "").strip() or result_text
            session_id = session_id or str(event.get("session_id") or "").strip()

    final_text = result_text or assistant_text
    return {
        "assistant_text": final_text,
        "session_id": session_id,
        "model": model,
        "tool_use_names": tool_use_names,
        "init_tools": init_tools,
        "raw_result_tail": text[-2000:],
    }


def classify_qwen_output_contract(
    *,
    task_mode: str,
    assistant_text: str,
    tool_use_names: list[str] | None = None,
) -> str:
    mode = str(task_mode or "").strip().lower()
    text = str(assistant_text or "").strip()
    if not text:
        return "missing_assistant_text"
    lowered = text.lower()
    if mode == "planner_chat":
        if tool_use_names:
            return "planner_tool_use"
        if any(pattern in lowered for pattern in _PLANNER_META_PATTERNS):
            return "planner_meta_output"
    elif mode in {"coding_implementation", "coding_validation"}:
        if any(pattern in lowered for pattern in _CODING_META_PATTERNS):
            return "coding_meta_output"
    return "ok"
