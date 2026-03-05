"""
SKYNET Bot Ã¢â‚¬â€ Coding Orchestration

Handles the full coding loop after a project is saved:
  1. User taps "Start Coding"
  2. Bot asks about GitHub repo / project folder setup (buttons)
  3. User confirms Ã¢â€ â€™ background asyncio.Task starts
  4. Loop: LLM breaks plan into milestones Ã¢â€ â€™ user approves each Ã¢â€ â€™ CLAW worker executes
  5. Progress notifications after each milestone
  6. /status command shows live dashboard

Key design: _coding_loop runs as a background asyncio.Task.
Milestone approvals are signalled via asyncio.Event stored in bot_data.
"""
from __future__ import annotations

import asyncio
import contextlib
import html as html_mod
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

import config as cfg
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import (
    CB_CODING_RETRY_PREFIX,
    CB_CODING_GITHUB_SKIP,
    CB_CODING_GITHUB_YES,
    coding_github_setup,
    main_menu,
    milestone_review,
    retry_coding,
    run_project,
)
from bot.state import KEY_DB, KEY_ROUTER
from db.store import (
    create_task,
    create_task_gate_result,
    create_task_orchestration_run,
    delete_task_gate_results,
    ensure_user,
    get_project,
    list_projects,
    list_tasks,
    update_task_status,
)
from gateway import is_worker_available, send_action
from orchestration.openclaw_runner import get_openclaw_runner

logger = logging.getLogger("skynet.bot.coding")

# ---------------------------------------------------------------------------
# Coding system prompt Ã¢â‚¬â€ shared between router-based and Ollama SSH paths.
# ---------------------------------------------------------------------------
_CODING_SYSTEM_PROMPT = (
    "You are an expert coding agent. Implement the task completely.\n"
    "For EVERY file you create or modify, output it in a fenced code block.\n"
    "The opening fence MUST be the filename (not a language name).\n\n"
    "Example:\n"
    "```main.py\n"
    "print('hello')\n"
    "```\n\n"
    "Rules:\n"
    "- The opening ``` MUST be followed by the actual filename, NEVER a language like python or js.\n"
    "- Write complete, working code Ã¢â‚¬â€ no placeholders, no '...'.\n"
    "- Include every file needed (source, config, requirements, etc.).\n"
    "- Do NOT add explanations outside code blocks.\n"
    "- Name the main entry-point file after the project name given in the task.\n"
)

# Language tag Ã¢â€ â€™ file extension for fallback naming.
_LANG_EXT: dict[str, str] = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
    "typescript": ".ts", "ts": ".ts", "java": ".java", "c": ".c",
    "cpp": ".cpp", "c++": ".cpp", "go": ".go", "rust": ".rs",
    "ruby": ".rb", "bash": ".sh", "sh": ".sh", "html": ".html",
    "css": ".css", "json": ".json", "yaml": ".yaml", "yml": ".yaml",
    "toml": ".toml", "sql": ".sql",
}

_QUALITY_PROFILE_LEGACY = "legacy"
_QUALITY_PROFILE_STRICT = "strict"
_CODING_PROFILE_LEGACY = "legacy"
_CODING_PROFILE_CLAUDE_OLLAMA = "claude_ollama"
_CODING_PROFILE_CODEX_PRIMARY = "codex_primary"
_ORCHESTRATION_MODE_ACP_FIRST = "acp_first"
_RUN_CONTRACT_FILE = "skynet_run.json"
_ALLOWED_INTERPRETERS = {"python", "python3", "node"}
_DEFAULT_CODING_CHAIN = ("codex", "claude_ollama", "cline")
_VALID_CODING_STAGES = set(_DEFAULT_CODING_CHAIN)
_STAGE_AGENT_NAME = {
    "codex": "codex",
    "claude_ollama": "claude",
    "cline": "cline",
}
_STAGE_ENV_HINT = {
    "codex": "OPENCLAW_SSH_CODEX_BIN",
    "claude_ollama": "OPENCLAW_SSH_CLAUDE_BIN",
    "cline": "OPENCLAW_SSH_CLINE_BIN",
}


def _parse_code_blocks(text: str) -> list[tuple[str, str]]:
    """Parse fenced code blocks from LLM output into (filename, content) pairs."""
    pattern = re.compile(r"```([^\n`]+)\n(.*?)```", re.DOTALL)

    file_blocks: list[tuple[str, str]] = []
    lang_blocks: list[tuple[str, str]] = []
    for match in pattern.finditer(text):
        tag = match.group(1).strip()
        content = match.group(2)
        if "." in tag or "/" in tag or "\\" in tag:
            file_blocks.append((tag, content))
        else:
            lang_blocks.append((tag, content))

    # If no file-path blocks, convert language-tag blocks using fallback names.
    if not file_blocks and lang_blocks:
        for idx, (tag, content) in enumerate(lang_blocks):
            ext = _LANG_EXT.get(tag.lower(), f".{tag.lower()}")
            fallback = f"main{ext}" if idx == 0 else f"file{idx}{ext}"
            file_blocks.append((fallback, content))

    return file_blocks

# bot_data keys for inter-handler signalling
_MS_EVENT_KEY    = "ms_event_{uid}"
_MS_DECISION_KEY = "ms_decision_{uid}"
_ACTIVE_LOOP_KEY = "coding_loop_{uid}"   # stores the asyncio.Task
_STOP_REQUEST_KEY = "coding_stop_requested_{uid}"
_GITHUB_PREF_KEY = "coding_github_pref_{uid}_{pid}"
_TRACKER_STATE_KEY = "tracker_state_{uid}_{pid}"
_ACTIVE_PROJECT_KEY = "coding_active_project_{uid}"
_TRACKER_GATE_ORDER = ("infra_preflight", "run_contract", "lint", "tests", "smoke")

# User_data key set by project handler after save
_PROJECT_ID_KEY = "last_project_id"
_CODING_PID_KEY = "coding_project_id"


def _run_files_key(user_id: int, project_id: str) -> str:
    return f"run_files_{user_id}_{project_id}"


def _run_contract_key(user_id: int, project_id: str) -> str:
    return f"run_contract_{user_id}_{project_id}"


def _stop_request_key(user_id: int) -> str:
    return _STOP_REQUEST_KEY.format(uid=user_id)


def _tracker_state_key(user_id: int, project_id: str) -> str:
    return _TRACKER_STATE_KEY.format(uid=user_id, pid=project_id)


def _active_project_key(user_id: int) -> str:
    return _ACTIVE_PROJECT_KEY.format(uid=user_id)


def _tracker_enabled() -> bool:
    return bool(getattr(cfg, "TELEGRAM_TRACKER_ENABLED", True))


def _tracker_bar_width() -> int:
    width = int(getattr(cfg, "TELEGRAM_TRACKER_BAR_WIDTH", 20) or 20)
    return max(10, min(width, 40))


def _tracker_edit_interval() -> int:
    interval = int(getattr(cfg, "TELEGRAM_TRACKER_EDIT_INTERVAL_SECONDS", 3) or 3)
    return max(0, interval)


def _tracker_stale_warn_seconds() -> int:
    timeout = int(getattr(cfg, "TELEGRAM_TRACKER_STALE_WARN_SECONDS", 90) or 90)
    return max(30, timeout)


def _tracker_verbose_pipeline() -> bool:
    return bool(getattr(cfg, "TELEGRAM_TRACKER_VERBOSE_PIPELINE", True))


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _render_progress_bar(percent: int, width: int) -> str:
    clamped_percent = max(0, min(100, int(percent)))
    safe_width = max(10, min(int(width), 40))
    filled = int(round((clamped_percent / 100.0) * safe_width))
    return f"[{'#' * filled}{'-' * (safe_width - filled)}]"


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _tracker_progress_weights(*, strict_mode: bool) -> tuple[int, int]:
    # setup=10, extraction=10, execution=55, gates=20, finalization=5
    # In non-strict mode, gate budget is reallocated to execution.
    if strict_mode:
        return 55, 20
    return 75, 0


def _tracker_recompute_percent(state: dict[str, Any]) -> int:
    strict_mode = bool(state.get("strict_mode", False))
    exec_weight, gates_weight = _tracker_progress_weights(strict_mode=strict_mode)

    score = (
        10.0 * _clamp_unit(float(state.get("setup_progress", 0.0) or 0.0))
        + 10.0 * _clamp_unit(float(state.get("extraction_progress", 0.0) or 0.0))
        + float(exec_weight) * _clamp_unit(float(state.get("execution_progress", 0.0) or 0.0))
        + float(gates_weight) * _clamp_unit(float(state.get("gates_progress", 0.0) or 0.0))
        + 5.0 * _clamp_unit(float(state.get("final_progress", 0.0) or 0.0))
    )
    next_percent = max(0, min(100, int(round(score))))
    prior_percent = int(state.get("percent", 0) or 0)
    monotonic = max(prior_percent, next_percent)
    state["percent"] = monotonic
    return monotonic


def _tracker_estimate_percent_from_tasks(tasks: list[dict[str, Any]]) -> int:
    if not tasks:
        return 0
    total = len(tasks)
    done = sum(1 for t in tasks if str(t.get("status", "")).lower() == "done")
    failed = sum(1 for t in tasks if str(t.get("status", "")).lower() == "failed")
    running = sum(1 for t in tasks if str(t.get("status", "")).lower() == "running")
    progress = (done + failed + 0.5 * running) / float(total)
    return max(0, min(100, int(round(progress * 100))))


def _tracker_get_state(
    *,
    bot_data: dict[str, Any],
    user_id: int,
    project_id: str,
) -> dict[str, Any] | None:
    raw = bot_data.get(_tracker_state_key(user_id, project_id))
    if isinstance(raw, dict):
        return raw
    return None


def _tracker_get_active_state(
    *,
    bot_data: dict[str, Any],
    user_id: int,
) -> tuple[str, dict[str, Any]] | None:
    project_id = str(bot_data.get(_active_project_key(user_id)) or "").strip()
    if not project_id:
        return None
    state = _tracker_get_state(bot_data=bot_data, user_id=user_id, project_id=project_id)
    if state is None:
        return None
    return project_id, state


def _tracker_render_text(state: dict[str, Any]) -> str:
    percent = int(state.get("percent", 0) or 0)
    phase = str(state.get("phase") or "setup").replace("_", " ").title()
    detail = str(state.get("phase_detail") or "").strip()
    status = str(state.get("status") or "running").replace("_", " ").title()
    milestone_index = int(state.get("milestone_index", 0) or 0)
    milestones_total = int(state.get("milestones_total", 0) or 0)
    attempt = int(state.get("attempt", 0) or 0)
    stage = str(state.get("stage") or "").strip()
    gate = str(state.get("gate") or "").strip()
    transport = str(state.get("transport") or "unknown").strip()
    run_contract = str(state.get("run_contract_status") or "unknown").strip()
    session_id = str(state.get("session_id") or "").strip()
    runtime_mode = str(state.get("runtime_mode") or "").strip()
    queue_mode = str(state.get("queue_mode") or "").strip()
    created_monotonic = float(state.get("created_monotonic", time.monotonic()) or time.monotonic())
    now = time.monotonic()
    elapsed = _format_elapsed(now - created_monotonic)
    stale_seconds = now - float(state.get("last_signal_monotonic", created_monotonic) or created_monotonic)
    stale_notice = ""
    if status.lower() == "running" and stale_seconds >= _tracker_stale_warn_seconds():
        stale_notice = "Signal: still running (no new step yet)."

    lines = [
        f"Coding Progress {_render_progress_bar(percent, _tracker_bar_width())} {percent}%",
        f"Phase: {phase}{(' - ' + detail) if detail else ''}",
    ]
    if milestones_total > 0:
        lines.append(f"Milestone: {max(0, milestone_index)}/{milestones_total}")
    else:
        lines.append("Milestone: preparing")
    if _tracker_verbose_pipeline():
        pipeline_parts = []
        if stage:
            pipeline_parts.append(f"stage={stage}")
        if gate:
            pipeline_parts.append(f"gate={gate}")
        if session_id:
            pipeline_parts.append(f"session={session_id[:10]}")
        if runtime_mode:
            pipeline_parts.append(f"runtime={runtime_mode}")
        if queue_mode:
            pipeline_parts.append(f"queue={queue_mode}")
        pipeline_parts.append(f"transport={transport}")
        pipeline_parts.append(f"run_contract={run_contract}")
        lines.append("Pipeline: " + " | ".join(pipeline_parts))
    if attempt > 0:
        lines.append(f"Attempt: {attempt}")
    lines.append(f"Elapsed: {elapsed}")
    lines.append(f"Status: {status}")
    if stale_notice:
        lines.append(stale_notice)
    return "\n".join(lines)


async def _tracker_init_message(
    *,
    app,
    chat_id: int,
    user_id: int,
    project: dict[str, Any],
    working_dir: str,
    strict_mode: bool,
) -> None:
    if not _tracker_enabled():
        return
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        return

    now = time.monotonic()
    state: dict[str, Any] = {
        "project_id": project_id,
        "project_name": str(project.get("name") or "").strip(),
        "working_dir": working_dir,
        "strict_mode": strict_mode,
        "transport": str(getattr(cfg, "CODING_TRANSPORT", "auto") or "auto"),
        "run_contract_status": "pending" if strict_mode else "legacy",
        "session_id": "",
        "runtime_mode": str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
        "queue_mode": str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue"),
        "message_id": 0,
        "created_monotonic": now,
        "last_edit_monotonic": 0.0,
        "last_signal_monotonic": now,
        "last_rendered_text": "",
        "phase": "setup",
        "phase_detail": "Session started",
        "status": "running",
        "milestone_index": 0,
        "milestones_total": 0,
        "attempt": 0,
        "stage": "",
        "gate": "",
        "setup_progress": 0.1,
        "extraction_progress": 0.0,
        "execution_progress": 0.0,
        "gates_progress": 0.0,
        "final_progress": 0.0,
        "percent": 0,
    }
    _tracker_recompute_percent(state)
    text = _tracker_render_text(state)
    msg = await app.bot.send_message(chat_id, text)
    state["message_id"] = int(getattr(msg, "message_id", 0) or 0)
    state["last_rendered_text"] = text
    state["last_edit_monotonic"] = now
    app.bot_data[_tracker_state_key(user_id, project_id)] = state
    app.bot_data[_active_project_key(user_id)] = project_id
    logger.info(
        "telegram.tracker.init project_id=%s task_id=%s phase=%s percent=%s status=%s",
        project_id,
        None,
        state.get("phase"),
        state.get("percent"),
        state.get("status"),
    )


async def _tracker_update(
    *,
    app,
    chat_id: int,
    user_id: int,
    project_id: str | None = None,
    phase: str | None = None,
    phase_detail: str | None = None,
    status: str | None = None,
    milestone_index: int | None = None,
    milestones_total: int | None = None,
    attempt: int | None = None,
    stage: str | None = None,
    gate: str | None = None,
    run_contract_status: str | None = None,
    session_id: str | None = None,
    runtime_mode: str | None = None,
    queue_mode: str | None = None,
    setup_progress: float | None = None,
    extraction_progress: float | None = None,
    execution_progress: float | None = None,
    gates_progress: float | None = None,
    final_progress: float | None = None,
    heartbeat_elapsed: int | None = None,
    force: bool = False,
) -> None:
    if not _tracker_enabled():
        return

    pid = str(project_id or app.bot_data.get(_active_project_key(user_id)) or "").strip()
    if not pid:
        return
    state = _tracker_get_state(bot_data=app.bot_data, user_id=user_id, project_id=pid)
    if state is None:
        return

    now = time.monotonic()
    if phase is not None:
        state["phase"] = str(phase).strip() or state.get("phase", "setup")
    if phase_detail is not None:
        state["phase_detail"] = str(phase_detail).strip()
    if status is not None:
        state["status"] = str(status).strip() or state.get("status", "running")
    if milestone_index is not None:
        state["milestone_index"] = max(0, int(milestone_index))
    if milestones_total is not None:
        state["milestones_total"] = max(0, int(milestones_total))
    if attempt is not None:
        state["attempt"] = max(0, int(attempt))
    if stage is not None:
        state["stage"] = str(stage).strip()
    if gate is not None:
        state["gate"] = str(gate).strip()
    if run_contract_status is not None:
        state["run_contract_status"] = str(run_contract_status).strip() or state.get(
            "run_contract_status", "unknown"
        )
    if session_id is not None:
        state["session_id"] = str(session_id).strip()
    if runtime_mode is not None:
        state["runtime_mode"] = str(runtime_mode).strip()
    if queue_mode is not None:
        state["queue_mode"] = str(queue_mode).strip()
    if heartbeat_elapsed is not None:
        state["last_signal_monotonic"] = now
        if not phase_detail:
            state["phase_detail"] = f"{state.get('phase_detail', '').strip()} ({heartbeat_elapsed}s elapsed)".strip()
    elif phase is not None or phase_detail is not None or stage is not None or gate is not None:
        state["last_signal_monotonic"] = now

    progress_updates = (
        ("setup_progress", setup_progress),
        ("extraction_progress", extraction_progress),
        ("execution_progress", execution_progress),
        ("gates_progress", gates_progress),
        ("final_progress", final_progress),
    )
    for key, raw in progress_updates:
        if raw is None:
            continue
        state[key] = max(float(state.get(key, 0.0) or 0.0), _clamp_unit(float(raw)))

    _tracker_recompute_percent(state)
    text = _tracker_render_text(state)
    if text == str(state.get("last_rendered_text") or "") and not force:
        return

    edit_interval = _tracker_edit_interval()
    since_last_edit = now - float(state.get("last_edit_monotonic", 0.0) or 0.0)
    if not force and edit_interval > 0 and since_last_edit < edit_interval:
        return

    message_id = int(state.get("message_id", 0) or 0)
    if message_id <= 0:
        return

    try:
        await app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )
    except Exception as exc:  # pragma: no cover - network behavior
        err_text = str(exc).lower()
        if "message is not modified" in err_text:
            state["last_rendered_text"] = text
            state["last_edit_monotonic"] = now
            return
        if "message to edit not found" in err_text or "message can't be edited" in err_text:
            try:
                replacement = await app.bot.send_message(chat_id, text)
                state["message_id"] = int(getattr(replacement, "message_id", 0) or 0)
                logger.info(
                    "telegram.tracker.replace_message project_id=%s task_id=%s phase=%s percent=%s status=%s",
                    pid,
                    None,
                    state.get("phase"),
                    state.get("percent"),
                    state.get("status"),
                )
            except Exception as send_exc:  # pragma: no cover - network behavior
                logger.warning(
                    "telegram.tracker.error project_id=%s task_id=%s stage=replace error_excerpt=%s",
                    pid,
                    None,
                    str(send_exc)[:220],
                )
                return
        else:
            logger.warning(
                "telegram.tracker.error project_id=%s task_id=%s stage=edit error_excerpt=%s",
                pid,
                None,
                str(exc)[:220],
            )
            return

    state["last_rendered_text"] = text
    state["last_edit_monotonic"] = now
    logger.info(
        "telegram.tracker.update project_id=%s task_id=%s phase=%s percent=%s status=%s stage=%s gate=%s",
        pid,
        None,
        state.get("phase"),
        state.get("percent"),
        state.get("status"),
        state.get("stage"),
        state.get("gate"),
    )


async def _tracker_finalize(
    *,
    app,
    chat_id: int,
    user_id: int,
    project_id: str,
    status: str,
    detail: str,
) -> None:
    await _tracker_update(
        app=app,
        chat_id=chat_id,
        user_id=user_id,
        project_id=project_id,
        phase="finalization",
        phase_detail=detail,
        status=status,
        final_progress=1.0,
        stage="",
        gate="",
        force=True,
    )
    logger.info(
        "telegram.tracker.final project_id=%s task_id=%s phase=%s percent=%s status=%s",
        project_id,
        None,
        "finalization",
        (_tracker_get_state(bot_data=app.bot_data, user_id=user_id, project_id=project_id) or {}).get("percent", 0),
        status,
    )


def _quality_profile(project: dict[str, Any] | None) -> str:
    raw = str((project or {}).get("quality_profile") or _QUALITY_PROFILE_LEGACY).strip().lower()
    if raw not in {_QUALITY_PROFILE_LEGACY, _QUALITY_PROFILE_STRICT}:
        return _QUALITY_PROFILE_LEGACY
    return raw


def _coding_profile(project: dict[str, Any] | None) -> str:
    raw = str(
        (project or {}).get("coding_profile")
        or cfg.CODING_DEFAULT_PROFILE
        or _CODING_PROFILE_LEGACY
    ).strip().lower()
    if raw not in {
        _CODING_PROFILE_LEGACY,
        _CODING_PROFILE_CLAUDE_OLLAMA,
        _CODING_PROFILE_CODEX_PRIMARY,
    }:
        return _CODING_PROFILE_LEGACY
    return raw


def _effective_coding_profile(project: dict[str, Any] | None) -> str:
    if bool(getattr(cfg, "CODING_FORCE_PRIMARY_FOR_ALL", False)):
        return _CODING_PROFILE_CODEX_PRIMARY
    return _coding_profile(project)


def _orchestration_mode() -> str:
    return str(cfg.effective_orchestration_mode() or "legacy").strip().lower()


def _use_acp_orchestration() -> bool:
    return _orchestration_mode() == _ORCHESTRATION_MODE_ACP_FIRST


def _uses_claude_ollama(project: dict[str, Any] | None) -> bool:
    return _effective_coding_profile(project) == _CODING_PROFILE_CLAUDE_OLLAMA


def _parse_coding_fallback_chain(raw: str) -> list[str]:
    seen: set[str] = set()
    parsed: list[str] = []
    for token in str(raw or "").split(","):
        stage = token.strip().lower()
        if stage == "claude":
            stage = "claude_ollama"
        if not stage or stage in seen or stage not in _VALID_CODING_STAGES:
            continue
        seen.add(stage)
        parsed.append(stage)
    if parsed:
        return parsed
    return list(_DEFAULT_CODING_CHAIN)


def _build_coding_stage_chain(
    project: dict[str, Any] | None,
    *,
    include_legacy: bool = False,
) -> list[str]:
    effective = _effective_coding_profile(project)
    if effective == _CODING_PROFILE_CODEX_PRIMARY:
        if _use_acp_orchestration():
            return _parse_coding_fallback_chain(getattr(cfg, "OPENCLAW_STAGE_CHAIN", cfg.CODING_FALLBACK_CHAIN))
        return _parse_coding_fallback_chain(cfg.CODING_FALLBACK_CHAIN)
    if effective == _CODING_PROFILE_CLAUDE_OLLAMA:
        return ["claude_ollama"]
    if include_legacy:
        return ["claude_ollama"]
    return []


def _parse_agent_availability(report: str) -> dict[str, bool]:
    availability: dict[str, bool] = {}
    for stage, agent in _STAGE_AGENT_NAME.items():
        line = _agent_status_line(report, agent)
        if not line:
            continue
        lowered = line.lower()
        if "unavailable" in lowered:
            availability[stage] = False
        elif "available" in lowered:
            availability[stage] = True
    return availability


def _stage_payload(
    *,
    stage_name: str,
    prompt: str,
    working_dir: str,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "working_dir": working_dir,
        "timeout_seconds": timeout_seconds,
    }
    if stage_name == "codex":
        payload["agent"] = "codex"
        payload["backend"] = "auto"
    elif stage_name == "claude_ollama":
        payload["agent"] = "claude"
        payload["backend"] = "ollama"
        payload["model"] = cfg.CLAUDE_OLLAMA_DEFAULT_MODEL
        payload["auto_pull_model"] = cfg.CLAUDE_OLLAMA_AUTO_PULL
    elif stage_name == "cline":
        payload["agent"] = "cline"
        payload["backend"] = "auto"
    else:
        raise ValueError(f"Unsupported coding stage: {stage_name}")
    return payload


def _is_strict_project(project: dict[str, Any] | None) -> bool:
    if not cfg.STRICT_QUALITY_GATES_ENABLED:
        return False
    return _quality_profile(project) == _QUALITY_PROFILE_STRICT


def _action_error_text(result: dict[str, Any], action: str) -> str:
    if result.get("status") == "error":
        return str(result.get("error") or f"{action} failed").strip()
    inner = result.get("result", result)
    return str(
        inner.get("stderr")
        or inner.get("stdout")
        or f"{action} failed"
    ).strip()


def _action_inner_result(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("result", result)


def _action_exit_code(result: dict[str, Any]) -> int:
    inner = _action_inner_result(result)
    return int(inner.get("returncode", inner.get("exit_code", 0)))


def _action_excerpt(result: dict[str, Any], *, limit: int = 240) -> str:
    inner = _action_inner_result(result)
    text = str(inner.get("stderr") or inner.get("stdout") or "").strip()
    if not text:
        text = "(no output)"
    return text[:limit]


async def _send_action_with_heartbeat(
    *,
    app,
    chat_id: int,
    user_id: int | None,
    action: str,
    params: dict[str, Any],
    timeout: int,
    label: str,
    max_wait_seconds: int | None = None,
    confirmed: bool = True,
    heartbeat_hook: Callable[[int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """
    Run a long worker action while periodically notifying the user the task is still active.
    """
    interval = int(getattr(cfg, "CODING_PROGRESS_HEARTBEAT_SECONDS", 30) or 30)
    if interval <= 0:
        return await send_action(
            action,
            params,
            timeout=timeout,
            confirmed=confirmed,
        )

    pending = asyncio.create_task(
        send_action(
            action,
            params,
            timeout=timeout,
            confirmed=confirmed,
        )
    )
    elapsed = 0

    try:
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(pending), timeout=interval)
            except asyncio.TimeoutError:
                elapsed += interval
                if user_id is not None and app.bot_data.get(_stop_request_key(user_id)):
                    raise RuntimeError("STOP_REQUESTED: session stop requested by user")
                if max_wait_seconds is not None and elapsed >= max_wait_seconds:
                    raise RuntimeError(
                        f"WAIT_TIMEOUT: {label} exceeded {max_wait_seconds}s"
                    )
                await app.bot.send_message(
                    chat_id,
                    f"\u23f3 Still working on {label} ({elapsed}s elapsed)...",
                )
                if heartbeat_hook is not None:
                    with contextlib.suppress(Exception):
                        await heartbeat_hook(elapsed)
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending


def _is_infra_error(message: str) -> bool:
    lower = (message or "").lower()
    infra_markers = (
        "ssh action failed",
        "no agent connected",
        "agent disconnected",
        "worker not connected",
        "connection refused",
        "timed out",
        "timeout",
        "network is unreachable",
        "transport",
        "socket",
        "authentication failed",
        "could not resolve",
    )
    return any(marker in lower for marker in infra_markers)


def _is_manifest_missing_error(message: str) -> bool:
    lower = (message or "").lower()
    markers = (
        "no such file",
        "cannot find path",
        "does not exist",
        "not found",
    )
    return any(marker in lower for marker in markers)


def _is_safe_relative_path(path: str) -> bool:
    raw = (path or "").strip()
    if not raw:
        return False
    if any(ord(ch) < 32 for ch in raw):
        return False
    norm = raw.replace("\\", "/")
    if norm.startswith("/") or norm.startswith("\\"):
        return False
    if re.match(r"^[A-Za-z]:", norm):
        return False
    parts = [part for part in norm.split("/") if part not in ("", ".")]
    if not parts:
        return False
    if any(part == ".." for part in parts):
        return False
    return True


def _normalize_manifest_path(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")


def _build_manifest_command(
    *,
    interpreter: str,
    entrypoint: str,
    args: list[str],
) -> str:
    parts = [interpreter, entrypoint, *args]
    return " ".join(parts)


def _validate_cached_run_contract(contract: Any) -> dict[str, Any] | None:
    if not isinstance(contract, dict):
        return None
    interpreter = str(contract.get("interpreter") or "").strip().lower()
    entrypoint = str(contract.get("entrypoint") or "").strip()
    command = str(contract.get("command") or "").strip()
    args = contract.get("args")
    if interpreter not in _ALLOWED_INTERPRETERS:
        return None
    if not _is_safe_relative_path(entrypoint):
        return None
    if not isinstance(args, list) or any(
        (not isinstance(token, str) or not token or any(ch.isspace() for ch in token))
        for token in args
    ):
        return None
    if not command.startswith(f"{interpreter} "):
        return None
    return {
        "interpreter": interpreter,
        "entrypoint": _normalize_manifest_path(entrypoint),
        "args": args,
        "command": command,
    }


def _has_cached_run_contract(
    *,
    bot_data: dict[str, Any],
    user_id: int,
    project_id: str,
) -> bool:
    key = _run_contract_key(user_id, project_id)
    return _validate_cached_run_contract(bot_data.get(key)) is not None


def _agent_status_line(report: str, agent: str) -> str | None:
    target = f"{agent.strip().lower()}:"
    for raw in (report or "").splitlines():
        line = raw.strip()
        if line.lower().startswith(target):
            return line
    return None


def _agent_is_explicitly_unavailable(report: str, agent: str) -> bool:
    line = _agent_status_line(report, agent)
    if not line:
        return False
    lower = line.lower()
    return "unavailable" in lower and "available" in lower


async def _preflight_coding_environment(
    *,
    project: dict[str, Any],
) -> tuple[bool, str, list[str]]:
    """
    Validate coding prerequisites before milestone execution.

    For codex-primary/claude_ollama profiles, inspect coding agent telemetry and
    ensure at least one stage from the configured chain is available.
    """
    stage_chain = _build_coding_stage_chain(project)
    if not stage_chain:
        return True, "", []

    if _use_acp_orchestration():
        runner = get_openclaw_runner()
        available_chain, unavailable = runner.available_stages(stage_chain)
        if not available_chain:
            detail = "; ".join(
                f"{stage} ({reason})"
                for stage, reason in unavailable.items()
            )[:320]
            return (
                False,
                f"No control-plane coding agents available for chain {','.join(stage_chain)}. {detail}",
                stage_chain,
            )
        if available_chain != stage_chain:
            return (
                True,
                f"Filtered unavailable control-plane stages. Active chain: {','.join(available_chain)}.",
                available_chain,
            )
        return True, "", available_chain

    try:
        result = await send_action(
            "check_coding_agents",
            {},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        return False, f"Preflight check failed: {type(exc).__name__}: {exc}", stage_chain

    if result.get("status") == "error":
        return False, _action_error_text(result, "check_coding_agents"), stage_chain

    report = str(_action_inner_result(result).get("stdout") or "")
    if not report.strip():
        return (
            True,
            "Agent telemetry unavailable; proceeding without preflight enforcement.",
            stage_chain,
        )

    availability = _parse_agent_availability(report)
    any_known = bool(availability)
    any_available = any(availability.get(stage) is True for stage in stage_chain)
    first_stage = stage_chain[0]
    first_known = availability.get(first_stage)
    filtered_chain = [stage for stage in stage_chain if availability.get(stage) is not False]
    if not filtered_chain:
        filtered_chain = stage_chain

    if any_known and not any_available:
        detail_parts: list[str] = []
        for stage in stage_chain:
            agent = _STAGE_AGENT_NAME.get(stage, stage)
            line = _agent_status_line(report, agent) or f"{agent}: unavailable"
            env_hint = _STAGE_ENV_HINT.get(stage, "")
            hint = f" ({env_hint})" if env_hint else ""
            detail_parts.append(f"{line}{hint}")
        detail = "; ".join(detail_parts)[:320]
        return (
            False,
            f"No coding agents available for chain {','.join(stage_chain)}. {detail}",
            stage_chain,
        )

    if first_known is False and any_available:
        fallback_stage = next(
            (stage for stage in stage_chain[1:] if availability.get(stage) is True),
            "",
        )
        if fallback_stage:
            return (
                True,
                f"Primary stage {first_stage} unavailable; continuing with fallback {fallback_stage}.",
                filtered_chain,
            )

    if any_known and filtered_chain != stage_chain:
        return (
            True,
            f"Filtered unavailable coding stages. Active chain: {','.join(filtered_chain)}.",
            filtered_chain,
        )

    return True, "", filtered_chain


async def _record_gate_result(
    *,
    db,
    task_id: int,
    attempt: int,
    gate_name: str,
    status: str,
    command: str = "",
    summary: str = "",
) -> None:
    await create_task_gate_result(
        db,
        task_id=task_id,
        attempt=attempt,
        gate_name=gate_name,
        status=status,
        command=command,
        summary=summary[:500],
    )


def _runtime_from_contract(contract: dict[str, Any]) -> str:
    interpreter = str(contract.get("interpreter") or "").strip().lower()
    if interpreter in {"python", "python3"}:
        return "python"
    return "node"


def _has_detected_tests(*, runtime: str, files: list[str]) -> bool:
    for path in files:
        lower = _normalize_slashes(path).lower()
        base = lower.rsplit("/", 1)[-1]
        if runtime == "python":
            if ("/tests/" in f"/{lower}") and lower.endswith(".py"):
                return True
            if base.startswith("test_") and base.endswith(".py"):
                return True
            if base.endswith("_test.py"):
                return True
        else:
            if "/tests/" in f"/{lower}" and lower.endswith((".js", ".ts", ".mjs", ".cjs")):
                return True
            if base.endswith((".test.js", ".spec.js", ".test.ts", ".spec.ts")):
                return True
    return False


async def _list_project_files(
    *,
    working_dir: str,
) -> tuple[list[str], str, str, bool]:
    command = f"list_directory --recursive {working_dir}"
    try:
        list_result = await send_action(
            "list_directory",
            {"directory": working_dir, "recursive": True},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return [], command, message, True

    if list_result.get("status") == "error":
        message = _action_error_text(list_result, "list_directory")
        return [], command, message, _is_infra_error(message)

    if _action_exit_code(list_result) != 0:
        message = _action_excerpt(list_result)
        return [], command, message, _is_infra_error(message)

    listing = str(_action_inner_result(list_result).get("stdout") or "")
    files = _extract_file_paths_from_listing(listing, working_dir=working_dir)
    return files, command, "", False


async def _load_and_validate_run_contract(
    *,
    working_dir: str,
) -> tuple[dict[str, Any] | None, str, bool]:
    manifest_path = f"{working_dir}/{_RUN_CONTRACT_FILE}"
    try:
        manifest_result = await send_action(
            "file_read",
            {"file": manifest_path},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return None, message, True

    if manifest_result.get("status") == "error":
        message = _action_error_text(manifest_result, "file_read")
        return None, message, _is_infra_error(message)

    if _action_exit_code(manifest_result) != 0:
        message = _action_excerpt(manifest_result)
        infra = _is_infra_error(message) and not _is_manifest_missing_error(message)
        if not infra and _is_manifest_missing_error(message):
            synthesized, synth_summary, synth_infra = await _synthesize_run_contract(
                working_dir=working_dir
            )
            if synthesized is not None:
                return synthesized, synth_summary, False
            return None, synth_summary or message, synth_infra
        return None, message, infra

    manifest_raw = str(_action_inner_result(manifest_result).get("stdout") or "")
    try:
        payload = json.loads(manifest_raw)
    except Exception as exc:
        return None, f"Invalid {_RUN_CONTRACT_FILE}: {exc}", False

    if not isinstance(payload, dict):
        return None, f"Invalid {_RUN_CONTRACT_FILE}: expected a JSON object", False

    interpreter = str(payload.get("interpreter") or "").strip().lower()
    if interpreter not in _ALLOWED_INTERPRETERS:
        return None, "run_contract.interpreter must be python, python3, or node", False

    entrypoint_raw = str(payload.get("entrypoint") or "").strip()
    if not _is_safe_relative_path(entrypoint_raw):
        return None, "run_contract.entrypoint must be a safe relative path", False
    entrypoint = _normalize_manifest_path(entrypoint_raw)

    args = payload.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list):
        return None, "run_contract.args must be an array when provided", False
    clean_args: list[str] = []
    for token in args:
        if not isinstance(token, str):
            return None, "run_contract.args must contain only strings", False
        token = token.strip()
        if not token:
            return None, "run_contract.args cannot contain empty tokens", False
        if any(ch.isspace() for ch in token):
            return None, "run_contract.args tokens cannot include whitespace", False
        clean_args.append(token)

    entrypoint_path = f"{working_dir}/{entrypoint}"
    try:
        entry_result = await send_action(
            "file_read",
            {"file": entrypoint_path},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return None, message, True

    if entry_result.get("status") == "error":
        message = _action_error_text(entry_result, "file_read")
        return None, message, _is_infra_error(message)

    if _action_exit_code(entry_result) != 0:
        return None, f"Entrypoint file not found: {entrypoint}", False

    contract = {
        "interpreter": interpreter,
        "entrypoint": entrypoint,
        "args": clean_args,
        "command": _build_manifest_command(
            interpreter=interpreter,
            entrypoint=entrypoint,
            args=clean_args,
        ),
    }
    return contract, "run contract validated", False


async def _synthesize_run_contract(
    *,
    working_dir: str,
) -> tuple[dict[str, Any] | None, str, bool]:
    files, list_cmd, list_error, list_infra = await _list_project_files(
        working_dir=working_dir
    )
    if list_infra:
        return None, list_error or list_cmd, True
    if list_error:
        return None, list_error, False
    if not files:
        return None, "run_contract missing and no project files found to infer entrypoint", False

    normalized_root = _normalize_slashes(working_dir).rstrip("/")
    slug = normalized_root.rsplit("/", 1)[-1] if "/" in normalized_root else normalized_root
    selection = _select_entrypoint(
        files=files,
        slug=slug,
        project_type="Other",
    )
    if not selection:
        return None, "run_contract missing and no runnable python/js entrypoint could be inferred", False

    run_cmd, target = selection
    interpreter = run_cmd.split(" ", 1)[0].strip().lower()
    if interpreter not in _ALLOWED_INTERPRETERS:
        return None, f"run_contract synthesis chose unsupported interpreter: {interpreter}", False

    payload = {
        "interpreter": interpreter,
        "entrypoint": target,
        "args": [],
    }
    manifest_content = json.dumps(payload, indent=2) + "\n"
    manifest_path = f"{working_dir}/{_RUN_CONTRACT_FILE}"
    try:
        write_result = await send_action(
            "file_write",
            {"file": manifest_path, "content": manifest_content},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        return None, f"Failed to write {_RUN_CONTRACT_FILE}: {type(exc).__name__}: {exc}", True

    if write_result.get("status") == "error":
        message = _action_error_text(write_result, "file_write")
        return None, f"Failed to write {_RUN_CONTRACT_FILE}: {message}", _is_infra_error(message)
    if _action_exit_code(write_result) != 0:
        message = _action_excerpt(write_result)
        return None, f"Failed to write {_RUN_CONTRACT_FILE}: {message}", _is_infra_error(message)

    contract = {
        "interpreter": interpreter,
        "entrypoint": target,
        "args": [],
        "command": _build_manifest_command(
            interpreter=interpreter,
            entrypoint=target,
            args=[],
        ),
    }
    return contract, "run contract synthesized from detected entrypoint", False


def _python_smoke_test_content(*, run_contract: dict[str, Any]) -> str:
    entrypoint = _normalize_manifest_path(str(run_contract.get("entrypoint") or ""))
    args = [str(token) for token in (run_contract.get("args") or [])]
    args_literal = json.dumps(args, ensure_ascii=True)
    return (
        "from __future__ import annotations\n\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n\n"
        f"ENTRYPOINT = {entrypoint!r}\n"
        f"ARGS = {args_literal}\n\n"
        "def test_smoke_entrypoint_exits_zero() -> None:\n"
        "    root = Path(__file__).resolve().parents[1]\n"
        "    script = root / ENTRYPOINT\n"
        "    cmd = [sys.executable, str(script), *ARGS]\n"
        "    result = subprocess.run(\n"
        "        cmd,\n"
        "        cwd=root,\n"
        "        capture_output=True,\n"
        "        text=True,\n"
        "    )\n"
        "    assert result.returncode == 0, result.stderr or result.stdout\n"
    )


async def _bootstrap_required_tests(
    *,
    working_dir: str,
    runtime: str,
    run_contract: dict[str, Any],
) -> tuple[bool, str, bool, str]:
    """
    Create deterministic smoke tests when strict mode requires tests but none exist.
    """
    if runtime != "python":
        return False, "No tests detected; strict mode requires tests.", False, ""

    test_rel_path = "tests/test_smoke.py"
    test_file = f"{working_dir}/{test_rel_path}"
    content = _python_smoke_test_content(run_contract=run_contract)
    try:
        write_result = await send_action(
            "file_write",
            {"file": test_file, "content": content},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return False, f"Failed to create {test_rel_path}: {message}", True, ""

    if write_result.get("status") == "error":
        message = _action_error_text(write_result, "file_write")
        return (
            False,
            f"Failed to create {test_rel_path}: {message}",
            _is_infra_error(message),
            "",
        )
    if _action_exit_code(write_result) != 0:
        message = _action_excerpt(write_result)
        return (
            False,
            f"Failed to create {test_rel_path}: {message}",
            _is_infra_error(message),
            "",
        )
    return True, f"Auto-created {test_rel_path} for strict test gate.", False, test_rel_path


def _normalize_written_files(raw_files: Any) -> list[str]:
    if not isinstance(raw_files, list):
        return []
    clean: list[str] = []
    for path in raw_files:
        value = str(path).strip()
        if value:
            clean.append(value)
    return clean


def _acp_stage_name(stage_name: str) -> str:
    if stage_name == "claude_ollama":
        return "claude"
    return stage_name


def _acp_backend_name(stage_name: str) -> str:
    if stage_name == "claude_ollama":
        return "ollama"
    return "native"


async def _record_orchestration_event(
    *,
    db,
    task_id: int | None,
    phase: str,
    stage: str,
    session_id: str,
    status: str,
    summary: str,
    queue_mode: str = "",
) -> None:
    if db is None:
        return
    with contextlib.suppress(Exception):
        await create_task_orchestration_run(
            db,
            task_id=task_id,
            phase=phase,
            stage=stage,
            session_id=session_id,
            runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
            queue_mode=queue_mode or str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue")),
            status=status,
            summary=summary[:500],
        )


async def _write_generated_blocks_to_worker(
    *,
    working_dir: str,
    generated_output: str,
) -> tuple[list[str], str]:
    blocks = _parse_code_blocks(generated_output or "")
    if not blocks:
        return [], "No file code blocks found in orchestration output."

    written: list[str] = []
    errors: list[str] = []
    for filename, content in blocks:
        relative = _normalize_manifest_path(str(filename or "").strip())
        if not _is_safe_relative_path(relative):
            errors.append(f"unsafe path skipped: {filename}")
            continue
        result = await send_action(
            "file_write",
            {"file": f"{working_dir}/{relative}", "content": content},
            timeout=30,
            confirmed=True,
        )
        if result.get("status") == "error" or _action_exit_code(result) != 0:
            errors.append(f"{relative}: {_action_error_text(result, 'file_write')[:180]}")
            continue
        written.append(relative)
    return written, "; ".join(errors[:4])


async def _run_stage_chain_for_generation(
    *,
    db,
    app,
    chat_id: int,
    user_id: int | None,
    project: dict[str, Any],
    task_id: int | None,
    prompt: str,
    working_dir: str,
    stage_chain: list[str],
    label_prefix: str,
    timeout_seconds: int = 1800,
    require_runnable_files: bool = True,
    notify_stage_switch: bool = True,
    tracker_hook: Callable[..., Awaitable[None]] | None = None,
) -> dict[str, Any]:
    attempted_stages: list[str] = []
    stage_failures: list[dict[str, str]] = []

    if not stage_chain:
        return {
            "ok": False,
            "inner": {},
            "stage_name": "",
            "attempted_stages": attempted_stages,
            "stage_failures": stage_failures,
        }

    for idx, stage_name in enumerate(stage_chain, start=1):
        attempted_stages.append(stage_name)
        use_acp = _use_acp_orchestration()
        payload: dict[str, Any] | None = None
        if not use_acp:
            payload = _stage_payload(
                stage_name=stage_name,
                prompt=prompt,
                working_dir=working_dir,
                timeout_seconds=timeout_seconds,
            )
        session_id = ""
        queue_mode = str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue")
        logger.info(
            "coding.stage.start project_id=%s task_id=%s stage=%s",
            project.get("id"),
            task_id,
            stage_name,
        )
        if use_acp:
            await _record_orchestration_event(
                db=db,
                task_id=task_id,
                phase=label_prefix,
                stage=stage_name,
                session_id="pending",
                status="started",
                summary=f"Starting orchestration stage {stage_name}",
                queue_mode=queue_mode,
            )
        if tracker_hook is not None:
            with contextlib.suppress(Exception):
                await tracker_hook(
                    event="stage_start",
                    stage=stage_name,
                    stage_index=idx,
                    stage_total=len(stage_chain),
                    runtime=(
                        str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp")
                        if use_acp
                        else ""
                    ),
                    queue_mode=(queue_mode if use_acp else ""),
                    detail=f"Running stage {stage_name} ({idx}/{len(stage_chain)})",
                )

        failure_reason = ""
        result: dict[str, Any] | None = None
        inner: dict[str, Any] = {}
        return_code = 1
        written: list[str] = []
        try:
            async def _heartbeat_tracker(elapsed: int, *, sid: str = "") -> None:
                if tracker_hook is None:
                    return
                await tracker_hook(
                    event="stage_heartbeat",
                    stage=stage_name,
                    stage_index=idx,
                    stage_total=len(stage_chain),
                    elapsed=elapsed,
                    session_id=sid,
                    runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                    queue_mode=queue_mode,
                    detail=f"Stage {stage_name} still running ({elapsed}s)",
                )

            if use_acp:
                runner = get_openclaw_runner()
                session = await runner.start_session(
                    phase=label_prefix,
                    project_id=str(project.get("id") or ""),
                    task_id=task_id,
                    stage=_acp_stage_name(stage_name),
                    runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                    queue_mode=queue_mode,
                )
                session_id = str(session.get("session_id") or "")
                if session_id:
                    await _record_orchestration_event(
                        db=db,
                        task_id=task_id,
                        phase=label_prefix,
                        stage=stage_name,
                        session_id=session_id,
                        status="running",
                        summary=f"session started ({stage_name})",
                        queue_mode=queue_mode,
                    )

                stage_prompt = (
                    "Control-plane coding mode:\n"
                    "- You cannot edit worker files directly.\n"
                    "- Return every file as fenced code blocks tagged with exact filenames.\n"
                    "- Do not add non-code explanations outside file blocks.\n\n"
                    f"{prompt}"
                )
                run_task = asyncio.create_task(
                    runner.run_prompt(
                        session_id=session_id,
                        prompt=stage_prompt,
                        timeout_seconds=timeout_seconds,
                        stage=_acp_stage_name(stage_name),
                        model=(
                            str(getattr(cfg, "CLAUDE_OLLAMA_DEFAULT_MODEL", "") or "")
                            if stage_name == "claude_ollama"
                            else ""
                        ),
                        backend=_acp_backend_name(stage_name),
                    )
                )
                heartbeat = max(
                    1,
                    int(getattr(cfg, "CODING_PROGRESS_HEARTBEAT_SECONDS", 30) or 30),
                )
                max_wait_seconds = max(
                    1,
                    int(getattr(cfg, "CODING_AGENT_MAX_WAIT_SECONDS", 900) or 900),
                )
                elapsed = 0
                run_result: dict[str, Any] | None = None
                while True:
                    try:
                        run_result = await asyncio.wait_for(asyncio.shield(run_task), timeout=heartbeat)
                        break
                    except asyncio.TimeoutError:
                        elapsed += heartbeat
                        if user_id is not None and app.bot_data.get(_stop_request_key(user_id)):
                            await runner.cancel(session_id)
                            raise RuntimeError("STOP_REQUESTED: session stop requested by user")
                        if elapsed >= max_wait_seconds:
                            await runner.cancel(session_id)
                            raise RuntimeError(
                                f"WAIT_TIMEOUT: {label_prefix} via {stage_name} exceeded {max_wait_seconds}s"
                            )
                        await app.bot.send_message(
                            chat_id,
                            f"\u23f3 Still working on {label_prefix} via {stage_name} ({elapsed}s elapsed)...",
                        )
                        await _heartbeat_tracker(elapsed, sid=session_id)

                run_result = run_result or {"returncode": 1, "stdout": "", "stderr": "unknown orchestration error"}
                generated_output = str(run_result.get("stdout") or "")
                written, write_errors = await _write_generated_blocks_to_worker(
                    working_dir=working_dir,
                    generated_output=generated_output,
                )
                stderr_text = str(run_result.get("stderr") or "").strip()
                if write_errors:
                    stderr_text = f"{stderr_text}\nFILE_WRITE_WARNINGS: {write_errors}".strip()
                inner = {
                    "returncode": int(run_result.get("returncode", 1) or 1),
                    "stdout": generated_output,
                    "stderr": stderr_text,
                    "files_written": written,
                    "session_id": session_id,
                    "runtime": str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                    "queue_mode": queue_mode,
                }
                result = {"status": "success", "result": inner}
            else:
                result = await _send_action_with_heartbeat(
                    app=app,
                    chat_id=chat_id,
                    user_id=user_id,
                    action="run_coding_agent",
                    params=payload or {},
                    timeout=timeout_seconds,
                    label=f"{label_prefix} via {stage_name} ({idx}/{len(stage_chain)})",
                    max_wait_seconds=max(
                        1,
                        int(getattr(cfg, "CODING_AGENT_MAX_WAIT_SECONDS", 900) or 900),
                    ),
                    heartbeat_hook=lambda elapsed: _heartbeat_tracker(elapsed, sid=""),
                )
        except Exception as exc:
            reason = str(exc).strip()
            if reason.startswith("STOP_REQUESTED:") or reason.startswith("WAIT_TIMEOUT:"):
                raise
            failure_reason = f"{type(exc).__name__}: {exc}"
        else:
            inner = _action_inner_result(result or {})
            return_code = int(inner.get("returncode", inner.get("exit_code", 0)) or 0)
            written = _normalize_written_files(inner.get("files_written"))
            if result.get("status") == "error":
                failure_reason = _action_error_text(result, "run_coding_agent")
            elif return_code != 0:
                failure_reason = _action_excerpt(result)
            elif require_runnable_files and not _has_runnable_written_files(written):
                failure_reason = "no runnable files generated"

        if not failure_reason:
            logger.info(
                "coding.stage.success project_id=%s task_id=%s stage=%s returncode=%s files=%s",
                project.get("id"),
                task_id,
                stage_name,
                return_code,
                len(written),
            )
            if use_acp:
                await _record_orchestration_event(
                    db=db,
                    task_id=task_id,
                    phase=label_prefix,
                    stage=stage_name,
                    session_id=session_id or str(inner.get("session_id") or ""),
                    status="passed",
                    summary=f"rc={return_code} files={len(written)}",
                    queue_mode=queue_mode,
                )
            if tracker_hook is not None:
                with contextlib.suppress(Exception):
                    await tracker_hook(
                        event="stage_success",
                        stage=stage_name,
                        stage_index=idx,
                        stage_total=len(stage_chain),
                        returncode=return_code,
                        files_written=len(written),
                        session_id=session_id or str(inner.get("session_id") or ""),
                        runtime=str(inner.get("runtime") or ""),
                        queue_mode=str(inner.get("queue_mode") or ""),
                        detail=f"Stage {stage_name} succeeded",
                    )
            return {
                "ok": True,
                "inner": inner,
                "stage_name": stage_name,
                "attempted_stages": attempted_stages,
                "stage_failures": stage_failures,
            }

        failure_excerpt = str(failure_reason).strip()[:220] or "unknown stage failure"
        logger.warning(
            "coding.stage.fail project_id=%s task_id=%s stage=%s returncode=%s error_excerpt=%s",
            project.get("id"),
            task_id,
            stage_name,
            return_code,
            failure_excerpt,
        )
        if use_acp:
            await _record_orchestration_event(
                db=db,
                task_id=task_id,
                phase=label_prefix,
                stage=stage_name,
                session_id=session_id or str(inner.get("session_id") or ""),
                status="failed",
                summary=failure_excerpt,
                queue_mode=queue_mode,
            )
        stage_failures.append(
            {
                "stage": stage_name,
                "returncode": str(return_code),
                "error_excerpt": failure_excerpt,
            }
        )
        if tracker_hook is not None:
            with contextlib.suppress(Exception):
                await tracker_hook(
                    event="stage_fail",
                    stage=stage_name,
                    stage_index=idx,
                    stage_total=len(stage_chain),
                    returncode=return_code,
                    reason=failure_excerpt,
                    session_id=session_id or str(inner.get("session_id") or ""),
                    runtime=str(inner.get("runtime") or ""),
                    queue_mode=str(inner.get("queue_mode") or ""),
                    detail=f"Stage {stage_name} failed: {failure_excerpt}",
                )
        if notify_stage_switch and idx < len(stage_chain):
            next_stage = stage_chain[idx]
            if tracker_hook is not None:
                with contextlib.suppress(Exception):
                    await tracker_hook(
                        event="stage_switch",
                        stage=stage_name,
                        next_stage=next_stage,
                        stage_index=idx,
                        stage_total=len(stage_chain),
                        reason=failure_excerpt,
                        detail=f"Switching from {stage_name} to {next_stage}",
                    )
            await app.bot.send_message(
                chat_id,
                f"\u26A0\uFE0F Stage {stage_name} failed ({failure_excerpt}). Trying {next_stage}...",
            )

    return {
        "ok": False,
        "inner": {},
        "stage_name": "",
        "attempted_stages": attempted_stages,
        "stage_failures": stage_failures,
    }


async def _run_quality_fix_pass(
    *,
    project: dict[str, Any],
    milestone_text: str,
    working_dir: str,
    failing_gates: list[dict[str, str]],
    stage_chain: list[str] | None = None,
) -> list[str]:
    failure_lines = []
    for gate in failing_gates:
        gate_name = gate.get("gate_name", "unknown")
        command = gate.get("command", "")
        summary = gate.get("summary", "")
        line = f"- {gate_name}"
        if command:
            line += f" | cmd: {command}"
        if summary:
            line += f" | error: {summary}"
        failure_lines.append(line)

    fix_prompt = (
        f"Project: {project['name']} ({project['project_type']})\n"
        f"Working directory: {working_dir}\n\n"
        f"Milestone task:\n{milestone_text}\n\n"
        "The previous implementation failed strict quality gates. "
        "Fix the code and update any needed files so all gates pass:\n"
        + "\n".join(failure_lines)
        + "\n\nRequirements:\n"
          f"- Include a valid {_RUN_CONTRACT_FILE}.\n"
          "- Add runnable tests if missing (Python: tests/test_smoke.py).\n"
          "- Ensure lint and tests pass.\n"
          "- Return complete files only."
    )

    prompt_for_payload = (
        fix_prompt
        + "\n\nExecution instructions:\n"
          "- Use coding tools to directly create or update files in the working directory.\n"
          "- Do not ask clarifying questions; implement the fixes now.\n"
          "- Print a short completion summary after file edits."
    )
    effective_stage_chain = [
        stage
        for stage in (stage_chain or _build_coding_stage_chain(project, include_legacy=True))
        if stage in _VALID_CODING_STAGES
    ]
    if not effective_stage_chain:
        raise RuntimeError("GENERATION_FAILED: no_stages")
    stage_failures: list[str] = []
    for stage_name in effective_stage_chain:
        if _use_acp_orchestration():
            runner = get_openclaw_runner()
            session = await runner.start_session(
                phase="quality_fix",
                project_id=str(project.get("id") or ""),
                task_id=None,
                stage=_acp_stage_name(stage_name),
                runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                queue_mode=str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue"),
            )
            run_result = await runner.run_prompt(
                session_id=str(session.get("session_id") or ""),
                prompt=prompt_for_payload,
                timeout_seconds=1800,
                stage=_acp_stage_name(stage_name),
                model=(
                    str(getattr(cfg, "CLAUDE_OLLAMA_DEFAULT_MODEL", "") or "")
                    if stage_name == "claude_ollama"
                    else ""
                ),
                backend=_acp_backend_name(stage_name),
            )
            return_code = int(run_result.get("returncode", 1) or 1)
            if return_code != 0:
                stage_failures.append(stage_name)
                logger.warning(
                    "coding.stage.fail project_id=%s task_id=%s stage=%s returncode=%s error_excerpt=%s",
                    project.get("id"),
                    None,
                    stage_name,
                    return_code,
                    str(run_result.get("stderr") or run_result.get("stdout") or "")[:200],
                )
                continue
            files_written, write_errors = await _write_generated_blocks_to_worker(
                working_dir=working_dir,
                generated_output=str(run_result.get("stdout") or ""),
            )
            if write_errors:
                logger.warning(
                    "quality_fix file write warnings project_id=%s stage=%s details=%s",
                    project.get("id"),
                    stage_name,
                    write_errors[:200],
                )
        else:
            payload = _stage_payload(
                stage_name=stage_name,
                prompt=prompt_for_payload,
                working_dir=working_dir,
                timeout_seconds=1800,
            )
            result = await send_action(
                "run_coding_agent",
                payload,
                timeout=1800,
                confirmed=True,
            )
            if result.get("status") == "error":
                stage_failures.append(stage_name)
                logger.warning(
                    "coding.stage.fail project_id=%s task_id=%s stage=%s returncode=1 error_excerpt=%s",
                    project.get("id"),
                    None,
                    stage_name,
                    _action_error_text(result, "run_coding_agent")[:200],
                )
                continue

            inner = _action_inner_result(result)
            return_code = int(inner.get("returncode", inner.get("exit_code", 0)) or 0)
            if return_code != 0:
                stage_failures.append(stage_name)
                logger.warning(
                    "coding.stage.fail project_id=%s task_id=%s stage=%s returncode=%s error_excerpt=%s",
                    project.get("id"),
                    None,
                    stage_name,
                    return_code,
                    _action_excerpt(result)[:200],
                )
                continue
            files_written = _normalize_written_files(inner.get("files_written"))

        logger.info(
            "coding.stage.success project_id=%s task_id=%s stage=%s returncode=%s files=%s",
            project.get("id"),
            None,
            stage_name,
            return_code,
            len(files_written),
        )
        return files_written

    tried = ",".join(stage_failures or effective_stage_chain)
    raise RuntimeError(f"GENERATION_FAILED: {tried or 'none'}")


def _extract_expected_stdout_marker(text: str) -> str:
    token_candidates = re.findall(r"\b[A-Z][A-Z0-9_]{4,}\b", text or "")
    skynet_candidates: list[str] = []
    seen: set[str] = set()
    for token in token_candidates:
        clean = token.strip()
        if not clean:
            continue
        upper = clean.upper()
        if "SKYNET" not in upper or upper in seen:
            continue
        seen.add(upper)
        skynet_candidates.append(clean)
    if skynet_candidates:
        best_token = ""
        best_score = -1
        for idx, token in enumerate(skynet_candidates):
            upper = token.upper()
            score = len(upper)
            if "LIVE" in upper:
                score += 1000
            if "E2E" in upper:
                score += 500
            if "OK" in upper:
                score += 100
            score -= idx  # Prefer earlier mentions for ties.
            if score > best_score:
                best_score = score
                best_token = token
        if best_token:
            return best_token

    patterns = (
        r"print(?: exactly)?[:\s]+['\"]?([A-Za-z0-9_\-]{5,})['\"]?",
        r"must print(?: exactly)?[:\s]+['\"]?([A-Za-z0-9_\-]{5,})['\"]?",
        r"output(?: exactly)?[:\s]+['\"]?([A-Za-z0-9_\-]{5,})['\"]?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
    return ""


def _build_strict_rescue_prompt(
    *,
    project: dict[str, Any],
    milestone_text: str,
    working_dir: str,
    entrypoint: str,
    interpreter: str,
    stdout_marker: str,
) -> str:
    tests_file = "tests/test_smoke.py" if interpreter in {"python", "python3"} else "tests/smoke.js"
    marker_req = (
        f"- The program must print exactly: {stdout_marker}\n"
        if stdout_marker
        else ""
    )
    return (
        f"Project: {project['name']} ({project['project_type']})\n"
        f"Working directory: {working_dir}\n\n"
        "STRICT RECOVERY MODE:\n"
        "Previous coding attempts exited successfully but produced no files.\n"
        "Write files now. Do not ask clarifying questions.\n\n"
        "Milestone task:\n"
        f"{milestone_text}\n\n"
        "Required outputs:\n"
        f"1) {entrypoint}\n"
        f"- Must be runnable with `{interpreter} {entrypoint}`\n"
        f"{marker_req}"
        "- Exit code must be 0.\n\n"
        f"2) {_RUN_CONTRACT_FILE} with:\n"
        "{\n"
        f'  \"interpreter\": \"{interpreter}\",\n'
        f'  \"entrypoint\": \"{entrypoint}\",\n'
        "  \"args\": []\n"
        "}\n\n"
        f"3) {tests_file}\n"
        "- Must execute the entrypoint and assert exit code 0.\n\n"
        "Output only fenced code blocks where each fence tag is the filename."
    )


def _has_runnable_written_files(paths: list[str]) -> bool:
    for path in paths:
        lower = _normalize_slashes(str(path)).lower()
        if lower.endswith(".py") or lower.endswith(".js"):
            return True
    return False


async def _write_strict_emergency_scaffold(
    *,
    working_dir: str,
    entrypoint: str,
    interpreter: str,
    stdout_marker: str,
) -> tuple[list[str], str]:
    if interpreter not in {"python", "python3"}:
        return [], "Emergency scaffold currently supports python only."

    marker = stdout_marker or "SKYNET_E2E_OK"
    app_content = (
        "from __future__ import annotations\n"
        "import sys\n\n"
        f"print({marker!r})\n"
        "sys.exit(0)\n"
    )
    manifest_content = json.dumps(
        {
            "interpreter": interpreter,
            "entrypoint": entrypoint,
            "args": [],
        },
        indent=2,
    ) + "\n"
    test_content = (
        "from __future__ import annotations\n\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n\n"
        "def test_smoke_entrypoint() -> None:\n"
        "    root = Path(__file__).resolve().parents[1]\n"
        f"    target = root / {entrypoint!r}\n"
        "    result = subprocess.run(\n"
        "        [sys.executable, str(target)],\n"
        "        cwd=root,\n"
        "        capture_output=True,\n"
        "        text=True,\n"
        "    )\n"
        "    assert result.returncode == 0, result.stderr or result.stdout\n"
        f"    assert {marker!r} in result.stdout\n"
    )

    to_write = {
        entrypoint: app_content,
        _RUN_CONTRACT_FILE: manifest_content,
        "tests/test_smoke.py": test_content,
    }
    written: list[str] = []
    for rel_path, content in to_write.items():
        file_path = f"{working_dir}/{rel_path}"
        result = await send_action(
            "file_write",
            {"file": file_path, "content": content},
            timeout=30,
            confirmed=True,
        )
        if result.get("status") == "error" or _action_exit_code(result) != 0:
            detail = _action_error_text(result, "file_write")
            raise RuntimeError(f"Emergency scaffold write failed for {rel_path}: {detail}")
        written.append(rel_path)

    return written, "STRICT_RECOVERY: wrote entrypoint, run contract, and smoke test."


async def _run_strict_quality_gates(
    *,
    db,
    task_id: int,
    project: dict[str, Any],
    milestone_text: str,
    working_dir: str,
    tracker_hook: Callable[..., Awaitable[None]] | None = None,
    stage_chain: list[str] | None = None,
) -> dict[str, Any]:
    await delete_task_gate_results(db, task_id=task_id)

    max_retries = max(0, int(cfg.STRICT_QUALITY_GATES_FIX_RETRIES))
    max_attempts = 1 + max_retries
    last_contract: dict[str, Any] | None = None

    for attempt in range(1, max_attempts + 1):
        failed_gates: list[dict[str, str]] = []
        infra_failure = False

        async def _emit_gate_event(
            gate_name: str,
            status: str,
            summary: str = "",
            command: str = "",
        ) -> None:
            if tracker_hook is None:
                return
            await tracker_hook(
                event="gate",
                gate_name=gate_name,
                status=status,
                summary=summary,
                command=command,
                attempt=attempt,
            )

        preflight_cmd = f"list_directory {working_dir}"
        with contextlib.suppress(Exception):
            await _emit_gate_event(
                "infra_preflight",
                "running",
                summary="Checking worker connectivity",
                command=preflight_cmd,
            )
        try:
            preflight = await send_action(
                "list_directory",
                {"directory": working_dir},
                timeout=15,
                confirmed=True,
            )
        except Exception as exc:
            summary = f"{type(exc).__name__}: {exc}"
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="infra_preflight",
                status="failed",
                command=preflight_cmd,
                summary=summary,
            )
            with contextlib.suppress(Exception):
                await _emit_gate_event(
                    "infra_preflight",
                    "failed",
                    summary=summary,
                    command=preflight_cmd,
                )
            return {
                "passed": False,
                "infra_failure": True,
                "error_message": f"INFRA_FAILURE: {summary[:220]}",
                "failed_gate_names": ["infra_preflight"],
                "run_contract": None,
                "fix_written_files": [],
            }

        if preflight.get("status") == "error" or _action_exit_code(preflight) != 0:
            summary = _action_error_text(preflight, "list_directory")
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="infra_preflight",
                status="failed",
                command=preflight_cmd,
                summary=summary,
            )
            with contextlib.suppress(Exception):
                await _emit_gate_event(
                    "infra_preflight",
                    "failed",
                    summary=summary,
                    command=preflight_cmd,
                )
            return {
                "passed": False,
                "infra_failure": True,
                "error_message": f"INFRA_FAILURE: {summary[:220]}",
                "failed_gate_names": ["infra_preflight"],
                "run_contract": None,
                "fix_written_files": [],
            }

        await _record_gate_result(
            db=db,
            task_id=task_id,
            attempt=attempt,
            gate_name="infra_preflight",
            status="passed",
            command=preflight_cmd,
            summary="worker connectivity OK",
        )
        with contextlib.suppress(Exception):
            await _emit_gate_event(
                "infra_preflight",
                "passed",
                summary="worker connectivity OK",
                command=preflight_cmd,
            )

        with contextlib.suppress(Exception):
            await _emit_gate_event(
                "run_contract",
                "running",
                summary=f"Validating {_RUN_CONTRACT_FILE}",
                command=f"file_read {_RUN_CONTRACT_FILE}",
            )
        run_contract, run_summary, run_infra = await _load_and_validate_run_contract(
            working_dir=working_dir,
        )
        if run_contract is None:
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="run_contract",
                status="failed",
                command=f"file_read {_RUN_CONTRACT_FILE}",
                summary=run_summary,
            )
            with contextlib.suppress(Exception):
                await _emit_gate_event(
                    "run_contract",
                    "failed",
                    summary=run_summary,
                    command=f"file_read {_RUN_CONTRACT_FILE}",
                )
            if run_infra:
                return {
                    "passed": False,
                    "infra_failure": True,
                    "error_message": f"INFRA_FAILURE: {run_summary[:220]}",
                    "failed_gate_names": ["run_contract"],
                    "run_contract": None,
                    "fix_written_files": [],
                }
            failed_gates.append(
                {
                    "gate_name": "run_contract",
                    "command": f"file_read {_RUN_CONTRACT_FILE}",
                    "summary": run_summary,
                }
            )
            last_contract = None
        else:
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="run_contract",
                status="passed",
                command=f"file_read {_RUN_CONTRACT_FILE}",
                summary=run_summary,
            )
            with contextlib.suppress(Exception):
                await _emit_gate_event(
                    "run_contract",
                    "passed",
                    summary=run_summary,
                    command=f"file_read {_RUN_CONTRACT_FILE}",
                )
            last_contract = run_contract

        if not last_contract:
            for gate_name in ("lint", "tests", "smoke"):
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name=gate_name,
                    status="skipped",
                    command="",
                    summary="Skipped because run_contract failed",
                )
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        gate_name,
                        "skipped",
                        summary="Skipped because run_contract failed",
                        command="",
                    )
        else:
            runtime = _runtime_from_contract(last_contract)

            lint_linter = "ruff" if runtime == "python" else "eslint"
            lint_cmd = (
                "python -m ruff check ." if lint_linter == "ruff" else "npx eslint ."
            )
            with contextlib.suppress(Exception):
                await _emit_gate_event(
                    "lint",
                    "running",
                    summary=f"Running {lint_linter}",
                    command=lint_cmd,
                )
            try:
                lint_result = await send_action(
                    "lint_project",
                    {"working_dir": working_dir, "linter": lint_linter},
                    timeout=120,
                    confirmed=True,
                )
            except Exception as exc:
                lint_summary = f"{type(exc).__name__}: {exc}"
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="lint",
                    status="failed",
                    command=lint_cmd,
                    summary=lint_summary,
                )
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        "lint",
                        "failed",
                        summary=lint_summary,
                        command=lint_cmd,
                    )
                infra_failure = True
                failed_gates.append(
                    {"gate_name": "lint", "command": lint_cmd, "summary": lint_summary}
                )
            else:
                lint_failed = (
                    lint_result.get("status") == "error" or _action_exit_code(lint_result) != 0
                )
                lint_summary = (
                    _action_error_text(lint_result, "lint_project")
                    if lint_failed
                    else _action_excerpt(lint_result)
                )
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="lint",
                    status="failed" if lint_failed else "passed",
                    command=lint_cmd,
                    summary=lint_summary,
                )
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        "lint",
                        "failed" if lint_failed else "passed",
                        summary=lint_summary,
                        command=lint_cmd,
                    )
                if lint_failed:
                    if _is_infra_error(lint_summary):
                        infra_failure = True
                    failed_gates.append(
                        {"gate_name": "lint", "command": lint_cmd, "summary": lint_summary}
                    )

            test_runner = "pytest" if runtime == "python" else "npm"
            tests_cmd = "python -m pytest --tb=short -q" if runtime == "python" else "npm test"
            with contextlib.suppress(Exception):
                await _emit_gate_event(
                    "tests",
                    "running",
                    summary="Discovering and running tests",
                    command=tests_cmd,
                )
            files, tests_scan_cmd, list_error, list_infra = await _list_project_files(
                working_dir=working_dir
            )
            if list_infra:
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="tests",
                    status="failed",
                    command=tests_scan_cmd,
                    summary=list_error or "Failed to list files for test discovery",
                )
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        "tests",
                        "failed",
                        summary=list_error or "Failed to list files for test discovery",
                        command=tests_scan_cmd,
                    )
                infra_failure = True
                failed_gates.append(
                    {
                        "gate_name": "tests",
                        "command": tests_scan_cmd,
                        "summary": list_error or "Failed to list files for test discovery",
                    }
                )
            elif list_error:
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="tests",
                    status="failed",
                    command=tests_scan_cmd,
                    summary=list_error,
                )
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        "tests",
                        "failed",
                        summary=list_error,
                        command=tests_scan_cmd,
                    )
                failed_gates.append(
                    {
                        "gate_name": "tests",
                        "command": tests_scan_cmd,
                        "summary": list_error,
                    }
                )
            else:
                tests_detected = _has_detected_tests(runtime=runtime, files=files)
                bootstrap_summary = ""
                if not tests_detected:
                    (
                        bootstrapped,
                        bootstrap_summary,
                        bootstrap_infra,
                        bootstrap_test_path,
                    ) = await _bootstrap_required_tests(
                        working_dir=working_dir,
                        runtime=runtime,
                        run_contract=last_contract,
                    )
                    if bootstrapped and bootstrap_test_path:
                        files = [*files, bootstrap_test_path]
                        tests_detected = _has_detected_tests(runtime=runtime, files=files)
                    elif bootstrap_infra:
                        infra_failure = True

                if not tests_detected:
                    summary = bootstrap_summary or "No tests detected; strict mode requires tests."
                    await _record_gate_result(
                        db=db,
                        task_id=task_id,
                        attempt=attempt,
                        gate_name="tests",
                        status="failed",
                        command=tests_scan_cmd,
                        summary=summary,
                    )
                    with contextlib.suppress(Exception):
                        await _emit_gate_event(
                            "tests",
                            "failed",
                            summary=summary,
                            command=tests_scan_cmd,
                        )
                    failed_gates.append(
                        {
                            "gate_name": "tests",
                            "command": tests_scan_cmd,
                            "summary": summary,
                        }
                    )
                else:
                    try:
                        tests_result = await send_action(
                            "run_tests",
                            {"working_dir": working_dir, "runner": test_runner},
                            timeout=300,
                            confirmed=True,
                        )
                    except Exception as exc:
                        tests_summary = f"{type(exc).__name__}: {exc}"
                        await _record_gate_result(
                            db=db,
                            task_id=task_id,
                            attempt=attempt,
                            gate_name="tests",
                            status="failed",
                            command=tests_cmd,
                            summary=tests_summary,
                        )
                        with contextlib.suppress(Exception):
                            await _emit_gate_event(
                                "tests",
                                "failed",
                                summary=tests_summary,
                                command=tests_cmd,
                            )
                        infra_failure = True
                        failed_gates.append(
                            {
                                "gate_name": "tests",
                                "command": tests_cmd,
                                "summary": tests_summary,
                            }
                        )
                    else:
                        tests_failed = (
                            tests_result.get("status") == "error"
                            or _action_exit_code(tests_result) != 0
                        )
                        tests_summary = (
                            _action_error_text(tests_result, "run_tests")
                            if tests_failed
                            else _action_excerpt(tests_result)
                        )
                        if bootstrap_summary:
                            tests_summary = f"{bootstrap_summary} {tests_summary}".strip()
                        await _record_gate_result(
                            db=db,
                            task_id=task_id,
                            attempt=attempt,
                            gate_name="tests",
                            status="failed" if tests_failed else "passed",
                            command=tests_cmd,
                            summary=tests_summary,
                        )
                        with contextlib.suppress(Exception):
                            await _emit_gate_event(
                                "tests",
                                "failed" if tests_failed else "passed",
                                summary=tests_summary,
                                command=tests_cmd,
                            )
                        if tests_failed:
                            if _is_infra_error(tests_summary):
                                infra_failure = True
                            failed_gates.append(
                                {
                                    "gate_name": "tests",
                                    "command": tests_cmd,
                                    "summary": tests_summary,
                                }
                            )

            smoke_cmd = str(last_contract.get("command") or "").strip()
            with contextlib.suppress(Exception):
                await _emit_gate_event(
                    "smoke",
                    "running",
                    summary="Running smoke command",
                    command=smoke_cmd,
                )
            try:
                smoke_result = await send_action(
                    "exec_command",
                    {"command": smoke_cmd, "working_dir": working_dir},
                    timeout=120,
                    confirmed=True,
                )
            except Exception as exc:
                smoke_summary = f"{type(exc).__name__}: {exc}"
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="smoke",
                    status="failed",
                    command=smoke_cmd,
                    summary=smoke_summary,
                )
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        "smoke",
                        "failed",
                        summary=smoke_summary,
                        command=smoke_cmd,
                    )
                infra_failure = True
                failed_gates.append(
                    {"gate_name": "smoke", "command": smoke_cmd, "summary": smoke_summary}
                )
            else:
                smoke_failed = (
                    smoke_result.get("status") == "error"
                    or _action_exit_code(smoke_result) != 0
                )
                smoke_summary = (
                    _action_error_text(smoke_result, "exec_command")
                    if smoke_failed
                    else _action_excerpt(smoke_result)
                )
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="smoke",
                    status="failed" if smoke_failed else "passed",
                    command=smoke_cmd,
                    summary=smoke_summary,
                )
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        "smoke",
                        "failed" if smoke_failed else "passed",
                        summary=smoke_summary,
                        command=smoke_cmd,
                    )
                if smoke_failed:
                    if _is_infra_error(smoke_summary):
                        infra_failure = True
                    failed_gates.append(
                        {
                            "gate_name": "smoke",
                            "command": smoke_cmd,
                            "summary": smoke_summary,
                        }
                    )

        if infra_failure:
            top = failed_gates[0] if failed_gates else {"summary": "infra failure"}
            return {
                "passed": False,
                "infra_failure": True,
                "error_message": f"INFRA_FAILURE: {top.get('summary', '')[:220]}",
                "failed_gate_names": [gate["gate_name"] for gate in failed_gates],
                "run_contract": None,
                "fix_written_files": [],
            }

        if not failed_gates:
            return {
                "passed": True,
                "infra_failure": False,
                "error_message": "",
                "failed_gate_names": [],
                "run_contract": last_contract,
                "pass_summary": "QUALITY_GATES_PASSED: infra_preflight,run_contract,lint,tests,smoke",
                "fix_written_files": [],
            }

        if attempt < max_attempts:
            try:
                fix_written_files = await _run_quality_fix_pass(
                    project=project,
                    milestone_text=milestone_text,
                    working_dir=working_dir,
                    failing_gates=failed_gates,
                    stage_chain=stage_chain,
                )
            except Exception as exc:
                reason = str(exc).strip() or "quality auto-fix pass failed"
                if reason.startswith("FALLBACK_UNAVAILABLE:"):
                    failed_names = [gate["gate_name"] for gate in failed_gates]
                    return {
                        "passed": False,
                        "infra_failure": False,
                        "error_message": reason,
                        "failed_gate_names": failed_names,
                        "run_contract": last_contract,
                        "fix_written_files": [],
                    }
                logger.warning(
                    "Quality auto-fix pass failed for task %s attempt %s: %s",
                    task_id,
                    attempt,
                    exc,
                )
            else:
                if fix_written_files:
                    logger.info(
                        "Quality auto-fix pass wrote %d file(s) for task %s",
                        len(fix_written_files),
                        task_id,
                    )
            continue

        failed_names = [gate["gate_name"] for gate in failed_gates]
        short = ",".join(failed_names[:3])
        return {
            "passed": False,
            "infra_failure": False,
            "error_message": f"GATES_FAILED: {short}",
            "failed_gate_names": failed_names,
            "run_contract": last_contract,
            "fix_written_files": [],
        }

    return {
        "passed": False,
        "infra_failure": False,
        "error_message": "GATES_FAILED: unknown",
        "failed_gate_names": [],
        "run_contract": None,
        "fix_written_files": [],
    }


# Ã¢â€â‚¬Ã¢â€â‚¬ Entry: Start Coding Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

async def start_coding_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped Ã°Å¸Å¡â‚¬ Start Coding Ã¢â‚¬â€ ask GitHub/folder setup preference."""
    await update.callback_query.answer()

    project_id = context.user_data.get(_PROJECT_ID_KEY)
    if not project_id:
        await update.callback_query.message.reply_text(
            "No active project found. Start a project first.",
            reply_markup=main_menu(),
        )
        return

    context.user_data[_CODING_PID_KEY] = project_id

    await update.callback_query.message.reply_text(
        "Should I set up a GitHub repo and project folder on your laptop?",
        reply_markup=coding_github_setup(),
    )


async def _start_coding_loop(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    message,
    user_id: int,
    chat_id: int,
    project: dict,
    do_github: bool,
) -> bool:
    """Start a coding session task, guarding against duplicate active loops."""
    loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
    existing = context.bot_data.get(loop_key)
    if existing and not existing.done():
        await message.reply_text("A coding session is already running for you!")
        return False

    context.bot_data.pop(f"run_project_{user_id}", None)
    context.bot_data.pop(_run_files_key(user_id, project["id"]), None)
    context.bot_data.pop(_run_contract_key(user_id, project["id"]), None)
    context.bot_data.pop(_stop_request_key(user_id), None)
    context.bot_data.pop(_tracker_state_key(user_id, project["id"]), None)
    context.bot_data.pop(_active_project_key(user_id), None)
    slug = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"

    await message.reply_text(
        "Starting coding sessionÃ¢â‚¬Â¦\n"
        f"Ã°Å¸â€œÂ Project folder: <code>{working_dir}</code>\n\n"
        "I'll send you each milestone for approval before executing. "
        "Use /status anytime to check progress.",
        parse_mode="HTML",
    )

    await _tracker_init_message(
        app=context.application,
        chat_id=chat_id,
        user_id=user_id,
        project=project,
        working_dir=working_dir,
        strict_mode=_is_strict_project(project),
    )

    task = asyncio.create_task(
        _coding_loop(context.application, chat_id, user_id, project, do_github)
    )
    context.bot_data[loop_key] = task
    return True


async def coding_github_choice_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User chose GitHub setup option Ã¢â‚¬â€ spin up the background coding loop."""
    await update.callback_query.answer()

    cb_data    = update.callback_query.data or ""
    project_id = context.user_data.pop(_CODING_PID_KEY, None)
    user_id    = update.effective_user.id
    chat_id    = update.effective_chat.id

    if not project_id:
        await update.callback_query.message.reply_text("Session expired Ã¢â‚¬â€ start over.")
        return

    db = context.bot_data.get(KEY_DB)
    project = await get_project(db, project_id)
    if not project:
        await update.callback_query.message.reply_text("Project not found in database.")
        return

    do_github = (cb_data == CB_CODING_GITHUB_YES)

    context.bot_data[_GITHUB_PREF_KEY.format(uid=user_id, pid=project_id)] = do_github

    await _start_coding_loop(
        context=context,
        message=update.callback_query.message,
        user_id=user_id,
        chat_id=chat_id,
        project=project,
        do_github=do_github,
    )


# Ã¢â€â‚¬Ã¢â€â‚¬ Milestone approval callbacks Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

async def retry_coding_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Retry coding for a saved project, reusing the previous GitHub preference when known."""
    await update.callback_query.answer()

    cb_data = update.callback_query.data or ""
    project_id = cb_data.removeprefix(CB_CODING_RETRY_PREFIX).strip()
    if not project_id:
        await update.callback_query.message.reply_text(
            "Invalid retry request.",
            reply_markup=main_menu(),
        )
        return

    db = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user
    user = await ensure_user(
        db,
        telegram_user_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name or "",
    )

    project = await get_project(db, project_id)
    if not project or int(project.get("user_id", -1)) != int(user["id"]):
        await update.callback_query.message.reply_text(
            "This retry link is invalid or you no longer have access to this project.",
            reply_markup=main_menu(),
        )
        return

    context.user_data[_PROJECT_ID_KEY] = project_id
    pref_key = _GITHUB_PREF_KEY.format(uid=tg_user.id, pid=project_id)
    remembered_pref = context.bot_data.get(pref_key)
    if isinstance(remembered_pref, bool):
        mode_label = "GitHub repo + folder setup" if remembered_pref else "folder-only setup"
        await update.callback_query.message.reply_text(
            f"Retrying with your previous preference: {mode_label}."
        )
        await _start_coding_loop(
            context=context,
            message=update.callback_query.message,
            user_id=tg_user.id,
            chat_id=update.effective_chat.id,
            project=project,
            do_github=remembered_pref,
        )
        return

    context.user_data[_CODING_PID_KEY] = project_id
    await update.callback_query.message.reply_text(
        "Should I set up a GitHub repo and project folder on your laptop?",
        reply_markup=coding_github_setup(),
    )

async def approve_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped Ã¢Å“â€¦ Run It Ã¢â‚¬â€ signal the coding loop to proceed."""
    await update.callback_query.answer("RunningÃ¢â‚¬Â¦")
    user_id  = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "approve"
        event.set()
        with contextlib.suppress(Exception):
            await _tracker_update(
                app=context.application,
                chat_id=update.effective_chat.id,
                user_id=user_id,
                phase="milestone_execution",
                phase_detail="Milestone approved; starting execution",
                status="running",
                stage="",
                gate="",
            )
    else:
        await update.callback_query.message.reply_text(
            "No active milestone waiting for approval."
        )


async def skip_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped Ã¢ÂÂ­ Skip Ã¢â‚¬â€ signal the coding loop to skip this milestone."""
    await update.callback_query.answer("SkippingÃ¢â‚¬Â¦")
    user_id   = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "skip"
        event.set()
        with contextlib.suppress(Exception):
            await _tracker_update(
                app=context.application,
                chat_id=update.effective_chat.id,
                user_id=user_id,
                phase="milestone_execution",
                phase_detail="Milestone skipped by user",
                status="running",
                stage="",
                gate="",
            )


async def stop_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped Stop Session; request graceful cancellation."""
    await update.callback_query.answer("Stopping...")
    user_id = update.effective_user.id
    context.bot_data[_stop_request_key(user_id)] = True

    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "stop"
        event.set()
        with contextlib.suppress(Exception):
            await _tracker_update(
                app=context.application,
                chat_id=update.effective_chat.id,
                user_id=user_id,
                phase="finalization",
                phase_detail="Stop requested by user",
                status="stopped",
                final_progress=1.0,
                stage="",
                gate="",
                force=True,
            )
        return

    loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
    active_loop = context.bot_data.get(loop_key)
    if active_loop and not active_loop.done():
        await update.callback_query.message.reply_text(
            "Stopping current milestone execution... this may take a few seconds."
        )
    else:
        context.bot_data.pop(_stop_request_key(user_id), None)
        await update.callback_query.message.reply_text(
            "No active coding session to stop."
        )


# Ã¢â€â‚¬Ã¢â€â‚¬ Dashboard Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

async def dashboard_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/status - show latest project progress with tracker visibility."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    db = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user

    user = await ensure_user(
        db,
        telegram_user_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name or "",
    )
    projects = await list_projects(db, user_id=user["id"])
    if not projects:
        await update.message.reply_text(
            "No projects yet. Tap Start a Project to begin.",
            reply_markup=main_menu(),
        )
        return

    project = projects[0]
    tasks = await list_tasks(db, project_id=project["id"])

    status_words = {
        "pending": "PENDING",
        "running": "RUNNING",
        "done": "DONE",
        "failed": "FAILED",
    }
    if tasks:
        task_lines = "\n".join(
            f"{status_words.get(str(t.get('status', '')).lower(), 'UNKNOWN')} {t['title']}"
            for t in tasks
        )
    else:
        task_lines = "No tasks yet - coding has not started."

    loop_key = _ACTIVE_LOOP_KEY.format(uid=tg_user.id)
    is_running = (
        loop_key in context.bot_data
        and context.bot_data[loop_key]
        and not context.bot_data[loop_key].done()
    )
    status_note = " | coding in progress" if is_running else ""

    slug = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"
    tracker_state = _tracker_get_state(
        bot_data=context.bot_data,
        user_id=tg_user.id,
        project_id=project["id"],
    )

    tracker_block = ""
    if tracker_state:
        percent = int(tracker_state.get("percent", 0) or 0)
        phase = str(tracker_state.get("phase") or "setup").replace("_", " ")
        detail = str(tracker_state.get("phase_detail") or "").strip()
        stage = str(tracker_state.get("stage") or "").strip()
        gate = str(tracker_state.get("gate") or "").strip()
        transport = str(tracker_state.get("transport") or "unknown").strip()
        run_contract = str(tracker_state.get("run_contract_status") or "unknown").strip()
        session_id = str(tracker_state.get("session_id") or "").strip()
        runtime_mode = str(tracker_state.get("runtime_mode") or "").strip()
        queue_mode = str(tracker_state.get("queue_mode") or "").strip()
        tracker_block = (
            f"\n\nProgress {_render_progress_bar(percent, _tracker_bar_width())} {percent}%\n"
            f"Phase: {phase}{(' - ' + detail) if detail else ''}"
        )
        if stage:
            tracker_block += f"\nStage: {stage}"
        if gate:
            tracker_block += f"\nGate: {gate}"
        if session_id:
            tracker_block += f"\nSession: {session_id[:12]}"
        if runtime_mode:
            tracker_block += f"\nRuntime: {runtime_mode}"
        if queue_mode:
            tracker_block += f"\nQueue: {queue_mode}"
        tracker_block += f"\nTransport: {transport}"
        tracker_block += f"\nRun contract: {run_contract}"
    elif is_running:
        estimated = _tracker_estimate_percent_from_tasks(tasks)
        tracker_block = (
            f"\n\nProgress {_render_progress_bar(estimated, _tracker_bar_width())} {estimated}%\n"
            "Phase: estimating from task states"
        )

    text = (
        f"<b>{project['name']}</b> - {project['project_type']}\n"
        f"Folder: <code>{working_dir}</code>\n"
        f"Status: {project['status']}{status_note}\n\n"
        f"{task_lines}{tracker_block}"
    )

    run_pid = context.bot_data.get(f"run_project_{tg_user.id}")
    show_run_cta = False
    if run_pid and not is_running:
        run_project_row = await get_project(db, run_pid)
        if run_project_row:
            if _is_strict_project(run_project_row):
                show_run_cta = _has_cached_run_contract(
                    bot_data=context.bot_data,
                    user_id=tg_user.id,
                    project_id=run_pid,
                )
            else:
                show_run_cta = True

    if show_run_cta:
        keyboard = run_project()
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Main Menu", callback_data="nav:main_menu")]]
        )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _coding_loop(
    app,
    chat_id: int,
    user_id: int,
    project: dict,
    do_github: bool,
) -> None:
    """
    Background task: orchestrate milestone-by-milestone project execution.

    1. (Optional) Set up GitHub repo + project folder on CLAW worker.
    2. Extract milestones from the stored plan via LLM.
    3. For each milestone:
       a. Send to user with Ã¢Å“â€¦ Run It / Ã¢ÂÂ­ Skip buttons.
       b. Wait up to 1 h for user decision.
       c. If approved: dispatch run_coding_agent to CLAW worker.
       d. Notify user of result.
    4. Send completion message.
    """
    db     = app.bot_data.get(KEY_DB)
    router = app.bot_data.get(KEY_ROUTER)
    slug   = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"
    strict_mode = _is_strict_project(project)
    effective_profile = _effective_coding_profile(project)
    active_stage_chain = _build_coding_stage_chain(project)
    project_id = str(project["id"])
    run_files_cache_key = _run_files_key(user_id, project["id"])
    run_contract_cache_key = _run_contract_key(user_id, project["id"])
    stop_request_cache_key = _stop_request_key(user_id)
    last_valid_run_contract: dict[str, Any] | None = None
    tracker_finalized = False

    async def _update_tracker(**kwargs: Any) -> None:
        with contextlib.suppress(Exception):
            await _tracker_update(
                app=app,
                chat_id=chat_id,
                user_id=user_id,
                project_id=project_id,
                **kwargs,
            )

    async def _finalize_tracker(*, status: str, detail: str) -> None:
        nonlocal tracker_finalized
        if tracker_finalized:
            return
        with contextlib.suppress(Exception):
            await _tracker_finalize(
                app=app,
                chat_id=chat_id,
                user_id=user_id,
                project_id=project_id,
                status=status,
                detail=detail,
            )
        tracker_finalized = True

    def _execution_progress_value(
        *,
        successful: int,
        failed: int,
        skipped: int,
        current_index: int,
        current_fraction: float = 0.0,
    ) -> float:
        total_est = max(1, int(current_index) if current_index > 0 else 1)
        if milestones_total_local > 0:
            total_est = milestones_total_local
        completed = successful + failed + skipped + _clamp_unit(current_fraction)
        return _clamp_unit(completed / float(total_est))

    milestones_total_local = 0

    try:
        app.bot_data.pop(stop_request_cache_key, None)
        await _update_tracker(
            phase="setup",
            phase_detail="Preparing worker project directory",
            status="running",
            setup_progress=0.2,
            stage="",
            gate="",
        )
        # Ã¢â€â‚¬Ã¢â€â‚¬ Always create the project folder on the worker Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        if is_worker_available():
            try:
                await send_action(
                    "create_directory",
                    {"directory": working_dir},
                    confirmed=True,
                )
            except Exception:
                pass  # Directory may already exist Ã¢â‚¬â€ not fatal.
            await _update_tracker(
                phase="setup",
                phase_detail="Worker directory ready",
                setup_progress=0.45,
            )
        else:
            await _update_tracker(
                phase="setup",
                phase_detail="Worker unavailable",
                status="failed",
                setup_progress=0.45,
            )
            await _finalize_tracker(
                status="failed",
                detail="Worker unavailable before coding started",
            )
            await app.bot.send_message(
                chat_id, "Ã¢Å¡Â Ã¯Â¸Â Worker not connected Ã¢â‚¬â€ cannot create project folder."
            )
            return

        # Ã¢â€â‚¬Ã¢â€â‚¬ Optional GitHub setup Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        await _update_tracker(
            phase="setup",
            phase_detail="Running coding preflight checks",
            setup_progress=0.6,
        )
        preflight_ok, preflight_error, preflight_stage_chain = await _preflight_coding_environment(
            project=project,
        )
        if preflight_stage_chain:
            active_stage_chain = preflight_stage_chain
        if not preflight_ok:
            await _update_tracker(
                phase="setup",
                phase_detail="Preflight failed",
                status="failed",
                setup_progress=0.6,
            )
            await _finalize_tracker(
                status="failed",
                detail="Preflight failed",
            )
            await app.bot.send_message(
                chat_id,
                (
                    "\u26A0\uFE0F Coding preflight failed.\n"
                    f"<code>{html_mod.escape(preflight_error[:320])}</code>\n\n"
                    "Fix control-plane/worker setup and tap Retry Coding."
                ),
                parse_mode="HTML",
                reply_markup=retry_coding(project["id"]),
            )
            return
        if preflight_error:
            await _update_tracker(
                phase="setup",
                phase_detail=f"Preflight warning: {preflight_error[:120]}",
                setup_progress=0.7,
            )
            await app.bot.send_message(
                chat_id,
                f"\u26A0\uFE0F Coding preflight warning: {preflight_error[:260]}",
            )
        else:
            await _update_tracker(
                phase="setup",
                phase_detail=(
                    "Preflight checks passed"
                    if not active_stage_chain
                    else f"Preflight checks passed ({' -> '.join(active_stage_chain)})"
                ),
                setup_progress=0.75,
            )

        if do_github:
            await _update_tracker(
                phase="setup",
                phase_detail="Initializing GitHub repository",
                setup_progress=0.85,
            )
            await app.bot.send_message(chat_id, "Ã°Å¸â€Â§ Setting up GitHub repo and project folderÃ¢â‚¬Â¦")
            try:
                # Step 1: git init.
                init_result = await send_action(
                    "git_init",
                    {"working_dir": working_dir},
                    confirmed=True,
                )
                if init_result.get("status") == "error":
                    raise RuntimeError(init_result.get("error", "git init failed"))
                _init_inner = init_result.get("result", {})
                if _init_inner.get("returncode", 0) != 0:
                    raise RuntimeError(_init_inner.get("stderr") or _init_inner.get("stdout") or "git init failed")

                # Step 2: write a README so the initial push has a commit.
                readme_content = (
                    f"# {project['name']}\n\n"
                    f"{project.get('description') or project.get('project_type', '')}\n\n"
                    "_Created by SKYNET_\n"
                )
                readme_path = f"{working_dir}/README.md"
                fw_result = await send_action(
                    "file_write",
                    {"file": readme_path, "content": readme_content},  # key is "file" not "path"
                    confirmed=True,
                )
                _fw_inner = fw_result.get("result", fw_result)
                if _fw_inner.get("returncode", 0) != 0:
                    raise RuntimeError(
                        _fw_inner.get("stderr") or _fw_inner.get("stdout") or "file_write failed"
                    )

                # Step 3: stage + commit README.
                await send_action("git_add_all", {"working_dir": working_dir}, confirmed=True)
                commit_result = await send_action(
                    "git_commit",
                    {"working_dir": working_dir, "message": "Initial commit"},
                    confirmed=True,
                )
                _commit_inner = commit_result.get("result", {})
                if _commit_inner.get("returncode", 0) != 0:
                    raise RuntimeError(
                        _commit_inner.get("stderr") or _commit_inner.get("stdout") or "git commit failed"
                    )

                # Step 4: create GitHub repo and push the initial commit.
                gh_result = await send_action(
                    "gh_create_repo",
                    {
                        "working_dir": working_dir,
                        "repo_name":   slug,
                        "description": f"Created by SKYNET Ã¢â‚¬â€ {project['project_type']}",
                        "private":     True,
                    },
                    timeout=120,
                    confirmed=True,
                )
                if gh_result.get("status") == "error":
                    raise RuntimeError(gh_result.get("error", "Unknown error"))
                _gh_inner = gh_result.get("result", {})
                if _gh_inner.get("returncode", 0) != 0:
                    raise RuntimeError(_gh_inner.get("stderr") or _gh_inner.get("stdout") or "gh_create_repo failed")
                await app.bot.send_message(chat_id, "Ã¢Å“â€¦ GitHub repo created and pushed.")
                await _update_tracker(
                    phase="setup",
                    phase_detail="GitHub setup complete",
                    setup_progress=1.0,
                )
            except Exception as exc:
                await app.bot.send_message(
                    chat_id, f"Ã¢Å¡Â Ã¯Â¸Â GitHub setup failed: {exc}\nContinuing anywayÃ¢â‚¬Â¦"
                )
                await _update_tracker(
                    phase="setup",
                    phase_detail=f"GitHub setup warning: {str(exc)[:120]}",
                    setup_progress=0.95,
                )
        else:
            await _update_tracker(
                phase="setup",
                phase_detail="Skipping GitHub setup",
                setup_progress=1.0,
            )

        # Ã¢â€â‚¬Ã¢â€â‚¬ Extract milestones from plan Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        await _update_tracker(
            phase="milestone_extraction",
            phase_detail="Breaking plan into milestones",
            extraction_progress=0.1,
            setup_progress=1.0,
            milestone_index=0,
            milestones_total=0,
            stage="",
            gate="",
        )
        await app.bot.send_message(chat_id, "Ã°Å¸â€œâ€¹ Breaking the plan into milestonesÃ¢â‚¬Â¦")
        try:
            async def _extraction_heartbeat(elapsed: int) -> None:
                prog = min(0.9, 0.15 + min(0.75, elapsed / 240.0))
                await _update_tracker(
                    phase="milestone_extraction",
                    phase_detail=f"Still extracting milestones ({elapsed}s elapsed)",
                    extraction_progress=prog,
                    heartbeat_elapsed=elapsed,
                )

            milestones = await _extract_milestones_with_heartbeat(
                router=router,
                project=project,
                working_dir=working_dir,
                app=app,
                chat_id=chat_id,
                stop_request_cache_key=stop_request_cache_key,
                heartbeat_hook=_extraction_heartbeat,
            )
        except Exception as exc:
            err = (str(exc).strip() or type(exc).__name__)[:300]
            if err.startswith("STOP_REQUESTED:"):
                app.bot_data.pop(stop_request_cache_key, None)
                await _update_tracker(
                    phase="finalization",
                    phase_detail="Stopped during milestone extraction",
                    status="stopped",
                    extraction_progress=0.5,
                )
                await _finalize_tracker(
                    status="stopped",
                    detail="Stopped during milestone extraction",
                )
                await app.bot.send_message(
                    chat_id,
                    "Ã°Å¸â€ºâ€˜ Session stopped before milestones were extracted.",
                )
                return
            if err.startswith("MILESTONE_EXTRACTION_TIMEOUT:"):
                await _update_tracker(
                    phase="milestone_extraction",
                    phase_detail="Milestone extraction timed out",
                    status="failed",
                    extraction_progress=0.9,
                )
                await _finalize_tracker(
                    status="failed",
                    detail="Milestone extraction timed out",
                )
                await app.bot.send_message(
                    chat_id,
                    (
                        "Ã¢Å¡Â Ã¯Â¸Â Timed out while breaking the plan into milestones.\n"
                        f"<code>{html_mod.escape(err)}</code>\n\n"
                        "Tap Retry Coding after checking AI provider health."
                    ),
                    parse_mode="HTML",
                    reply_markup=retry_coding(project["id"]),
                )
                return
            raise
        total = len(milestones)
        milestones_total_local = total

        if not milestones:
            await _update_tracker(
                phase="milestone_extraction",
                phase_detail="No milestones extracted",
                status="failed",
                extraction_progress=1.0,
            )
            await _finalize_tracker(
                status="failed",
                detail="No milestones extracted from plan",
            )
            await app.bot.send_message(
                chat_id,
                "Could not extract milestones from the plan. "
                "Please refine your plan and try again.",
                reply_markup=main_menu(),
            )
            return

        await _update_tracker(
            phase="milestone_extraction",
            phase_detail=f"Extracted {total} milestone(s)",
            extraction_progress=1.0,
            milestones_total=total,
        )
        await app.bot.send_message(
            chat_id, f"Found <b>{total} milestone(s)</b>. Let's go!", parse_mode="HTML"
        )

        successful_milestones = 0
        failed_milestones = 0
        skipped_milestones = 0
        all_written_files: list[str] = []

        # Ã¢â€â‚¬Ã¢â€â‚¬ Milestone loop Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        for i, milestone_text in enumerate(milestones, 1):
            await _update_tracker(
                phase="milestone_review",
                phase_detail=f"Waiting for approval on milestone {i}/{total}",
                milestone_index=i,
                milestones_total=total,
                execution_progress=_execution_progress_value(
                    successful=successful_milestones,
                    failed=failed_milestones,
                    skipped=skipped_milestones,
                    current_index=i,
                    current_fraction=0.0,
                ),
                attempt=0,
                stage="",
                gate="",
            )
            if app.bot_data.get(stop_request_cache_key):
                app.bot_data.pop(stop_request_cache_key, None)
                await _update_tracker(
                    phase="finalization",
                    phase_detail=f"Stopped at milestone {i}/{total}",
                    status="stopped",
                    milestone_index=i,
                )
                await _finalize_tracker(
                    status="stopped",
                    detail=f"Stopped at milestone {i}/{total}",
                )
                await app.bot.send_message(
                    chat_id,
                    f"ðŸ›‘ Session stopped at milestone {i}/{total}.\n"
                    "Use /status to review completed milestones.",
                )
                return

            # Register approval event before rendering buttons so fast taps are not lost.
            event = asyncio.Event()
            event_key    = _MS_EVENT_KEY.format(uid=user_id)
            decision_key = _MS_DECISION_KEY.format(uid=user_id)
            app.bot_data[event_key] = event
            app.bot_data.pop(decision_key, None)

            # Show milestone to user.
            await app.bot.send_message(
                chat_id,
                f"<b>Milestone {i}/{total}</b>\n\n{milestone_text}",
                parse_mode="HTML",
                reply_markup=milestone_review(),
            )

            # Wait for user decision (up to 1 hour).

            try:
                await asyncio.wait_for(event.wait(), timeout=3600)
            except asyncio.TimeoutError:
                await _update_tracker(
                    phase="milestone_review",
                    phase_detail=f"Milestone {i}/{total} approval timed out",
                    milestone_index=i,
                    execution_progress=_execution_progress_value(
                        successful=successful_milestones,
                        failed=failed_milestones,
                        skipped=skipped_milestones + 1,
                        current_index=i,
                        current_fraction=0.0,
                    ),
                )
                await app.bot.send_message(
                    chat_id, f"Ã¢ÂÂ° Milestone {i} timed out Ã¢â‚¬â€ skipping."
                )
                app.bot_data.pop(event_key, None)
                skipped_milestones += 1
                continue

            app.bot_data.pop(event_key, None)
            decision = app.bot_data.pop(decision_key, "skip")

            if decision == "stop":
                await _update_tracker(
                    phase="finalization",
                    phase_detail=f"Stopped at milestone {i}/{total}",
                    status="stopped",
                    milestone_index=i,
                )
                await _finalize_tracker(
                    status="stopped",
                    detail=f"Stopped at milestone {i}/{total}",
                )
                await app.bot.send_message(
                    chat_id,
                    f"Ã°Å¸â€ºâ€˜ Session stopped at milestone {i}/{total}.\n"
                    "Use /status to review completed milestones.",
                )
                return

            if decision == "skip":
                await _update_tracker(
                    phase="milestone_execution",
                    phase_detail=f"Milestone {i}/{total} skipped",
                    milestone_index=i,
                    execution_progress=_execution_progress_value(
                        successful=successful_milestones,
                        failed=failed_milestones,
                        skipped=skipped_milestones + 1,
                        current_index=i,
                        current_fraction=1.0,
                    ),
                    stage="",
                    gate="",
                    attempt=0,
                )
                await app.bot.send_message(chat_id, f"Ã¢ÂÂ­ Milestone {i} skipped.")
                skipped_milestones += 1
                continue

            # Create DB task record.
            short_title = milestone_text[:80].split("\n")[0]
            task_rec = await create_task(
                db,
                project_id=project["id"],
                title=f"Milestone {i}: {short_title}",
                description=milestone_text,
            )
            await update_task_status(db, task_rec["id"], status="running")
            await _update_tracker(
                phase="milestone_execution",
                phase_detail=f"Executing milestone {i}/{total}",
                milestone_index=i,
                attempt=1,
                stage="",
                gate="",
                execution_progress=_execution_progress_value(
                    successful=successful_milestones,
                    failed=failed_milestones,
                    skipped=skipped_milestones,
                    current_index=i,
                    current_fraction=0.15,
                ),
            )
            await app.bot.send_message(chat_id, f"Ã¢Å¡â„¢Ã¯Â¸Â Executing milestone {i}Ã¢â‚¬Â¦")

            # Dispatch to CLAW worker.
            if not is_worker_available():
                await _update_tracker(
                    phase="milestone_execution",
                    phase_detail="Worker disconnected during execution",
                    status="failed",
                    milestone_index=i,
                    execution_progress=_execution_progress_value(
                        successful=successful_milestones,
                        failed=failed_milestones + 1,
                        skipped=skipped_milestones,
                        current_index=i,
                        current_fraction=1.0,
                    ),
                )
                await app.bot.send_message(
                    chat_id, "Ã¢Å¡Â Ã¯Â¸Â Worker disconnected Ã¢â‚¬â€ cannot execute. Skipping."
                )
                await update_task_status(
                    db, task_rec["id"],
                    status="failed", error_message="Agent not connected",
                )
                failed_milestones += 1
                continue

            prompt = (
                f"Project: {project['name']} ({project['project_type']})\n"
                f"Working directory: {working_dir}\n\n"
                f"Task:\n{milestone_text}\n\n"
                "Implement this task completely. Write all necessary files, "
                "then run tests if applicable."
            )

            # Feed previously written code so the model builds incrementally.
            if all_written_files:
                existing_code = ""
                for fname in all_written_files:
                    if not fname.endswith((".py", ".js", ".ts", ".html", ".css")):
                        continue
                    try:
                        read_result = await send_action(
                            "file_read",
                            {"file": f"{working_dir}/{fname}"},
                            timeout=10,
                            confirmed=True,
                        )
                        inner_read = read_result.get("result", read_result)
                        content = (inner_read.get("stdout") or "").strip()
                        if content and len(content) < 3000:
                            existing_code += f"\n\n```{fname}\n{content}\n```"
                    except Exception:
                        pass
                if existing_code:
                    prompt += (
                        "\n\nExisting files (build on these, do NOT rewrite unchanged code):"
                        + existing_code
                    )

            try:
                # Non-legacy profiles use explicit stage-chain generation.
                claude_ollama_mode = (
                    effective_profile != _CODING_PROFILE_LEGACY
                    or bool(getattr(cfg, "CODING_FORCE_PRIMARY_FOR_ALL", False))
                )

                gate_completion: set[str] = set()

                async def _stage_tracker_hook(**payload: Any) -> None:
                    event_name = str(payload.get("event") or "").strip()
                    stage_name = str(
                        payload.get("stage") or payload.get("next_stage") or ""
                    ).strip()
                    detail = str(payload.get("detail") or "").strip()
                    stage_index = int(payload.get("stage_index", 0) or 0)
                    if event_name == "stage_switch":
                        logger.info(
                            "telegram.tracker.stage.switch project_id=%s task_id=%s stage=%s next_stage=%s reason=%s",
                            project_id,
                            task_rec["id"],
                            str(payload.get("stage") or ""),
                            str(payload.get("next_stage") or ""),
                            str(payload.get("reason") or "")[:220],
                        )
                    elif event_name in {"stage_start", "stage_fail", "stage_success"}:
                        logger.info(
                            "telegram.tracker.update project_id=%s task_id=%s stage_event=%s stage=%s",
                            project_id,
                            task_rec["id"],
                            event_name,
                            stage_name,
                        )
                    await _update_tracker(
                        phase="milestone_execution",
                        phase_detail=detail or f"{event_name.replace('_', ' ')}",
                        milestone_index=i,
                        attempt=stage_index if stage_index > 0 else 1,
                        stage=stage_name,
                        session_id=str(payload.get("session_id") or ""),
                        runtime_mode=str(payload.get("runtime") or ""),
                        queue_mode=str(payload.get("queue_mode") or ""),
                        execution_progress=_execution_progress_value(
                            successful=successful_milestones,
                            failed=failed_milestones,
                            skipped=skipped_milestones,
                            current_index=i,
                            current_fraction=0.3,
                        ),
                    )

                async def _gate_tracker_hook(**payload: Any) -> None:
                    gate_name = str(payload.get("gate_name") or "").strip()
                    gate_status = str(payload.get("status") or "").strip().lower()
                    gate_summary = str(payload.get("summary") or "").strip()
                    gate_command = str(payload.get("command") or "").strip()
                    if gate_status in {"passed", "failed", "skipped"} and gate_name:
                        gate_completion.add(gate_name)
                    gates_progress = _clamp_unit(
                        len(gate_completion) / float(max(1, len(_TRACKER_GATE_ORDER)))
                    )
                    run_contract_status = None
                    if gate_name == "run_contract":
                        if gate_status == "passed":
                            run_contract_status = "validated"
                        elif gate_status == "failed":
                            run_contract_status = "invalid"
                    logger.info(
                        "telegram.tracker.gate.update project_id=%s task_id=%s gate=%s status=%s summary=%s",
                        project_id,
                        task_rec["id"],
                        gate_name,
                        gate_status,
                        gate_summary[:220],
                    )
                    await _update_tracker(
                        phase="quality_gates",
                        phase_detail=(
                            f"{gate_name}: {gate_status}"
                            + (f" ({gate_summary[:120]})" if gate_summary else "")
                        ),
                        milestone_index=i,
                        gate=gate_name,
                        attempt=int(payload.get("attempt", 1) or 1),
                        gates_progress=gates_progress,
                        run_contract_status=run_contract_status,
                        execution_progress=_execution_progress_value(
                            successful=successful_milestones,
                            failed=failed_milestones,
                            skipped=skipped_milestones,
                            current_index=i,
                            current_fraction=0.8,
                        ),
                    )

                if claude_ollama_mode:
                    execution_stage_chain = (
                        active_stage_chain
                        or _build_coding_stage_chain(project, include_legacy=True)
                    )
                    generation_result = await _run_stage_chain_for_generation(
                        db=db,
                        app=app,
                        chat_id=chat_id,
                        user_id=user_id,
                        project=project,
                        task_id=task_rec["id"],
                        prompt=prompt,
                        working_dir=working_dir,
                        stage_chain=execution_stage_chain,
                        label_prefix="coding generation",
                        require_runnable_files=True,
                        notify_stage_switch=True,
                        tracker_hook=_stage_tracker_hook,
                    )
                    if not generation_result.get("ok"):
                        attempted = generation_result.get("attempted_stages") or []
                        raise RuntimeError(f"GENERATION_FAILED: {','.join(attempted) or 'none'}")
                    inner = generation_result.get("inner", {})
                    # New profile: always use Claude CLI against Ollama in attempt 1.
                    max_attempts = 0
                    for attempt in range(1, max_attempts + 1):
                        result = await _send_action_with_heartbeat(
                            app=app,
                            chat_id=chat_id,
                            user_id=user_id,
                            action="run_coding_agent",
                            params={
                                "agent": "claude",
                                "backend": "ollama",
                                "model": cfg.CLAUDE_OLLAMA_DEFAULT_MODEL,
                                "prompt": prompt,
                                "working_dir": working_dir,
                                "timeout_seconds": 1800,
                                "auto_pull_model": cfg.CLAUDE_OLLAMA_AUTO_PULL,
                            },
                            timeout=1800,
                            label=f"coding agent attempt {attempt}/{max_attempts}",
                            max_wait_seconds=max(
                                1,
                                int(getattr(cfg, "CODING_AGENT_MAX_WAIT_SECONDS", 900) or 900),
                            ),
                        )
                        if result.get("status") == "error":
                            raise RuntimeError(result.get("error", "run_coding_agent failed"))

                        inner = result.get("result", result)
                        return_code = inner.get("returncode", inner.get("exit_code", 0))
                        written = inner.get("files_written") or []

                        if return_code == 0 and written:
                            break

                        if attempt < max_attempts:
                            reason = "no files generated" if not written else f"exit code {return_code}"
                            await app.bot.send_message(
                                chat_id,
                                f"Ã¢Å¡Â Ã¯Â¸Â Attempt {attempt}/{max_attempts} Ã¢â‚¬â€ {reason}. RetryingÃ¢â‚¬Â¦"
                            )
                            continue

                        if return_code != 0:
                            detail = (
                                inner.get("stderr")
                                or inner.get("stdout")
                                or f"Failed after {max_attempts} attempts (exit {return_code})"
                            )
                            raise RuntimeError(str(detail))
                else:
                    # Legacy profile keeps router-first behavior.
                    router_written: list[str] = []
                    try:
                        coding_resp = await router.chat(
                            messages=[{"role": "user", "content": prompt}],
                            system=_CODING_SYSTEM_PROMPT,
                            max_tokens=4096,
                            task_type="coding",
                        )
                        if coding_resp.text:
                            blocks = _parse_code_blocks(coding_resp.text)
                            if blocks:
                                for fname, file_content in blocks:
                                    await send_action(
                                        "file_write",
                                        {"file": f"{working_dir}/{fname}", "content": file_content},
                                        timeout=15,
                                        confirmed=True,
                                    )
                                    router_written.append(fname)
                                logger.info(
                                    "Router coding wrote %d file(s) via %s: %s",
                                    len(router_written),
                                    coding_resp.provider_name,
                                    ", ".join(router_written),
                                )
                    except Exception as exc:
                        logger.info("Router coding unavailable, using coding CLI fallback: %s", exc)

                    if router_written:
                        inner = {
                            "returncode": 0,
                            "stdout": f"Wrote {len(router_written)} file(s): {', '.join(router_written)}",
                            "files_written": router_written,
                        }

                    if not router_written:
                        max_attempts = 3
                        for attempt in range(1, max_attempts + 1):
                            result = await _send_action_with_heartbeat(
                                app=app,
                                chat_id=chat_id,
                                user_id=user_id,
                                action="run_coding_agent",
                                params={
                                    "agent": "claude",
                                    "backend": "auto",
                                    "prompt": prompt,
                                    "working_dir": working_dir,
                                    "timeout_seconds": 1800,
                                },
                                timeout=1800,
                                label=f"coding agent attempt {attempt}/{max_attempts}",
                                max_wait_seconds=max(
                                    1,
                                    int(getattr(cfg, "CODING_AGENT_MAX_WAIT_SECONDS", 900) or 900),
                                ),
                            )
                            if result.get("status") == "error":
                                raise RuntimeError(result.get("error", "run_coding_agent failed"))

                            inner = result.get("result", result)
                            return_code = inner.get("returncode", inner.get("exit_code", 0))
                            written = inner.get("files_written") or []

                            if return_code == 0 and written:
                                break

                            if attempt < max_attempts:
                                reason = "no files generated" if not written else f"exit code {return_code}"
                                await app.bot.send_message(
                                    chat_id,
                                    f"Ã¢Å¡Â Ã¯Â¸Â Attempt {attempt}/{max_attempts} Ã¢â‚¬â€ {reason}. RetryingÃ¢â‚¬Â¦"
                                )
                                continue

                            if return_code != 0:
                                detail = (
                                    inner.get("stderr")
                                    or inner.get("stdout")
                                    or f"Failed after {max_attempts} attempts (exit {return_code})"
                                )
                                raise RuntimeError(str(detail))

                # Last-resort strict rescue: if coding agent exits 0 but writes nothing,
                # force one explicit generation pass with required file artifacts.
                return_code = int(inner.get("returncode", inner.get("exit_code", 0)) or 0)
                written = _normalize_written_files(inner.get("files_written"))
                if (
                    strict_mode
                    and successful_milestones == 0
                    and return_code == 0
                    and not _has_runnable_written_files(written)
                ):
                    entry_interpreter = "node" if _project_prefers_node(project["project_type"]) else "python"
                    entry_ext = ".js" if entry_interpreter == "node" else ".py"
                    entrypoint = f"{slug}{entry_ext}"
                    combined_text = (
                        f"{milestone_text}\n{project.get('description', '')}\n{prompt}"
                    )
                    stdout_marker = _extract_expected_stdout_marker(combined_text)
                    rescue_prompt = _build_strict_rescue_prompt(
                        project=project,
                        milestone_text=milestone_text,
                        working_dir=working_dir,
                        entrypoint=entrypoint,
                        interpreter=entry_interpreter,
                        stdout_marker=stdout_marker,
                    )

                    await app.bot.send_message(
                        chat_id,
                        "âš ï¸ No files produced yet. Running strict recovery generationâ€¦",
                    )
                    rescue_stage_result = await _run_stage_chain_for_generation(
                        db=db,
                        app=app,
                        chat_id=chat_id,
                        user_id=user_id,
                        project=project,
                        task_id=task_rec["id"],
                        prompt=rescue_prompt,
                        working_dir=working_dir,
                        stage_chain=(
                            active_stage_chain
                            or _build_coding_stage_chain(project, include_legacy=True)
                        ),
                        label_prefix="strict recovery generation",
                        require_runnable_files=True,
                        notify_stage_switch=True,
                        tracker_hook=_stage_tracker_hook,
                    )
                    if rescue_stage_result.get("ok"):
                        rescue_result: dict[str, Any] = {
                            "status": "success",
                            "result": rescue_stage_result.get("inner", {}),
                        }
                    else:
                        attempted = rescue_stage_result.get("attempted_stages") or []
                        rescue_result = {
                            "status": "error",
                            "error": f"GENERATION_FAILED: {','.join(attempted) or 'none'}",
                        }
                    if rescue_result.get("status") == "error":
                        logger.warning(
                            "Strict recovery generation failed for project %s milestone %s: %s",
                            project.get("id"),
                            i,
                            rescue_result.get("error"),
                        )
                    else:
                        rescue_inner = rescue_result.get("result", rescue_result)
                        rescue_code = int(
                            rescue_inner.get("returncode", rescue_inner.get("exit_code", 0)) or 0
                        )
                        rescue_written = rescue_inner.get("files_written") or []
                        if rescue_code == 0 and _has_runnable_written_files(rescue_written):
                            inner = rescue_inner
                            written = rescue_written

                    if (
                        strict_mode
                        and cfg.STRICT_EMPTY_OUTPUT_EMERGENCY_SCAFFOLD
                        and not _has_runnable_written_files(written)
                    ):
                        try:
                            emergency_written, emergency_summary = await _write_strict_emergency_scaffold(
                                working_dir=working_dir,
                                entrypoint=entrypoint,
                                interpreter=entry_interpreter,
                                stdout_marker=stdout_marker,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Emergency strict scaffold failed for project %s milestone %s: %s",
                                project.get("id"),
                                i,
                                exc,
                            )
                        else:
                            if emergency_written:
                                inner = {
                                    "returncode": 0,
                                    "stdout": emergency_summary,
                                    "stderr": "",
                                    "files_written": emergency_written,
                                }
                                written = emergency_written

                summary = (inner.get("stdout") or inner.get("stderr") or "")[:500].strip()

                # Track written files for the run handler.
                written = _normalize_written_files(inner.get("files_written"))
                if written:
                    all_written_files.extend(written)
                else:
                    # Fallback: parse "Wrote N file(s): a.py, b.py" from stdout
                    m = re.search(r"Wrote \d+ file\(s\): (.+)", summary)
                    if m:
                        all_written_files.extend(
                            f.strip() for f in m.group(1).split(",") if f.strip()
                        )

                if strict_mode:
                    await _update_tracker(
                        phase="quality_gates",
                        phase_detail="Running strict quality gates",
                        milestone_index=i,
                        gate="",
                        gates_progress=0.0,
                        execution_progress=_execution_progress_value(
                            successful=successful_milestones,
                            failed=failed_milestones,
                            skipped=skipped_milestones,
                            current_index=i,
                            current_fraction=0.7,
                        ),
                    )
                    gate_result = await _run_strict_quality_gates(
                        db=db,
                        task_id=task_rec["id"],
                        project=project,
                        milestone_text=milestone_text,
                        working_dir=working_dir,
                        tracker_hook=_gate_tracker_hook,
                        stage_chain=(
                            active_stage_chain
                            or _build_coding_stage_chain(project, include_legacy=True)
                        ),
                    )
                    if not gate_result.get("passed"):
                        failed_names = gate_result.get("failed_gate_names") or []
                        err = str(gate_result.get("error_message") or "GATES_FAILED")
                        if (
                            not err.startswith("INFRA_FAILURE:")
                            and not err.startswith("FALLBACK_UNAVAILABLE:")
                            and failed_names
                        ):
                            err = f"GATES_FAILED: {','.join(failed_names)}"
                        err = err[:300]
                        await update_task_status(
                            db,
                            task_rec["id"],
                            status="failed",
                            error_message=err,
                        )
                        failed_milestones += 1
                        await _update_tracker(
                            phase="quality_gates",
                            phase_detail=f"Milestone {i}/{total} failed gates: {','.join(failed_names) or 'unknown'}",
                            status="failed",
                            milestone_index=i,
                            gate=(failed_names[0] if failed_names else ""),
                            run_contract_status=(
                                "invalid"
                                if "run_contract" in {str(n) for n in failed_names}
                                else None
                            ),
                            gates_progress=1.0,
                            execution_progress=_execution_progress_value(
                                successful=successful_milestones,
                                failed=failed_milestones,
                                skipped=skipped_milestones,
                                current_index=i,
                                current_fraction=1.0,
                            ),
                        )
                        await app.bot.send_message(
                            chat_id,
                            f"ÃƒÂ¢Ã‚ÂÃ…â€™ Milestone {i} failed:\n<code>{html_mod.escape(err)}</code>",
                            parse_mode="HTML",
                        )
                        continue
                    run_contract = gate_result.get("run_contract")
                    if isinstance(run_contract, dict):
                        last_valid_run_contract = run_contract
                    await _update_tracker(
                        phase="quality_gates",
                        phase_detail=f"Milestone {i}/{total} quality gates passed",
                        milestone_index=i,
                        gate="",
                        run_contract_status="validated",
                        gates_progress=1.0,
                    )
                    pass_summary = str(gate_result.get("pass_summary") or "").strip()
                    if pass_summary:
                        summary = (summary + "\n" + pass_summary).strip()[:500]

                await update_task_status(
                    db, task_rec["id"], status="done", result_summary=summary
                )
                successful_milestones += 1
                await _update_tracker(
                    phase="milestone_execution" if not strict_mode else "quality_gates",
                    phase_detail=f"Milestone {i}/{total} completed",
                    milestone_index=i,
                    status="running",
                    stage="",
                    gate="",
                    attempt=0,
                    execution_progress=_execution_progress_value(
                        successful=successful_milestones,
                        failed=failed_milestones,
                        skipped=skipped_milestones,
                        current_index=i,
                        current_fraction=1.0,
                    ),
                    gates_progress=1.0 if strict_mode else None,
                )
                notice = f"Ã¢Å“â€¦ Milestone {i} complete!"
                if summary:
                    notice += f"\n\n{summary}"
                await app.bot.send_message(chat_id, notice)

            except Exception as exc:
                err = (str(exc).strip() or type(exc).__name__)[:300]
                if err.startswith("STOP_REQUESTED:"):
                    await update_task_status(
                        db,
                        task_rec["id"],
                        status="failed",
                        error_message="STOP_REQUESTED",
                    )
                    app.bot_data.pop(stop_request_cache_key, None)
                    await _update_tracker(
                        phase="finalization",
                        phase_detail=f"Stopped while executing milestone {i}/{total}",
                        status="stopped",
                        milestone_index=i,
                        execution_progress=_execution_progress_value(
                            successful=successful_milestones,
                            failed=failed_milestones + 1,
                            skipped=skipped_milestones,
                            current_index=i,
                            current_fraction=1.0,
                        ),
                    )
                    await _finalize_tracker(
                        status="stopped",
                        detail=f"Stopped at milestone {i}/{total}",
                    )
                    await app.bot.send_message(
                        chat_id,
                        f"ðŸ›‘ Session stopped while executing milestone {i}/{total}.\n"
                        "Use /status to review progress.",
                    )
                    return
                if err.startswith("WAIT_TIMEOUT:"):
                    await update_task_status(
                        db,
                        task_rec["id"],
                        status="failed",
                        error_message=err,
                    )
                    failed_milestones += 1
                    await _update_tracker(
                        phase="milestone_execution",
                        phase_detail=f"Milestone {i}/{total} timed out",
                        status="failed",
                        milestone_index=i,
                        execution_progress=_execution_progress_value(
                            successful=successful_milestones,
                            failed=failed_milestones,
                            skipped=skipped_milestones,
                            current_index=i,
                            current_fraction=1.0,
                        ),
                    )
                    await _finalize_tracker(
                        status="failed",
                        detail=f"Milestone {i}/{total} timed out",
                    )
                    await app.bot.send_message(
                        chat_id,
                        (
                            f"âš ï¸ Milestone {i} timed out while waiting for the coding agent.\n"
                            f"<code>{html_mod.escape(err)}</code>\n\n"
                            "Tap Retry Coding after checking worker health."
                        ),
                        parse_mode="HTML",
                        reply_markup=retry_coding(project["id"]),
                    )
                    return
                await update_task_status(
                    db, task_rec["id"], status="failed", error_message=err
                )
                failed_milestones += 1
                await _update_tracker(
                    phase="milestone_execution",
                    phase_detail=f"Milestone {i}/{total} failed: {err[:120]}",
                    status="failed",
                    milestone_index=i,
                    execution_progress=_execution_progress_value(
                        successful=successful_milestones,
                        failed=failed_milestones,
                        skipped=skipped_milestones,
                        current_index=i,
                        current_fraction=1.0,
                    ),
                )
                await app.bot.send_message(
                    chat_id, f"Ã¢ÂÅ’ Milestone {i} failed:\n<code>{html_mod.escape(str(err))}</code>",
                    parse_mode="HTML",
                )

        # Ã¢â€â‚¬Ã¢â€â‚¬ Done Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        milestone_summary = (
            f"complete={successful_milestones}, "
            f"failed={failed_milestones}, "
            f"skipped={skipped_milestones}"
        )
        if successful_milestones > 0:
            unique_written: list[str] = []
            seen_written: set[str] = set()
            for path in all_written_files:
                clean = str(path).strip()
                if not clean:
                    continue
                key = clean.lower()
                if key in seen_written:
                    continue
                seen_written.add(key)
                unique_written.append(clean)

            app.bot_data[f"run_project_{user_id}"] = project["id"]
            app.bot_data[run_files_cache_key] = unique_written
            if strict_mode and last_valid_run_contract:
                app.bot_data[run_contract_cache_key] = last_valid_run_contract
            elif strict_mode:
                app.bot_data.pop(run_contract_cache_key, None)

            can_run_now = (not strict_mode) or bool(last_valid_run_contract)
            await _update_tracker(
                phase="finalization",
                phase_detail=(
                    f"Completed with {successful_milestones} successful milestone(s); "
                    f"failed={failed_milestones}, skipped={skipped_milestones}"
                ),
                status="completed",
                milestone_index=total,
                milestones_total=total,
                execution_progress=1.0,
                gates_progress=1.0 if strict_mode else None,
                final_progress=1.0,
                run_contract_status=(
                    "validated" if strict_mode and last_valid_run_contract else (
                        "invalid" if strict_mode else "legacy"
                    )
                ),
                stage="",
                gate="",
            )
            await _finalize_tracker(
                status="completed",
                detail=(
                    f"Session completed: complete={successful_milestones}, "
                    f"failed={failed_milestones}, skipped={skipped_milestones}"
                ),
            )
            await app.bot.send_message(
                chat_id,
                f"\U0001F389 <b>{project['name']}</b> coding session complete!\n"
                f"\U0001F4C1 <code>{working_dir}</code>\n"
                f"{milestone_summary}\n\n"
                "Use /status to review milestones or run the project now.",
                parse_mode="HTML",
                reply_markup=run_project() if can_run_now else main_menu(),
            )
        else:
            await app.bot.send_message(
                chat_id,
                f"\u26A0\uFE0F <b>{project['name']}</b> session finished with no successful milestones.\n"
                f"\U0001F4C1 <code>{working_dir}</code>\n"
                f"{milestone_summary}\n\n"
                "Tap Retry Coding to run again with your previous GitHub setup mode, "
                "or use /status to inspect failures first.",
                parse_mode="HTML",
                reply_markup=retry_coding(project["id"]),
            )
            await _update_tracker(
                phase="finalization",
                phase_detail=(
                    f"No successful milestones; failed={failed_milestones}, "
                    f"skipped={skipped_milestones}"
                ),
                status="failed",
                milestone_index=total,
                milestones_total=total,
                execution_progress=1.0 if total > 0 else 0.0,
                gates_progress=1.0 if strict_mode else None,
                final_progress=1.0,
                stage="",
                gate="",
            )
            await _finalize_tracker(
                status="failed",
                detail=(
                    f"No successful milestones (failed={failed_milestones}, "
                    f"skipped={skipped_milestones})"
                ),
            )

    except Exception:
        logger.exception("Coding loop crashed for project %s user %s", project["id"], user_id)
        await _update_tracker(
            phase="finalization",
            phase_detail="Unexpected coding loop crash",
            status="failed",
            final_progress=1.0,
            stage="",
            gate="",
        )
        await _finalize_tracker(
            status="failed",
            detail="Unexpected coding loop crash",
        )
        await app.bot.send_message(
            chat_id,
            "An unexpected error occurred in the coding loop. "
            "Use /status to see what was completed.",
            reply_markup=main_menu(),
        )
    finally:
        app.bot_data.pop(stop_request_cache_key, None)
        app.bot_data.pop(_MS_EVENT_KEY.format(uid=user_id), None)
        app.bot_data.pop(_MS_DECISION_KEY.format(uid=user_id), None)
        app.bot_data.pop(_active_project_key(user_id), None)
        loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
        if app.bot_data.get(loop_key) is asyncio.current_task():
            app.bot_data.pop(loop_key, None)


# Ã¢â€â‚¬Ã¢â€â‚¬ Run Project Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

async def run_project_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped Run Project and wants execution on the worker."""
    await update.callback_query.answer()

    user_id = update.effective_user.id
    db = context.bot_data.get(KEY_DB)

    # Prefer the project from the last coding session; fallback to most recent.
    pid_key = f"run_project_{user_id}"
    project_id = context.bot_data.get(pid_key)
    project = None
    if project_id:
        project = await get_project(db, project_id)

    if not project:
        tg_user = update.effective_user
        user = await ensure_user(
            db,
            telegram_user_id=tg_user.id,
            username=tg_user.username or "",
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name or "",
        )
        projects = await list_projects(db, user_id=user["id"])
        project = projects[0] if projects else None

    if not project:
        await update.callback_query.message.reply_text(
            "No project found to run.",
            reply_markup=main_menu(),
        )
        return

    strict_mode = _is_strict_project(project)
    run_files_cache_key = _run_files_key(user_id, project["id"])
    run_contract_cache_key = _run_contract_key(user_id, project["id"])

    if not is_worker_available():
        await update.callback_query.message.reply_text(
            "Worker not connected - cannot run the project right now.",
            reply_markup=run_project() if not strict_mode else main_menu(),
        )
        return

    slug = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"
    project_type = str(project.get("project_type", "") or "")

    run_cmd: str | None = None
    run_target: str | None = None

    if strict_mode:
        cached_contract = _validate_cached_run_contract(
            context.bot_data.get(run_contract_cache_key)
        )
        if cached_contract:
            run_cmd = cached_contract["command"]
            run_target = cached_contract["entrypoint"]
        else:
            manifest_contract, manifest_summary, manifest_infra = await _load_and_validate_run_contract(
                working_dir=working_dir,
            )
            if manifest_contract:
                context.bot_data[run_contract_cache_key] = manifest_contract
                run_cmd = manifest_contract["command"]
                run_target = manifest_contract["entrypoint"]
            else:
                detail = html_mod.escape(manifest_summary[:260])
                if manifest_infra:
                    msg = (
                        f"Run failed: infrastructure error validating <code>{_RUN_CONTRACT_FILE}</code>: {detail}"
                    )
                else:
                    msg = (
                        f"Strict run contract is missing or invalid (<code>{_RUN_CONTRACT_FILE}</code>): {detail}"
                    )
                await update.callback_query.message.reply_text(
                    msg,
                    parse_mode="HTML",
                    reply_markup=main_menu(),
                )
                return
    else:
        stored_files = context.bot_data.get(run_files_cache_key) or []
        if stored_files:
            resolved = _select_entrypoint(
                files=stored_files,
                slug=slug,
                project_type=project_type,
            )
        else:
            resolved = None

        if not resolved:
            try:
                list_result = await send_action(
                    "list_directory",
                    {"directory": working_dir, "recursive": True},
                    timeout=20,
                    confirmed=True,
                )
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                await update.callback_query.message.reply_text(
                    f"Run failed: infrastructure error while listing files: <code>{html_mod.escape(detail[:260])}</code>",
                    parse_mode="HTML",
                    reply_markup=run_project(),
                )
                return

            if list_result.get("status") == "error" or _action_exit_code(list_result) != 0:
                detail = _action_error_text(list_result, "list_directory")
                await update.callback_query.message.reply_text(
                    f"Run failed: infrastructure error while listing files: <code>{html_mod.escape(detail[:260])}</code>",
                    parse_mode="HTML",
                    reply_markup=run_project(),
                )
                return

            listing = str(_action_inner_result(list_result).get("stdout") or "")
            discovered_files = _extract_file_paths_from_listing(
                listing,
                working_dir=working_dir,
            )
            resolved = _select_entrypoint(
                files=discovered_files,
                slug=slug,
                project_type=project_type,
            )

        if not resolved:
            await update.callback_query.message.reply_text(
                f"No runnable entry point found in <code>{html_mod.escape(working_dir)}</code>.\n"
                "The coding agent may not have finished writing files. Try running the coding loop again.",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
            return

        run_cmd, run_target = resolved

    if not run_cmd:
        await update.callback_query.message.reply_text(
            "No runnable command is available for this project.",
            reply_markup=main_menu(),
        )
        return

    await update.callback_query.message.reply_text(
        f"Running <code>{html_mod.escape(run_target or '')}</code> on your laptop...",
        parse_mode="HTML",
    )

    run_markup = run_project() if (not strict_mode or _has_cached_run_contract(
        bot_data=context.bot_data,
        user_id=user_id,
        project_id=project["id"],
    )) else main_menu()

    try:
        result = await send_action(
            "exec_command",
            {"command": run_cmd, "working_dir": working_dir},
            timeout=60,
            confirmed=True,
        )
        if result.get("status") == "error":
            detail = _action_error_text(result, "exec_command")
            if _is_infra_error(detail):
                raise RuntimeError(f"Infrastructure error: {detail}")
            raise RuntimeError(detail)

        inner = _action_inner_result(result)
        stdout = (inner.get("stdout") or "").strip()
        stderr = (inner.get("stderr") or "").strip()
        exit_code = inner.get("returncode", inner.get("exit_code", 0))

        output = html_mod.escape((stdout or stderr or "(no output)")[:1000])
        status_line = (
            f"Finished (exit {exit_code})"
            if exit_code == 0
            else f"Exited with code {exit_code}"
        )
        await update.callback_query.message.reply_text(
            f"<pre>{output}</pre>\n\n{status_line}",
            parse_mode="HTML",
            reply_markup=run_markup,
        )
    except Exception as exc:
        await update.callback_query.message.reply_text(
            f"Run failed: {html_mod.escape(str(exc)[:300])}",
            parse_mode="HTML",
            reply_markup=run_markup,
        )

def _project_prefers_node(project_type: str) -> bool:
    lowered = project_type.lower()
    return any(token in lowered for token in ("javascript", "node", "react", "next.js", "js"))


def _normalize_slashes(path: str) -> str:
    return path.replace("\\", "/")


def _to_relative_path(path: str, *, working_dir: str) -> str:
    """
    Best-effort conversion of discovered paths to a path runnable from working_dir.
    """
    norm_path = _normalize_slashes(path).strip()
    norm_working_dir = _normalize_slashes(working_dir).rstrip("/")
    if not norm_path:
        return norm_path
    if norm_working_dir and norm_path.lower().startswith((norm_working_dir + "/").lower()):
        return norm_path[len(norm_working_dir) + 1 :]
    return norm_path


def _extract_file_paths_from_listing(listing: str, *, working_dir: str) -> list[str]:
    """
    Parse list_directory output (including recursive [DIR] format) into file paths.
    """
    files: list[str] = []
    dir_stack: list[str] = []

    for raw_line in listing.splitlines():
        line = raw_line.rstrip()
        if not line or line == "... (truncated)":
            continue

        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        depth = max(0, indent // 2)

        if stripped.startswith("[DIR] "):
            dir_name = stripped[len("[DIR] ") :].strip().rstrip("/\\")
            if not dir_name:
                continue

            # Absolute fallback formats may include a full path in [DIR] lines.
            if re.match(r"^[A-Za-z]:[\\/]", dir_name) or dir_name.startswith("/"):
                rel_dir = _to_relative_path(dir_name, working_dir=working_dir).strip("/")
                dir_stack = [p for p in _normalize_slashes(rel_dir).split("/") if p]
                continue

            if depth <= len(dir_stack):
                dir_stack = dir_stack[:depth]
            dir_stack.append(dir_name)
            continue

        file_name = re.sub(r"\s+\(\d+\s+bytes\)\s*$", "", stripped).strip()
        if not file_name:
            continue

        if re.match(r"^[A-Za-z]:[\\/]", file_name) or file_name.startswith("/"):
            rel_file = _to_relative_path(file_name, working_dir=working_dir)
        else:
            prefix = "/".join(dir_stack[:depth]) if depth <= len(dir_stack) else "/".join(dir_stack)
            rel_file = f"{prefix}/{file_name}" if prefix else file_name
        rel_file = _normalize_slashes(rel_file).lstrip("./")
        if rel_file:
            files.append(rel_file)

    # Preserve order while removing duplicates (case-insensitive for Windows paths).
    unique_files: list[str] = []
    seen: set[str] = set()
    for filepath in files:
        key = filepath.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(filepath)
    return unique_files


def _select_entrypoint(
    *,
    files: list[str],
    slug: str,
    project_type: str,
) -> tuple[str, str] | None:
    """
    Return (command, target_path) for the best .py/.js entrypoint candidate.
    """
    slug_lower = slug.lower()
    type_prefers_node = _project_prefers_node(project_type)
    candidates: list[tuple[int, str, str]] = []  # (score, interpreter, path)

    for path in files:
        norm_path = _normalize_slashes(path).strip()
        lower = norm_path.lower()
        if lower.endswith(".py"):
            interpreter = "python"
        elif lower.endswith(".js"):
            interpreter = "node"
        else:
            continue

        basename = lower.rsplit("/", 1)[-1]
        depth = lower.count("/")
        score = 0

        if basename in (f"{slug_lower}.py", f"{slug_lower}.js"):
            score += 120
        if basename in ("main.py", "app.py", "index.py", "main.js", "app.js", "index.js", "server.js"):
            score += 90

        if depth == 0:
            score += 20
        score -= depth * 2

        if " " in norm_path:
            score -= 10

        if type_prefers_node and interpreter == "node":
            score += 40
        if not type_prefers_node and interpreter == "python":
            score += 15

        candidates.append((score, interpreter, norm_path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[2].lower()))
    _, interpreter, target = candidates[0]
    return f"{interpreter} {target}", target


def _parse_json_string_list(raw: str) -> list[str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return [item.strip() for item in parsed if str(item).strip()]
    return None


async def _extract_milestones_codex_then_router(
    *,
    router,
    project: dict[str, Any],
    working_dir: str,
) -> list[str]:
    if str(getattr(cfg, "PLANNER_PRIMARY_AGENT", "codex")).strip().lower() != "codex":
        return await _extract_milestones_router(router, project)

    plan = project.get("description", "")
    if not plan:
        return []

    prompt = (
        "You are a project planner. Extract coding milestones from the plan.\n"
        "Return ONLY a JSON array of strings.\n"
        "No markdown, no explanation.\n\n"
        f"Project: {project['name']}\n"
        f"Plan:\n{plan}\n"
    )
    timeout = max(30, int(getattr(cfg, "MILESTONE_CODEX_TIMEOUT_SECONDS", 120) or 120))

    try:
        if _use_acp_orchestration():
            runner = get_openclaw_runner()
            session = await runner.start_session(
                phase="milestone_extraction",
                project_id=str(project.get("id") or ""),
                task_id=None,
                stage="codex",
                runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                queue_mode="soft",
            )
            run_result = await runner.run_prompt(
                session_id=str(session.get("session_id") or ""),
                prompt=prompt,
                timeout_seconds=timeout,
                stage="codex",
                backend="native",
            )
            if int(run_result.get("returncode", 1) or 1) != 0:
                raise RuntimeError(str(run_result.get("stderr") or run_result.get("stdout") or "codex failed"))
            output = str(run_result.get("stdout") or "").strip()
        else:
            result = await send_action(
                "run_coding_agent",
                {
                    "agent": "codex",
                    "backend": "auto",
                    "prompt": prompt,
                    "working_dir": working_dir,
                    "timeout_seconds": timeout,
                },
                timeout=timeout,
                confirmed=True,
            )
            if result.get("status") == "error":
                raise RuntimeError(_action_error_text(result, "run_coding_agent"))
            if _action_exit_code(result) != 0:
                raise RuntimeError(_action_excerpt(result))
            output = str(_action_inner_result(result).get("stdout") or "").strip()

        parsed = _parse_json_string_list(output)
        if parsed:
            return parsed
        raise RuntimeError("Codex milestone output was not valid JSON list")
    except Exception as exc:
        logger.warning(
            "milestone.primary.failover project_id=%s stage=codex error=%s",
            project.get("id"),
            str(exc)[:220],
        )
        return await _extract_milestones_router(router, project)


async def _extract_milestones(
    router,
    project: dict[str, Any],
    *,
    working_dir: str | None = None,
) -> list[str]:
    effective_working_dir = working_dir or f"{cfg.WORKER_PROJECTS_DIR}/_planner_sessions/milestones"
    return await _extract_milestones_codex_then_router(
        router=router,
        project=project,
        working_dir=effective_working_dir,
    )


async def _extract_milestones_router(router, project: dict) -> list[str]:
    """
    Ask the LLM to extract an ordered list of coding milestones from the plan.
    Returns a list of milestone description strings.
    """
    plan = project.get("description", "")
    if not plan:
        return []

    system = (
        "You are a project planner. Extract the coding milestones from the project plan "
        "as a JSON array of strings. Each element is ONE self-contained coding task "
        "(e.g. 'Set up project structure', 'Implement login endpoint'). "
        "Output ONLY a valid JSON array, no extra text."
    )
    messages = [
        {
            "role": "user",
            "content": f"Project: {project['name']}\n\nPlan:\n{plan}\n\n"
                       "Return the milestones as a JSON array of strings.",
        }
    ]

    try:
        response = await router.chat(
            messages=messages,
            system=system,
            max_tokens=1024,
            task_type="planning",
        )
        raw = (response.text or "").strip()
        # Strip markdown code fences if present.
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        milestones = json.loads(raw)
        if isinstance(milestones, list) and all(isinstance(m, str) for m in milestones):
            return [m.strip() for m in milestones if m.strip()]
    except Exception:
        logger.warning("JSON milestone extraction failed Ã¢â‚¬â€ falling back to line parsing")

    # Fallback: split on numbered list items (1. ... 2. ...)
    fallback = _parse_milestones_fallback(plan)
    if fallback:
        return fallback

    # Last resort: ask the LLM to generate milestones from the project name and type
    # (handles cases where the plan text is garbage or a meta-response).
    logger.warning("No milestones found in plan text Ã¢â‚¬â€ generating from project info")
    try:
        gen_system = (
            "You are a project planner. Generate 2-4 coding milestones for the given project. "
            "Each milestone is ONE self-contained coding task. "
            "Output ONLY a valid JSON array of strings, no extra text."
        )
        gen_messages = [{
            "role": "user",
            "content": (
                f"Project name: {project['name']}\n"
                f"Type: {project.get('project_type', 'Other')}\n"
                f"Description: {plan[:500]}\n\n"
                "Generate milestones as a JSON array of strings."
            ),
        }]
        response = await router.chat(
            messages=gen_messages, system=gen_system,
            max_tokens=512, task_type="planning",
        )
        raw = (response.text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        milestones = json.loads(raw)
        if isinstance(milestones, list) and all(isinstance(m, str) for m in milestones):
            return [m.strip() for m in milestones if m.strip()]
    except Exception:
        logger.warning("Last-resort milestone generation also failed")

    return []


async def _extract_milestones_with_heartbeat(
    *,
    router,
    project: dict[str, Any],
    working_dir: str,
    app,
    chat_id: int,
    stop_request_cache_key: str,
    heartbeat_hook: Callable[[int], Awaitable[None]] | None = None,
) -> list[str]:
    heartbeat = max(
        1,
        int(getattr(cfg, "MILESTONE_EXTRACTION_HEARTBEAT_SECONDS", 20) or 20),
    )
    max_wait = max(
        heartbeat,
        int(getattr(cfg, "MILESTONE_EXTRACTION_MAX_WAIT_SECONDS", 180) or 180),
    )
    pending = asyncio.create_task(
        _extract_milestones(
            router,
            project,
            working_dir=working_dir,
        )
    )
    elapsed = 0
    try:
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(pending), timeout=heartbeat)
            except asyncio.TimeoutError:
                elapsed += heartbeat
                if app.bot_data.get(stop_request_cache_key):
                    raise RuntimeError("STOP_REQUESTED: session stop requested by user")
                if elapsed >= max_wait:
                    raise RuntimeError(
                        f"MILESTONE_EXTRACTION_TIMEOUT: exceeded {max_wait}s"
                    )
                await app.bot.send_message(
                    chat_id,
                    f"\u23f3 Still breaking the plan into milestones ({elapsed}s elapsed)...",
                )
                if heartbeat_hook is not None:
                    with contextlib.suppress(Exception):
                        await heartbeat_hook(elapsed)
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending


def _parse_milestones_fallback(plan: str) -> list[str]:
    """Extract numbered list items from free-form plan text."""
    pattern = re.compile(r"^\s*\d+\.\s+(.+)", re.MULTILINE)
    matches = pattern.findall(plan)
    return [m.strip() for m in matches if m.strip()]


def _slugify(name: str) -> str:
    """Convert a project name to a safe directory/repo slug."""
    slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "project"



