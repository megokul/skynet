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
    create_architecture_state,
    create_learning_event,
    create_node_worker_assignment,
    create_task_strategy,
    create_task,
    create_task_gate_result,
    create_task_orchestration_run,
    create_task_node_event,
    delete_task_gate_results,
    ensure_user,
    get_active_task_graph,
    get_active_architecture_state,
    get_active_prompt_policy,
    get_project,
    list_active_workers,
    list_learning_events,
    list_critic_findings,
    list_graph_nodes,
    list_project_memory,
    list_runtime_trace_events,
    list_task_node_events,
    list_task_gate_results,
    list_projects,
    list_tasks,
    query_code_index,
    supersede_architecture_state,
    update_task_node_worker,
    update_task_status,
    upsert_project_memory,
    upsert_prompt_policy,
    upsert_worker_registry,
)
from gateway import is_worker_available, send_action
from orchestration.arch_critic import evaluate_architecture_refs, load_arch_rules
from orchestration.architect import (
    build_architect_prompt,
    default_architecture_state,
    evaluate_architecture_contract,
    next_architecture_version,
    parse_architecture_state,
)
from orchestration.completion import validate_completion_contract
from orchestration.compression import build_context_bundle
from orchestration.critic import build_review_prompt, is_blocking, parse_critic_response
from orchestration.director import build_director_prompt, default_director_contract, parse_director_contract
from orchestration.failure import (
    FAIL_CONTRACT,
    FAIL_ENVIRONMENT,
    FAIL_STRICT_GATE,
    FAIL_TEST,
    classify_gate_failure,
    classify_generation_error,
)
from orchestration.graph import LoopNode
from orchestration.indexer import index_file
from orchestration.learning import (
    apply_prompt_policy,
    build_conservative_prompt_policy,
    build_pattern_key,
)
from orchestration.loop_controller import ClosedLoopController
from orchestration.openclaw_runner import get_openclaw_runner
from orchestration.trace import format_timeline_lines, load_trace_timeline
from runtime_trace import build_debug_bundle, command_hash, emit_runtime_trace, emit_runtime_trace_async
from orchestration.worker_pool import select_worker

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
_CONTROL_LOOP_PROFILE_LEGACY = "legacy"
_CONTROL_LOOP_PROFILE_V1 = "loop_v1"
_CONTROL_LOOP_PROFILE_V2 = "loop_v2"
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


def _runtime_flow() -> str:
    raw = str(cfg.get_str("SKYNET_LIVE_E2E_FLOW", "") or "").strip().lower()
    if raw in {"telegram_real", "conversation", "direct"}:
        return raw
    return "direct"


async def _emit_runtime(
    *,
    context: ContextTypes.DEFAULT_TYPE | None,
    event: str,
    status: str = "ok",
    level: str = "info",
    user_id: int | None = None,
    project_id: str = "",
    task_id: str = "",
    graph_id: str = "",
    node_key: str = "",
    node_type: str = "",
    phase: str = "",
    stage: str = "",
    gate: str = "",
    worker_id: str = "",
    transport: str = "ssh_first",
    runtime_mode: str = "ssh",
    error_type: str = "",
    error_code: str = "",
    error_message: str = "",
    action_name: str = "",
    working_dir: str = "",
    details: dict[str, Any] | None = None,
    failure_class: str = "",
    mitigation_hint: str = "",
) -> None:
    db = None
    chat_id = ""
    if context is not None and isinstance(getattr(context, "bot_data", None), dict):
        db = context.bot_data.get(KEY_DB)
        chat_id = str(context.bot_data.get("last_chat_id") or "")
    debug_bundle = None
    if status.strip().lower() in {"fail", "failed", "error"}:
        debug_bundle = build_debug_bundle(
            failure_class=failure_class or error_code or "UNKNOWN",
            error_message=error_message,
            causal_chain=[event],
            mitigation_hint=mitigation_hint or "Inspect /trace deep timeline and debug.bundle entries.",
            retry_policy_snapshot={"strict_mode": bool(getattr(cfg, "STRICT_QUALITY_GATES_ENABLED", True))},
        )
    await emit_runtime_trace_async(
        db=db,
        event=event,
        status=status,
        level=level,
        flow=_runtime_flow(),
        project_id=project_id,
        task_id=task_id,
        graph_id=graph_id,
        node_key=node_key,
        node_type=node_type,
        phase=phase,
        stage=stage,
        gate=gate,
        worker_id=worker_id,
        transport=transport,
        runtime_mode=runtime_mode,
        error_type=error_type,
        error_code=error_code,
        error_message=error_message,
        telegram_chat_id=chat_id,
        telegram_user_id=str(user_id or ""),
        action_name=action_name,
        command_hash=command_hash(action_name),
        working_dir=working_dir,
        details=details or {},
        debug_bundle=debug_bundle,
        failure_class=failure_class or error_code,
        mitigation_hint=mitigation_hint,
    )


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
    graph_id = str(state.get("graph_id") or "").strip()
    node_key = str(state.get("node_key") or "").strip()
    node_type = str(state.get("node_type") or "").strip()
    critic_name = str(state.get("critic_name") or "").strip()
    arch_version = str(state.get("arch_version") or "").strip()
    worker_id = str(state.get("worker_id") or "").strip()
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
        if graph_id:
            pipeline_parts.append(f"graph={graph_id}")
        if arch_version:
            pipeline_parts.append(f"arch={arch_version}")
        if node_key:
            pipeline_parts.append(f"node={node_key}")
        if node_type:
            pipeline_parts.append(f"type={node_type}")
        if worker_id:
            pipeline_parts.append(f"worker={worker_id}")
        if critic_name:
            pipeline_parts.append(f"critic={critic_name}")
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
    use_acp = _use_acp_orchestration()
    state: dict[str, Any] = {
        "project_id": project_id,
        "project_name": str(project.get("name") or "").strip(),
        "working_dir": working_dir,
        "strict_mode": strict_mode,
        "transport": str(getattr(cfg, "CODING_TRANSPORT", "auto") or "auto"),
        "run_contract_status": "pending" if strict_mode else "legacy",
        "session_id": "",
        "runtime_mode": (
            str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp")
            if use_acp
            else "ssh"
        ),
        "queue_mode": (
            str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue")
            if use_acp
            else ""
        ),
        "graph_id": "",
        "arch_version": "",
        "node_key": "",
        "node_type": "",
        "worker_id": "",
        "critic_name": "",
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
    graph_id: str | None = None,
    arch_version: str | None = None,
    node_key: str | None = None,
    node_type: str | None = None,
    worker_id: str | None = None,
    critic_name: str | None = None,
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
    if graph_id is not None:
        state["graph_id"] = str(graph_id).strip()
    if arch_version is not None:
        state["arch_version"] = str(arch_version).strip()
    if node_key is not None:
        state["node_key"] = str(node_key).strip()
    if node_type is not None:
        state["node_type"] = str(node_type).strip()
    if worker_id is not None:
        state["worker_id"] = str(worker_id).strip()
    if critic_name is not None:
        state["critic_name"] = str(critic_name).strip()
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


def _control_loop_profile(project: dict[str, Any] | None) -> str:
    raw = str(
        (project or {}).get("control_loop_profile")
        or getattr(cfg, "CONTROL_LOOP_DEFAULT_PROFILE", _CONTROL_LOOP_PROFILE_V2)
        or _CONTROL_LOOP_PROFILE_LEGACY
    ).strip().lower()
    if raw not in {
        _CONTROL_LOOP_PROFILE_LEGACY,
        _CONTROL_LOOP_PROFILE_V1,
        _CONTROL_LOOP_PROFILE_V2,
    }:
        return _CONTROL_LOOP_PROFILE_LEGACY
    return raw


def _effective_control_loop_profile(project: dict[str, Any] | None) -> str:
    if not bool(getattr(cfg, "CONTROL_LOOP_ENABLED", True)):
        return _CONTROL_LOOP_PROFILE_LEGACY
    if bool(getattr(cfg, "CONTROL_LOOP_FORCE_FOR_ALL", False)):
        return _CONTROL_LOOP_PROFILE_V2
    return _control_loop_profile(project)


def _use_control_loop_v1(project: dict[str, Any] | None) -> bool:
    return _effective_control_loop_profile(project) in {
        _CONTROL_LOOP_PROFILE_V1,
        _CONTROL_LOOP_PROFILE_V2,
    }


def _use_control_loop_v2(project: dict[str, Any] | None) -> bool:
    return _effective_control_loop_profile(project) == _CONTROL_LOOP_PROFILE_V2


def _orchestration_mode() -> str:
    return str(cfg.effective_orchestration_mode() or "legacy").strip().lower()


def _use_acp_orchestration() -> bool:
    return _orchestration_mode() == _ORCHESTRATION_MODE_ACP_FIRST


def _uses_claude_ollama(project: dict[str, Any] | None) -> bool:
    return _effective_coding_profile(project) == _CODING_PROFILE_CLAUDE_OLLAMA


def _claude_ollama_stage_enabled() -> bool:
    return bool(getattr(cfg, "CLAUDE_OLLAMA_STAGE_ENABLED", False))


def _filter_stage_chain_by_policy(stage_chain: list[str]) -> tuple[list[str], list[str]]:
    filtered: list[str] = []
    disabled: list[str] = []
    for stage in stage_chain:
        if stage == "claude_ollama" and not _claude_ollama_stage_enabled():
            disabled.append(stage)
            continue
        filtered.append(stage)
    return filtered, disabled


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
    include_policy_disabled: bool = False,
) -> list[str]:
    if _use_control_loop_v1(project):
        raw_chain = ["codex"]
        if include_policy_disabled:
            return raw_chain
        filtered_chain, _ = _filter_stage_chain_by_policy(raw_chain)
        return filtered_chain
    effective = _effective_coding_profile(project)
    raw_chain: list[str] = []
    if effective == _CODING_PROFILE_CODEX_PRIMARY:
        if _use_acp_orchestration():
            raw_chain = _parse_coding_fallback_chain(
                getattr(cfg, "OPENCLAW_STAGE_CHAIN", cfg.CODING_FALLBACK_CHAIN),
            )
        else:
            raw_chain = _parse_coding_fallback_chain(cfg.CODING_FALLBACK_CHAIN)
    elif effective == _CODING_PROFILE_CLAUDE_OLLAMA:
        raw_chain = ["claude_ollama"]
    elif include_legacy:
        raw_chain = ["claude_ollama"]
    else:
        raw_chain = []
    if include_policy_disabled:
        return raw_chain
    filtered_chain, _ = _filter_stage_chain_by_policy(raw_chain)
    return filtered_chain


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
    if _use_control_loop_v1(project):
        return True
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
    working_dir: str | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Validate coding prerequisites before milestone execution.

    For codex-primary/claude_ollama profiles, inspect coding agent telemetry and
    ensure at least one stage from the configured chain is available.
    """
    raw_stage_chain = _build_coding_stage_chain(project, include_policy_disabled=True)
    stage_chain, disabled_stages = _filter_stage_chain_by_policy(raw_stage_chain)
    policy_note = ""
    if disabled_stages:
        policy_note = (
            "Policy-disabled stage(s): "
            + ",".join(disabled_stages)
            + "."
        )
    if raw_stage_chain and not stage_chain:
        disabled_hint = policy_note or f"Configured chain: {','.join(raw_stage_chain)}"
        return False, f"No enabled coding stages after policy filtering. {disabled_hint}", raw_stage_chain
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
            if policy_note:
                detail = f"{policy_note} {detail}".strip()
            return (
                False,
                f"No control-plane coding agents available for chain {','.join(stage_chain)}. {detail}",
                stage_chain,
            )
        if available_chain != stage_chain:
            info = f"Filtered unavailable control-plane stages. Active chain: {','.join(available_chain)}."
            if policy_note:
                info = f"{policy_note} {info}"
            return (
                True,
                info,
                available_chain,
            )
        return True, policy_note, available_chain

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
        if policy_note:
            detail = f"{policy_note} {detail}".strip()
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
            info = f"Primary stage {first_stage} unavailable; continuing with fallback {fallback_stage}."
            if policy_note:
                info = f"{policy_note} {info}"
            return (
                True,
                info,
                filtered_chain,
            )

    if any_known and filtered_chain != stage_chain:
        info = f"Filtered unavailable coding stages. Active chain: {','.join(filtered_chain)}."
        if policy_note:
            info = f"{policy_note} {info}"
        return (
            True,
            info,
            filtered_chain,
        )

    if _use_control_loop_v1(project) and working_dir:
        runtime_probe = "node -v" if _project_prefers_node(str(project.get("project_type") or "")) else "python -V"
        try:
            runtime_result = await send_action(
                "exec_command",
                {"working_dir": working_dir, "command": runtime_probe},
                timeout=20,
                confirmed=True,
            )
        except Exception as exc:
            return False, f"Runtime preflight failed ({runtime_probe}): {type(exc).__name__}: {exc}", filtered_chain
        if runtime_result.get("status") == "error" or _action_exit_code(runtime_result) != 0:
            detail = _action_error_text(runtime_result, "exec_command")
            return False, f"Runtime preflight failed ({runtime_probe}): {detail}", filtered_chain

        write_probe = (
            "python -c \"from pathlib import Path; p=Path('.skynet_write_probe'); "
            "p.write_text('ok', encoding='utf-8'); p.unlink(missing_ok=True)\""
        )
        try:
            write_result = await send_action(
                "exec_command",
                {"working_dir": working_dir, "command": write_probe},
                timeout=20,
                confirmed=True,
            )
        except Exception as exc:
            return False, f"CODEX_WRITE_BLOCKED: {type(exc).__name__}: {exc}", filtered_chain
        if write_result.get("status") == "error" or _action_exit_code(write_result) != 0:
            detail = _action_error_text(write_result, "exec_command")
            return False, f"CODEX_WRITE_BLOCKED: {detail}", filtered_chain

    return True, policy_note, filtered_chain


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
        await emit_runtime_trace_async(
            db=db,
            event="coding.stage.start",
            status="start",
            flow=_runtime_flow(),
            project_id=str(project.get("id") or ""),
            task_id=str(task_id or ""),
            phase=str(label_prefix or ""),
            stage=stage_name,
            worker_id=str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
            transport="ssh_first",
            runtime_mode=("acp" if use_acp else "ssh"),
            action_name="run_coding_agent",
            command_hash=command_hash(prompt),
            working_dir=working_dir,
            details={"stage_index": idx, "stage_total": len(stage_chain)},
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
            heartbeat_runtime = (
                str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp")
                if use_acp
                else "ssh"
            )
            heartbeat_queue_mode = queue_mode if use_acp else ""

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
                    runtime=heartbeat_runtime,
                    queue_mode=heartbeat_queue_mode,
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
            if reason.startswith("STOP_REQUESTED:"):
                raise
            if reason.startswith("WAIT_TIMEOUT:"):
                failure_reason = reason
                return_code = 124
            else:
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
            await emit_runtime_trace_async(
                db=db,
                event="coding.stage.end",
                status="ok",
                flow=_runtime_flow(),
                project_id=str(project.get("id") or ""),
                task_id=str(task_id or ""),
                phase=str(label_prefix or ""),
                stage=stage_name,
                worker_id=str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
                transport="ssh_first",
                runtime_mode=("acp" if use_acp else "ssh"),
                action_name="run_coding_agent",
                command_hash=command_hash(prompt),
                working_dir=working_dir,
                details={"returncode": return_code, "files_written": len(written)},
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
        await emit_runtime_trace_async(
            db=db,
            event="coding.stage.end",
            status="fail",
            level="error",
            flow=_runtime_flow(),
            project_id=str(project.get("id") or ""),
            task_id=str(task_id or ""),
            phase=str(label_prefix or ""),
            stage=stage_name,
            worker_id=str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
            transport="ssh_first",
            runtime_mode=("acp" if use_acp else "ssh"),
            error_type="StageFailure",
            error_code="GENERATION_FAILED",
            error_message=failure_excerpt,
            action_name="run_coding_agent",
            command_hash=command_hash(prompt),
            working_dir=working_dir,
            failure_class="GENERATION_FAILED",
            mitigation_hint="Inspect stage output and causal chain via /trace deep.",
            details={
                "returncode": return_code,
                "attempted_stages": list(attempted_stages),
            },
            debug_bundle=build_debug_bundle(
                failure_class="GENERATION_FAILED",
                error_message=failure_excerpt,
                causal_chain=[f"coding.stage.start:{stage_name}", f"coding.stage.end:{stage_name}"],
                last_success_event=(
                    f"coding.stage.end:{attempted_stages[-2]}"
                    if len(attempted_stages) > 1
                    else ""
                ),
                stdout_tail=str(inner.get("stdout") or ""),
                stderr_tail=str(inner.get("stderr") or failure_excerpt),
                files_touched=[str(path) for path in written if str(path).strip()],
                mitigation_hint="Validate worker writability, prompt quality, and command output.",
                retry_policy_snapshot={"stage_index": idx, "stage_total": len(stage_chain)},
            ),
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
    app=None,
    chat_id: int | None = None,
    user_id: int | None = None,
    heartbeat_hook: Callable[[int], Awaitable[None]] | None = None,
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
            if app is not None and isinstance(chat_id, int):
                result = await _send_action_with_heartbeat(
                    app=app,
                    chat_id=chat_id,
                    user_id=user_id,
                    action="run_coding_agent",
                    params=payload,
                    timeout=1800,
                    label=f"quality fix via {stage_name}",
                    max_wait_seconds=max(
                        1,
                        int(getattr(cfg, "CODING_AGENT_MAX_WAIT_SECONDS", 900) or 900),
                    ),
                    confirmed=True,
                    heartbeat_hook=heartbeat_hook,
                )
            else:
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
    app=None,
    chat_id: int | None = None,
    user_id: int | None = None,
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
            level = "error" if status.strip().lower() in {"failed", "error"} else "info"
            await emit_runtime_trace_async(
                db=db,
                event="coding.gate.update",
                status=("fail" if status.strip().lower() in {"failed", "error"} else status.strip().lower() or "ok"),
                level=level,
                flow=_runtime_flow(),
                project_id=str(project.get("id") or ""),
                task_id=str(task_id),
                phase="quality_gates",
                gate=gate_name,
                transport="ssh_first",
                runtime_mode="ssh",
                error_code=("STRICT_GATES_FAILED" if status.strip().lower() in {"failed", "error"} else ""),
                error_message=(summary if status.strip().lower() in {"failed", "error"} else ""),
                action_name=command.split(" ", 1)[0] if command else "",
                command_hash=command_hash(command),
                working_dir=working_dir,
                failure_class=("STRICT_GATE_FAILED" if status.strip().lower() in {"failed", "error"} else ""),
                mitigation_hint=(
                    "Inspect gate command output and rerun after fixing blocker."
                    if status.strip().lower() in {"failed", "error"}
                    else ""
                ),
                details={
                    "gate_name": gate_name,
                    "status": status,
                    "summary": summary[:240],
                    "command": command,
                    "attempt": attempt,
                },
            )
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
            async def _quality_fix_heartbeat(elapsed: int) -> None:
                with contextlib.suppress(Exception):
                    await _emit_gate_event(
                        "run_contract",
                        "running",
                        summary=f"Auto-fix in progress ({elapsed}s elapsed)",
                        command="run_coding_agent",
                    )
            try:
                fix_written_files = await _run_quality_fix_pass(
                    project=project,
                    milestone_text=milestone_text,
                    working_dir=working_dir,
                    failing_gates=failed_gates,
                    stage_chain=stage_chain,
                    app=app,
                    chat_id=chat_id,
                    user_id=user_id,
                    heartbeat_hook=_quality_fix_heartbeat,
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
    """User tapped start coding - ask GitHub/folder setup preference."""
    await update.callback_query.answer()
    await _emit_runtime(
        context=context,
        event="coding.session.request",
        status="start",
        user_id=update.effective_user.id if update.effective_user else None,
        project_id=str(context.user_data.get(_PROJECT_ID_KEY) or ""),
        phase="setup",
    )

    project_id = context.user_data.get(_PROJECT_ID_KEY)
    if not project_id:
        await _emit_runtime(
            context=context,
            event="coding.session.request",
            status="fail",
            user_id=update.effective_user.id if update.effective_user else None,
            phase="setup",
            error_code="NO_ACTIVE_PROJECT",
            error_message="No active project found before Start Coding.",
            failure_class="ENVIRONMENT_FAILED",
            mitigation_hint="Create or select a project before starting coding.",
        )
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
    await _emit_runtime(
        context=context,
        event="coding.session.request",
        status="ok",
        user_id=update.effective_user.id if update.effective_user else None,
        project_id=str(project_id),
        phase="setup",
        details={"awaiting_choice": True},
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
        await _emit_runtime(
            context=context,
            event="coding.loop.start",
            status="fail",
            user_id=user_id,
            project_id=str(project.get("id") or ""),
            phase="setup",
            error_code="LOOP_ALREADY_RUNNING",
            error_message="A coding session is already running for this user.",
            failure_class="ENVIRONMENT_FAILED",
        )
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
    await _emit_runtime(
        context=context,
        event="coding.loop.start",
        status="start",
        user_id=user_id,
        project_id=str(project.get("id") or ""),
        phase="setup",
        transport="ssh_first",
        runtime_mode="ssh",
        working_dir=working_dir,
        action_name="start_coding_loop",
        details={"do_github": bool(do_github), "strict_mode": _is_strict_project(project)},
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
        await _emit_runtime(
            context=context,
            event="coding.github.choice",
            status="fail",
            user_id=user_id,
            phase="setup",
            error_code="SESSION_EXPIRED",
            error_message="Coding GitHub choice callback missing project id.",
            failure_class="ENVIRONMENT_FAILED",
        )
        await update.callback_query.message.reply_text("Session expired Ã¢â‚¬â€ start over.")
        return

    db = context.bot_data.get(KEY_DB)
    project = await get_project(db, project_id)
    if not project:
        await _emit_runtime(
            context=context,
            event="coding.github.choice",
            status="fail",
            user_id=user_id,
            project_id=str(project_id),
            phase="setup",
            error_code="PROJECT_NOT_FOUND",
            error_message="Project row not found during coding GitHub choice.",
            failure_class="ENVIRONMENT_FAILED",
        )
        await update.callback_query.message.reply_text("Project not found in database.")
        return

    do_github = (cb_data == CB_CODING_GITHUB_YES)
    await _emit_runtime(
        context=context,
        event="coding.github.choice",
        status="ok",
        user_id=user_id,
        project_id=str(project_id),
        phase="setup",
        details={"do_github": bool(do_github)},
    )

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
        await _emit_runtime(
            context=context,
            event="coding.retry.request",
            status="fail",
            user_id=update.effective_user.id if update.effective_user else None,
            phase="setup",
            error_code="INVALID_RETRY_REQUEST",
            error_message="Retry callback missing project id.",
            failure_class="ENVIRONMENT_FAILED",
        )
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
        await _emit_runtime(
            context=context,
            event="coding.retry.request",
            status="fail",
            user_id=tg_user.id,
            project_id=str(project_id),
            phase="setup",
            error_code="ACCESS_DENIED",
            error_message="Retry link invalid or inaccessible project.",
            failure_class="ENVIRONMENT_FAILED",
        )
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
        await _emit_runtime(
            context=context,
            event="coding.retry.request",
            status="ok",
            user_id=tg_user.id,
            project_id=str(project_id),
            phase="setup",
            details={"reused_preference": True, "do_github": bool(remembered_pref)},
        )
        return

    context.user_data[_CODING_PID_KEY] = project_id
    await update.callback_query.message.reply_text(
        "Should I set up a GitHub repo and project folder on your laptop?",
        reply_markup=coding_github_setup(),
    )
    await _emit_runtime(
        context=context,
        event="coding.retry.request",
        status="ok",
        user_id=tg_user.id,
        project_id=str(project_id),
        phase="setup",
        details={"reused_preference": False},
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
        await _emit_runtime(
            context=context,
            event="milestone.approval",
            status="ok",
            user_id=user_id,
            phase="milestone_review",
            details={"decision": "approve"},
        )
    else:
        logger.debug(
            "Ignoring stale milestone approval callback for user_id=%s (no active event).",
            user_id,
        )
        await _emit_runtime(
            context=context,
            event="milestone.approval",
            status="skip",
            user_id=user_id,
            phase="milestone_review",
            details={"decision": "approve", "reason": "stale_callback"},
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
        await _emit_runtime(
            context=context,
            event="milestone.approval",
            status="ok",
            user_id=user_id,
            phase="milestone_review",
            details={"decision": "skip"},
        )
    else:
        await _emit_runtime(
            context=context,
            event="milestone.approval",
            status="skip",
            user_id=user_id,
            phase="milestone_review",
            details={"decision": "skip", "reason": "stale_callback"},
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
        await _emit_runtime(
            context=context,
            event="coding.stop.request",
            status="ok",
            user_id=user_id,
            phase="finalization",
            details={"active_event": True},
        )
        return

    loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
    active_loop = context.bot_data.get(loop_key)
    if active_loop and not active_loop.done():
        await update.callback_query.message.reply_text(
            "Stopping current milestone execution... this may take a few seconds."
        )
        await _emit_runtime(
            context=context,
            event="coding.stop.request",
            status="ok",
            user_id=user_id,
            phase="finalization",
            details={"active_event": False, "active_loop": True},
        )
    else:
        context.bot_data.pop(_stop_request_key(user_id), None)
        await update.callback_query.message.reply_text(
            "No active coding session to stop."
        )
        await _emit_runtime(
            context=context,
            event="coding.stop.request",
            status="skip",
            user_id=user_id,
            phase="finalization",
            details={"active_event": False, "active_loop": False},
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
        graph_id = str(tracker_state.get("graph_id") or "").strip()
        arch_version = str(tracker_state.get("arch_version") or "").strip()
        node_key = str(tracker_state.get("node_key") or "").strip()
        node_type = str(tracker_state.get("node_type") or "").strip()
        worker_id = str(tracker_state.get("worker_id") or "").strip()
        critic_name = str(tracker_state.get("critic_name") or "").strip()
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
        if graph_id:
            tracker_block += f"\nGraph: {graph_id}"
        if arch_version:
            tracker_block += f"\nArchitecture Version: {arch_version}"
        if node_key:
            tracker_block += f"\nNode: {node_key}"
        if node_type:
            tracker_block += f"\nNode Type: {node_type}"
        if worker_id:
            tracker_block += f"\nWorker: {worker_id}"
        if critic_name:
            tracker_block += f"\nCritic: {critic_name}"
        tracker_block += f"\nTransport: {transport}"
        tracker_block += f"\nRun contract: {run_contract}"
    elif is_running:
        estimated = _tracker_estimate_percent_from_tasks(tasks)
        tracker_block = (
            f"\n\nProgress {_render_progress_bar(estimated, _tracker_bar_width())} {estimated}%\n"
            "Phase: estimating from task states"
        )

    graph_block = ""
    active_graph = await get_active_task_graph(db, project_id=project["id"])
    if active_graph:
        graph_id = int(active_graph.get("id") or 0)
        graph_status = str(active_graph.get("status") or "active")
        graph_nodes = await list_graph_nodes(db, graph_id=graph_id)
        running_node = next(
            (row for row in graph_nodes if str(row.get("status") or "") == "running"),
            None,
        )
        queued_count = sum(1 for row in graph_nodes if str(row.get("status") or "") == "queued")
        done_count = sum(1 for row in graph_nodes if str(row.get("status") or "") == "done")
        graph_block = (
            f"\n\nLoop Graph: <code>{graph_id}</code>"
            f"\nGraph Status: {graph_status}"
            f"\nNodes: done={done_count}, queued={queued_count}, total={len(graph_nodes)}"
        )
        if running_node:
            graph_block += (
                f"\nActive Node: {running_node.get('node_key')} "
                f"({running_node.get('node_type')}, attempt={running_node.get('attempt_count')})"
            )
        arch_state = await get_active_architecture_state(db, project_id=project["id"])
        if arch_state:
            graph_block += f"\nActive Architecture: v{int(arch_state.get('version') or 0)}"
        learning_policy = await get_active_prompt_policy(
            db,
            scope="project",
            project_id=project["id"],
            policy_kind="repair",
        )
        if learning_policy:
            graph_block += "\nLearning Policy: active (repair)"

    text = (
        f"<b>{project['name']}</b> - {project['project_type']}\n"
        f"Folder: <code>{working_dir}</code>\n"
        f"Status: {project['status']}{status_note}\n\n"
        f"{task_lines}{tracker_block}{graph_block}"
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


async def trace_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/trace - show recent loop timeline events; `/trace deep` adds runtime debug bundle context."""
    db = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user
    user = await ensure_user(
        db,
        telegram_user_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name or "",
    )
    await _emit_runtime(
        context=context,
        event="trace.command",
        status="start",
        user_id=tg_user.id,
        phase="trace",
        details={"deep": bool(context.args and context.args[0].strip().lower() == "deep")},
    )
    projects = await list_projects(db, user_id=user["id"])
    if not projects:
        await update.message.reply_text("No projects yet.")
        await _emit_runtime(
            context=context,
            event="trace.command",
            status="skip",
            user_id=tg_user.id,
            phase="trace",
            details={"reason": "no_projects"},
        )
        return
    project = projects[0]
    active_graph = await get_active_task_graph(db, project_id=project["id"])
    if not active_graph:
        await update.message.reply_text("No active control-loop graph for this project.")
        await _emit_runtime(
            context=context,
            event="trace.command",
            status="skip",
            user_id=tg_user.id,
            project_id=str(project["id"]),
            phase="trace",
            details={"reason": "no_active_graph"},
        )
        return
    graph_id = int(active_graph.get("id") or 0)
    events = await load_trace_timeline(
        db,
        graph_id=graph_id,
        limit=30,
    )
    if not events:
        await update.message.reply_text(f"Graph {graph_id} has no trace events yet.")
        await _emit_runtime(
            context=context,
            event="trace.command",
            status="skip",
            user_id=tg_user.id,
            project_id=str(project["id"]),
            graph_id=str(graph_id),
            phase="trace",
            details={"reason": "no_events"},
        )
        return
    lines = format_timeline_lines(events)
    latest_failure = next(
        (
            row
            for row in reversed(events)
            if str(row.get("failure_type") or "").strip()
        ),
        None,
    )
    repair_events = [
        row for row in events if "repair" in str(row.get("event_type") or "").lower()
    ]
    header = (
        f"Graph: <code>{graph_id}</code>\n"
        f"Status: {html_mod.escape(str(active_graph.get('status') or 'active'))}\n"
        f"Events shown: {len(lines)}"
    )
    if latest_failure:
        header += (
            f"\nLatest failure: {html_mod.escape(str(latest_failure.get('failure_type') or 'unknown'))}"
            f" at #{int(latest_failure.get('id') or 0)}"
        )
    if repair_events:
        header += f"\nRepairs observed: {len(repair_events)}"
    body = "\n".join(lines[-30:])
    deep = bool(context.args and len(context.args) > 0 and str(context.args[0]).strip().lower() == "deep")
    if not deep:
        await update.message.reply_text(
            f"{header}\n\n<pre>{html_mod.escape(body)}</pre>",
            parse_mode="HTML",
        )
        await _emit_runtime(
            context=context,
            event="trace.command",
            status="ok",
            user_id=tg_user.id,
            project_id=str(project["id"]),
            graph_id=str(graph_id),
            phase="trace",
            details={"deep": False, "events": len(events)},
        )
        return

    runtime_rows = await list_runtime_trace_events(
        db,
        project_id=str(project["id"]),
        graph_id=str(graph_id),
        limit=80,
    )
    recent_rows = runtime_rows[-30:] if len(runtime_rows) > 30 else runtime_rows
    runtime_lines: list[str] = []
    for row in recent_rows:
        event_name = str(row.get("event") or "").strip() or "event"
        status = str(row.get("status") or "").strip() or "ok"
        stage_name = str(row.get("stage") or "").strip()
        gate_name = str(row.get("gate") or "").strip()
        error_code = str(row.get("error_code") or "").strip()
        msg = str(row.get("error_message") or "").strip()
        line = f"[{row.get('id')}] {event_name} ({status})"
        if stage_name:
            line += f" stage={stage_name}"
        if gate_name:
            line += f" gate={gate_name}"
        if error_code:
            line += f" code={error_code}"
        if msg:
            line += f" msg={msg[:120]}"
        runtime_lines.append(line)
    latest_debug_bundle = next(
        (row for row in reversed(runtime_rows) if str(row.get("event") or "").strip() == "debug.bundle"),
        None,
    )
    debug_digest = ""
    if latest_debug_bundle:
        payload = latest_debug_bundle.get("payload") or {}
        details = payload.get("details") if isinstance(payload, dict) else {}
        bundle = details.get("debug_bundle") if isinstance(details, dict) else {}
        if isinstance(bundle, dict):
            failure_class = str(bundle.get("failure_class") or "UNKNOWN")
            mitigation_hint = str(bundle.get("mitigation_hint") or "")
            causal_len = len(bundle.get("causal_chain") or [])
            debug_digest = (
                f"Latest debug bundle: class={failure_class}, causal_chain={causal_len}, "
                f"mitigation={mitigation_hint[:120]}"
            )

    deep_body = "\n".join(runtime_lines) if runtime_lines else "No runtime trace rows."
    deep_header = (
        f"{header}\n"
        f"Runtime events: {len(runtime_rows)}"
        + (f"\n{debug_digest}" if debug_digest else "")
    )
    await update.message.reply_text(
        f"{deep_header}\n\n<pre>{html_mod.escape(body)}</pre>\n\n<pre>{html_mod.escape(deep_body)}</pre>",
        parse_mode="HTML",
    )
    await _emit_runtime(
        context=context,
        event="trace.command",
        status="ok",
        user_id=tg_user.id,
        project_id=str(project["id"]),
        graph_id=str(graph_id),
        phase="trace",
        details={"deep": True, "timeline_events": len(events), "runtime_events": len(runtime_rows)},
    )


def _control_loop_stage_chain(stage_chain: list[str]) -> list[str]:
    filtered = [stage for stage in stage_chain if stage == "codex"]
    return filtered or ["codex"]


def _control_loop_work_prompt(
    *,
    project: dict[str, Any],
    milestone_text: str,
    working_dir: str,
) -> str:
    preferred_entrypoint = f"{str(project.get('name') or 'main').strip().lower().replace(' ', '_')}.py"
    return (
        f"Project: {project['name']} ({project['project_type']})\n"
        f"Working directory: {working_dir}\n\n"
        f"Task:\n{milestone_text}\n\n"
        "Implement this task completely by writing files directly in the working directory.\n"
        "This is an implementation task, not a planning task.\n"
        "Requirements:\n"
        f"- Include {_RUN_CONTRACT_FILE} with a valid command.\n"
        f"- Prefer entrypoint file `{preferred_entrypoint}` unless an existing entrypoint already exists.\n"
        "- Create or update tests needed to validate behavior.\n"
        "- Write complete files and ensure they run.\n"
        "- Do not ask clarifying questions.\n"
        "- Do NOT return architecture plans, checklists, or mermaid diagrams."
    )


def _control_loop_repair_prompt(
    *,
    project: dict[str, Any],
    working_dir: str,
    findings: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    for finding in findings:
        message = str(finding.get("message") or "").strip()
        severity = str(finding.get("severity") or "medium").strip()
        code = str(finding.get("code") or "").strip()
        files = ", ".join(str(item) for item in (finding.get("files") or []) if str(item).strip())
        suggested = str(finding.get("suggested_fix") or "").strip()
        line = f"- [{severity}] {code}: {message}"
        if files:
            line += f" (files: {files})"
        if suggested:
            line += f" | fix: {suggested}"
        lines.append(line)
    finding_block = "\n".join(lines) if lines else "- Fix all blocking critic findings."
    return (
        f"Project: {project['name']} ({project['project_type']})\n"
        f"Working directory: {working_dir}\n\n"
        "Repair task generated by critic findings.\n"
        "Apply minimal code edits to resolve these issues:\n"
        f"{finding_block}\n\n"
        "Requirements:\n"
        "- Keep existing behavior intact unless findings require changes.\n"
        "- Ensure lint/tests/smoke pass in strict gates.\n"
        "- Return complete updated files only.\n"
        "- Do NOT return architecture plans, checklists, or mermaid diagrams."
    )


def _gate_rows_summary(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows:
        gate_name = str(row.get("gate_name") or "").strip()
        status = str(row.get("status") or "").strip()
        summary = str(row.get("summary") or "").strip()
        if not gate_name:
            continue
        snippet = f"{gate_name}:{status}"
        if summary:
            snippet += f" ({summary[:120]})"
        parts.append(snippet)
    return "; ".join(parts)


async def _run_control_loop_v1(
    *,
    app,
    chat_id: int,
    user_id: int,
    db,
    project: dict[str, Any],
    milestones: list[str],
    working_dir: str,
    strict_mode: bool,
    active_stage_chain: list[str],
    run_files_cache_key: str,
    run_contract_cache_key: str,
    stop_request_cache_key: str,
    update_tracker: Callable[..., Awaitable[None]],
    finalize_tracker: Callable[..., Awaitable[None]],
) -> None:
    completion_contract = dict(project.get("_loop_success_contract") or {})
    if bool(getattr(cfg, "CONTROL_LOOP_COMPLETION_CONTRACT_REQUIRED", True)):
        required_artifacts = [
            str(item).strip()
            for item in (completion_contract.get("required_artifacts") or [])
            if str(item).strip()
        ]
        if _RUN_CONTRACT_FILE not in required_artifacts:
            required_artifacts.append(_RUN_CONTRACT_FILE)
        completion_contract["required_artifacts"] = required_artifacts
    else:
        completion_contract = {"required_artifacts": []}
    controller = ClosedLoopController(
        db=db,
        project_id=str(project["id"]),
        goal=str(project.get("description") or project.get("name") or "").strip(),
        milestones=milestones,
        repair_retries=max(0, int(getattr(cfg, "CONTROL_LOOP_REPAIR_RETRIES", 1) or 1)),
        max_parallel_nodes=max(1, int(getattr(cfg, "CONTROL_LOOP_MAX_PARALLEL_NODES", 2) or 2)),
        memory_enabled=bool(getattr(cfg, "CONTROL_LOOP_MEMORY_ENABLED", True)),
        max_iterations=max(1, int(getattr(cfg, "CONTROL_LOOP_BUDGET_MAX_ITERATIONS", 40) or 40)),
        max_runtime_seconds=max(60, int(getattr(cfg, "CONTROL_LOOP_BUDGET_MAX_RUNTIME_SECONDS", 3600) or 3600)),
        max_repairs=max(0, int(getattr(cfg, "CONTROL_LOOP_BUDGET_MAX_REPAIRS", 1) or 1)),
        max_tokens=max(1000, int(getattr(cfg, "CONTROL_LOOP_BUDGET_MAX_TOKENS", 250000) or 250000)),
        deadlock_idle_ticks=max(1, int(getattr(cfg, "CONTROL_LOOP_DEADLOCK_IDLE_TICKS", 3) or 3)),
        success_contract=completion_contract,
        node_specs=list(project.get("_loop_node_specs") or []),
    )
    graph_id = await controller.bootstrap(planner_summary=str(project.get("description") or ""))
    loop_v2_enabled = _use_control_loop_v2(project)
    director_contract = default_director_contract(
        goal=str(project.get("description") or project.get("name") or "").strip()
    )
    architecture_state = await get_active_architecture_state(
        db,
        project_id=str(project["id"]),
    )
    architecture_version = str((architecture_state or {}).get("version") or "")
    active_worker_id = str(
        getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "worker-primary") or "worker-primary"
    ).strip()
    learning_policy: dict[str, Any] | None = None

    if loop_v2_enabled and bool(getattr(cfg, "CONTROL_LOOP_DIRECTOR_ENABLED", True)):
        await update_tracker(
            phase="director",
            phase_detail="Building director contract",
            graph_id=str(graph_id),
            arch_version=architecture_version,
            setup_progress=0.7,
        )
        director_prompt = build_director_prompt(
            project_name=str(project.get("name") or ""),
            project_type=str(project.get("project_type") or ""),
            goal=str(project.get("description") or project.get("name") or ""),
            constraints=[
                "SSH-first transport",
                "Codex-only coding stage",
                "Strict quality gates mandatory",
            ],
            memory_snapshot={
                "project_id": str(project.get("id") or ""),
                "quality_profile": str(project.get("quality_profile") or ""),
            },
        )
        try:
            result = await send_action(
                "run_coding_agent",
                {
                    "agent": str(getattr(cfg, "CONTROL_LOOP_PLANNER_AGENT", "codex") or "codex"),
                    "backend": "auto",
                    "prompt": director_prompt,
                    "working_dir": working_dir,
                    "timeout_seconds": max(
                        30,
                        int(getattr(cfg, "CONTROL_LOOP_DIRECTOR_TIMEOUT_SECONDS", 120) or 120),
                    ),
                },
                timeout=max(30, int(getattr(cfg, "CONTROL_LOOP_DIRECTOR_TIMEOUT_SECONDS", 120) or 120)),
                confirmed=True,
            )
            if result.get("status") != "error" and _action_exit_code(result) == 0:
                raw_contract = str(_action_inner_result(result).get("stdout") or "").strip()
                director_contract = parse_director_contract(raw_contract)
                await create_task_node_event(
                    db,
                    graph_id=int(graph_id),
                    node_id=None,
                    node_key="director",
                    event_type="director.done",
                    status="done",
                    agent="codex",
                    stage="director",
                    details={"objective": str(director_contract.get("objective") or "")[:160]},
                )
            else:
                raise RuntimeError(_action_error_text(result, "run_coding_agent"))
        except Exception as exc:
            await create_task_node_event(
                db,
                graph_id=int(graph_id),
                node_id=None,
                node_key="director",
                event_type="director.failed",
                status="failed",
                agent="codex",
                stage="director",
                failure_type=FAIL_ENVIRONMENT,
                details={"error": str(exc)[:220], "fallback": "default_director_contract"},
            )
        await upsert_project_memory(
            db,
            project_id=str(project["id"]),
            tier="decisions",
            memory_key="director_contract",
            memory_value=director_contract,
            source_node_id=None,
        )

    if loop_v2_enabled and bool(getattr(cfg, "CONTROL_LOOP_ARCHITECT_ENABLED", True)):
        await update_tracker(
            phase="architect",
            phase_detail="Refreshing architecture state",
            graph_id=str(graph_id),
            arch_version=architecture_version,
            setup_progress=0.85,
        )
        architect_prompt = build_architect_prompt(
            project_name=str(project.get("name") or ""),
            goal=str(project.get("description") or project.get("name") or ""),
            director_contract=director_contract,
            previous_state=architecture_state,
            index_summary=[],
        )
        parsed_arch_state: dict[str, Any] | None = None
        try:
            result = await send_action(
                "run_coding_agent",
                {
                    "agent": str(getattr(cfg, "CONTROL_LOOP_PLANNER_AGENT", "codex") or "codex"),
                    "backend": "auto",
                    "prompt": architect_prompt,
                    "working_dir": working_dir,
                    "timeout_seconds": max(
                        30,
                        int(getattr(cfg, "CONTROL_LOOP_ARCHITECT_TIMEOUT_SECONDS", 180) or 180),
                    ),
                },
                timeout=max(30, int(getattr(cfg, "CONTROL_LOOP_ARCHITECT_TIMEOUT_SECONDS", 180) or 180)),
                confirmed=True,
            )
            if result.get("status") != "error" and _action_exit_code(result) == 0:
                raw_state = str(_action_inner_result(result).get("stdout") or "").strip()
                parsed_arch_state = parse_architecture_state(raw_state)
            else:
                raise RuntimeError(_action_error_text(result, "run_coding_agent"))
        except Exception as exc:
            parsed_arch_state = default_architecture_state(
                goal=str(project.get("description") or project.get("name") or "")
            )
            await create_task_node_event(
                db,
                graph_id=int(graph_id),
                node_id=None,
                node_key="architect",
                event_type="architect.failed",
                status="failed",
                agent="codex",
                stage="architect",
                failure_type=FAIL_ENVIRONMENT,
                details={"error": str(exc)[:220], "fallback": "default_architecture_state"},
            )
        if parsed_arch_state is not None:
            prev_version = int((architecture_state or {}).get("version", 0) or 0)
            next_version = next_architecture_version(architecture_state)
            if prev_version > 0:
                await supersede_architecture_state(
                    db,
                    project_id=str(project["id"]),
                    previous_version=prev_version,
                )
            architecture_state = await create_architecture_state(
                db,
                project_id=str(project["id"]),
                version=next_version,
                status="active",
                components=list(parsed_arch_state.get("components") or []),
                interfaces=list(parsed_arch_state.get("interfaces") or []),
                boundaries=list(parsed_arch_state.get("boundaries") or []),
                data_flows=list(parsed_arch_state.get("data_flows") or []),
                constraints=list(parsed_arch_state.get("constraints") or []),
                adr_summary=str(parsed_arch_state.get("adr_summary") or ""),
                created_by="architect",
            )
            architecture_version = str((architecture_state or {}).get("version") or "")
            await create_task_node_event(
                db,
                graph_id=int(graph_id),
                node_id=None,
                node_key="architect",
                event_type="architect.done",
                status="done",
                agent="codex",
                stage="architect",
                details={"version": architecture_version},
            )
            await upsert_project_memory(
                db,
                project_id=str(project["id"]),
                tier="decisions",
                memory_key="architecture_state",
                memory_value={
                    "version": int(architecture_version or 0),
                    "adr_summary": str((architecture_state or {}).get("adr_summary") or ""),
                },
                source_node_id=None,
            )

    if loop_v2_enabled and bool(getattr(cfg, "CONTROL_LOOP_WORKER_POOL_ENABLED", True)):
        await upsert_worker_registry(
            db,
            worker_id=active_worker_id,
            label="Primary SSH Worker",
            transport="ssh",
            endpoint={"host": "ssh-tunnel"},
            capabilities=["code", "test", "lint", "deps", "build", "docker", "terraform"],
            status="active",
            priority=200,
        )
    strategy_payload = project.get("_loop_execution_strategy")
    if isinstance(strategy_payload, dict):
        await create_task_strategy(
            db,
            graph_id=int(graph_id),
            parallel_lanes=list(
                (project.get("_loop_parallel_lanes") or strategy_payload.get("parallel_lanes") or [])
            ),
            risk_assessment=list(
                (project.get("_loop_risk_assessment") or strategy_payload.get("risk_assessment") or [])
            ),
            execution_strategy=strategy_payload,
        )

    execution_stage_chain = _control_loop_stage_chain(active_stage_chain)
    total = len(milestones)
    successful_milestones = 0
    failed_milestones = 0
    skipped_milestones = 0
    all_written_files: list[str] = []
    work_context: dict[str, dict[str, Any]] = {}
    last_valid_run_contract: dict[str, Any] | None = None

    async def _trace_event(
        *,
        node: LoopNode | None,
        event_type: str,
        status: str = "",
        failure_type: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not bool(getattr(cfg, "CONTROL_LOOP_TRACE_ENABLED", True)):
            return
        with contextlib.suppress(Exception):
            await create_task_node_event(
                db,
                graph_id=int(graph_id),
                node_id=(int(node.node_id) if isinstance(node, LoopNode) else None),
                node_key=(node.node_key if isinstance(node, LoopNode) else ""),
                event_type=event_type,
                status=status,
                agent=(node.owner if isinstance(node, LoopNode) else ""),
                stage=(node.node_type if isinstance(node, LoopNode) else ""),
                failure_type=failure_type,
                details=details or {},
            )
        with contextlib.suppress(Exception):
            await emit_runtime_trace_async(
                db=db,
                event=f"control_loop.{event_type}",
                status=(
                    "fail"
                    if str(failure_type or "").strip()
                    else (str(status or "").strip().lower() or "ok")
                ),
                level=("error" if str(failure_type or "").strip() else "info"),
                flow=_runtime_flow(),
                project_id=str(project.get("id") or ""),
                graph_id=str(graph_id),
                node_key=(node.node_key if isinstance(node, LoopNode) else ""),
                node_type=(node.node_type if isinstance(node, LoopNode) else ""),
                phase="execution",
                stage=(node.node_type if isinstance(node, LoopNode) else ""),
                worker_id=(
                    str(node.worker_id or active_worker_id)
                    if isinstance(node, LoopNode)
                    else str(active_worker_id)
                ),
                transport="ssh_first",
                runtime_mode="ssh",
                error_code=(str(failure_type or "").strip() if str(failure_type or "").strip() else ""),
                error_message=(
                    str((details or {}).get("error") or (details or {}).get("summary") or "")
                    if str(failure_type or "").strip()
                    else ""
                ),
                failure_class=(str(failure_type or "").strip() if str(failure_type or "").strip() else ""),
                details=details or {},
            )

    def _as_relative_path(path_value: str) -> str:
        clean = str(path_value or "").strip().replace("\\", "/")
        base = str(working_dir or "").strip().replace("\\", "/").rstrip("/")
        if base and clean.lower().startswith(base.lower() + "/"):
            return clean[len(base) + 1 :]
        return clean.lstrip("/")

    async def _read_worker_file(rel_path: str) -> str:
        rel = _as_relative_path(rel_path)
        if not rel:
            return ""
        result = await send_action(
            "file_read",
            {"file": f"{working_dir}/{rel}"},
            timeout=30,
            confirmed=True,
        )
        if result.get("status") == "error" or _action_exit_code(result) != 0:
            return ""
        return str(_action_inner_result(result).get("content") or "")

    async def _index_written_files(node: LoopNode, files_written: list[str]) -> list[dict[str, Any]]:
        if not bool(getattr(cfg, "CONTROL_LOOP_CODE_INDEX_ENABLED", True)):
            return []
        indexed: list[dict[str, Any]] = []
        for path in files_written:
            rel = _as_relative_path(path)
            if not rel:
                continue
            content = await _read_worker_file(rel)
            if not content:
                continue
            stats = await index_file(
                db,
                project_id=str(project["id"]),
                path=rel,
                content=content,
            )
            indexed.append({"path": rel, **stats})
        if indexed:
            await _trace_event(
                node=node,
                event_type="index.updated",
                status="ok",
                details={"count": len(indexed), "items": indexed[:8]},
            )
        return indexed

    async def _critic_context_bundle(node: LoopNode, milestone_text: str) -> str:
        if not bool(getattr(cfg, "CONTROL_LOOP_CONTEXT_COMPRESSION_ENABLED", True)):
            return ""
        event_rows = await list_task_node_events(
            db,
            graph_id=int(graph_id),
            limit=max(10, int(getattr(cfg, "CONTROL_LOOP_CONTEXT_TOPK", 12) or 12) * 2),
        )
        memory_rows = await list_project_memory(
            db,
            project_id=str(project["id"]),
        )
        finding_rows = await list_critic_findings(
            db,
            node_id=int(node.node_id),
        )
        index_hits = await query_code_index(
            db,
            project_id=str(project["id"]),
            terms=re.findall(r"[A-Za-z0-9_./:-]{3,}", milestone_text)[:10],
            top_k=max(5, int(getattr(cfg, "CONTROL_LOOP_CONTEXT_TOPK", 12) or 12)),
        )
        return build_context_bundle(
            objective=str(project.get("description") or project.get("name") or ""),
            active_node={
                "node_key": node.node_key,
                "node_type": node.node_type,
                "milestone": milestone_text,
            },
            last_failure=next(
                (
                    row
                    for row in reversed(event_rows)
                    if str(row.get("failure_type") or "").strip()
                ),
                None,
            ),
            required_artifacts=[_RUN_CONTRACT_FILE],
            memory_rows=memory_rows,
            event_rows=event_rows,
            findings=finding_rows,
            index_hits=index_hits,
            max_chars=max(1200, int(getattr(cfg, "CONTROL_LOOP_CONTEXT_MAX_CHARS", 12000) or 12000)),
        )

    async def _run_required_tools(node: LoopNode, files_written: list[str]) -> tuple[bool, str, str]:
        if not bool(getattr(cfg, "CONTROL_LOOP_TOOL_AWARE_ENABLED", True)):
            return True, "", ""
        payload = node.payload or {}
        tools = [str(item).strip().lower() for item in (payload.get("tools_required") or []) if str(item).strip()]
        if not tools:
            return True, "", ""
        python_project = any(str(path).lower().endswith(".py") for path in files_written)
        node_project = any(str(path).lower().endswith((".js", ".ts", ".tsx")) for path in files_written)
        for tool_name in tools:
            action = ""
            params: dict[str, Any] = {}
            timeout = 300
            if tool_name == "lint":
                action = "lint_project"
                params = {"working_dir": working_dir, "linter": ("ruff" if python_project else "eslint")}
            elif tool_name == "test":
                action = "run_tests"
                params = {"working_dir": working_dir, "runner": ("pytest" if python_project else "npm")}
            elif tool_name == "build":
                action = "build_project"
                params = {"working_dir": working_dir, "build_tool": ("python" if python_project else "npm")}
            elif tool_name == "deps":
                action = "install_dependencies"
                params = {"working_dir": working_dir, "manager": ("pip" if python_project else "npm")}
            elif tool_name == "docker":
                action = "run_tool_command"
                params = {"working_dir": working_dir, "command": "docker build -t skynet-loop .", "timeout_seconds": 900}
                timeout = 900
            elif tool_name == "terraform":
                action = "run_tool_command"
                params = {"working_dir": working_dir, "command": "terraform validate", "timeout_seconds": 300}
            elif tool_name == "code":
                continue
            else:
                continue

            result = await send_action(
                action,
                params,
                timeout=timeout,
                confirmed=True,
            )
            if result.get("status") == "error" or _action_exit_code(result) != 0:
                message = _action_error_text(result, action)
                failure_type = FAIL_ENVIRONMENT if _is_infra_error(message) else FAIL_STRICT_GATE
                await _trace_event(
                    node=node,
                    event_type="tool.failed",
                    status="failed",
                    failure_type=failure_type,
                    details={"tool": tool_name, "action": action, "message": message[:220]},
                )
                return False, message[:260], failure_type
            await _trace_event(
                node=node,
                event_type="tool.passed",
                status="passed",
                details={"tool": tool_name, "action": action},
            )
        return True, "", ""

    async def _stage_tracker_hook_factory(node: LoopNode, milestone_index: int):
        async def _hook(**payload: Any) -> None:
            stage_name = str(payload.get("stage") or payload.get("next_stage") or "").strip()
            detail = str(payload.get("detail") or "").strip()
            await update_tracker(
                phase="milestone_execution",
                phase_detail=detail or f"{node.node_key} {payload.get('event', 'stage')}",
                milestone_index=milestone_index,
                milestones_total=total,
                attempt=int(payload.get("stage_index", 1) or 1),
                stage=stage_name or node.node_key,
                runtime_mode=str(payload.get("runtime") or "").strip() or "ssh",
                queue_mode=str(payload.get("queue_mode") or "").strip(),
                graph_id=str(graph_id),
                arch_version=architecture_version,
                node_key=node.node_key,
                node_type=node.node_type,
                worker_id=node.worker_id or active_worker_id,
            )
        return _hook

    async def _work_executor(node: LoopNode) -> dict[str, Any]:
        nonlocal successful_milestones, failed_milestones, skipped_milestones, last_valid_run_contract, learning_policy
        payload = node.payload or {}
        milestone_text = str(payload.get("milestone_text") or node.title or "").strip()
        milestone_index = int(payload.get("index", 0) or 0)
        if milestone_index <= 0:
            milestone_index = max(1, successful_milestones + failed_milestones + skipped_milestones + 1)
        if node.node_type == "work":
            await update_tracker(
                phase="milestone_review",
                phase_detail=f"Waiting for approval on milestone {milestone_index}/{total}",
                milestone_index=milestone_index,
                milestones_total=total,
                stage=node.node_key,
                graph_id=str(graph_id),
                node_key=node.node_key,
                node_type=node.node_type,
            )
            event = asyncio.Event()
            event_key = _MS_EVENT_KEY.format(uid=user_id)
            decision_key = _MS_DECISION_KEY.format(uid=user_id)
            app.bot_data[event_key] = event
            app.bot_data.pop(decision_key, None)
            await app.bot.send_message(
                chat_id,
                f"<b>Milestone {milestone_index}/{total}</b>\n\n{milestone_text}",
                parse_mode="HTML",
                reply_markup=milestone_review(),
            )
            try:
                await asyncio.wait_for(event.wait(), timeout=3600)
            except asyncio.TimeoutError:
                app.bot_data.pop(event_key, None)
                skipped_milestones += 1
                return {"skipped": True, "summary": f"Milestone {milestone_index} approval timed out"}
            app.bot_data.pop(event_key, None)
            decision = app.bot_data.pop(decision_key, "skip")
            if decision == "stop":
                app.bot_data[stop_request_cache_key] = True
                return {"stopped": True, "summary": f"Stopped at milestone {milestone_index}"}
            if decision == "skip":
                skipped_milestones += 1
                return {"skipped": True, "summary": f"Milestone {milestone_index} skipped"}
        if app.bot_data.get(stop_request_cache_key):
            return {"stopped": True, "summary": "Stop requested"}

        task_title = (
            f"Milestone {milestone_index}: {milestone_text[:80].splitlines()[0]}"
            if node.node_type == "work"
            else f"Repair: {node.node_key}"
        )
        task_rec = await create_task(
            db,
            project_id=project["id"],
            title=task_title,
            description=milestone_text or node.title,
        )
        await update_task_status(db, task_rec["id"], status="running")

        prompt = _control_loop_work_prompt(
            project=project,
            milestone_text=milestone_text,
            working_dir=working_dir,
        )
        if node.node_type == "repair":
            prompt = _control_loop_repair_prompt(
                project=project,
                working_dir=working_dir,
                findings=list(payload.get("findings") or []),
            )
        if loop_v2_enabled:
            if not learning_policy:
                policy_row = await get_active_prompt_policy(
                    db,
                    scope="project",
                    project_id=str(project["id"]),
                    policy_kind="repair",
                )
                if isinstance(policy_row, dict):
                    candidate = policy_row.get("policy")
                    if isinstance(candidate, dict):
                        learning_policy = candidate
            prompt = apply_prompt_policy(prompt=prompt, policy=learning_policy)

        stage_tracker_hook = await _stage_tracker_hook_factory(node, milestone_index)
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
            label_prefix=f"control-loop {node.node_key}",
            require_runnable_files=True,
            notify_stage_switch=True,
            tracker_hook=stage_tracker_hook,
        )
        if not generation_result.get("ok"):
            attempted = generation_result.get("attempted_stages") or []
            error_message = f"GENERATION_FAILED: {','.join(attempted) or 'codex'}"
            failure_type = classify_generation_error(error_message)
            await update_task_status(
                db,
                task_rec["id"],
                status="failed",
                error_message=error_message,
            )
            await _trace_event(
                node=node,
                event_type="generation.failed",
                status="failed",
                failure_type=failure_type,
                details={"error": error_message, "attempted": attempted},
            )
            if node.node_type == "work":
                failed_milestones += 1
            return {"ok": False, "error": error_message, "failure_type": failure_type}

        inner = generation_result.get("inner", {})
        written = _normalize_written_files(inner.get("files_written"))
        summary = str(inner.get("stdout") or inner.get("stderr") or "").strip()[:500]
        tools_ok, tools_error, tools_failure_type = await _run_required_tools(node, written)
        if not tools_ok:
            await update_task_status(
                db,
                task_rec["id"],
                status="failed",
                error_message=tools_error or "tool execution failed",
            )
            if node.node_type == "work":
                failed_milestones += 1
            return {
                "ok": False,
                "error": tools_error or "tool execution failed",
                "failure_type": tools_failure_type or FAIL_STRICT_GATE,
            }

        if strict_mode:
            gate_completion: set[str] = set()

            async def _gate_hook(**payload: Any) -> None:
                gate_name = str(payload.get("gate_name") or "").strip()
                gate_status = str(payload.get("status") or "").strip().lower()
                if gate_name and gate_status in {"passed", "failed", "skipped"}:
                    gate_completion.add(gate_name)
                await update_tracker(
                    phase="quality_gates",
                    phase_detail=f"{gate_name}: {gate_status}",
                    milestone_index=milestone_index,
                    milestones_total=total,
                    gate=gate_name,
                    stage=node.node_key,
                    attempt=int(payload.get("attempt", 1) or 1),
                    gates_progress=_clamp_unit(len(gate_completion) / float(max(1, len(_TRACKER_GATE_ORDER)))),
                    graph_id=str(graph_id),
                    node_key=node.node_key,
                    node_type=node.node_type,
                )

            gate_result = await _run_strict_quality_gates(
                db=db,
                task_id=task_rec["id"],
                project=project,
                milestone_text=milestone_text,
                working_dir=working_dir,
                tracker_hook=_gate_hook,
                stage_chain=execution_stage_chain,
                app=app,
                chat_id=chat_id,
                user_id=user_id,
            )
            if not bool(gate_result.get("passed")):
                failed_names = gate_result.get("failed_gate_names") or []
                error_message = str(gate_result.get("error_message") or "STRICT_GATES_FAILED")
                if failed_names:
                    error_message = f"STRICT_GATES_FAILED:{','.join(failed_names)}"
                failure_type = classify_gate_failure(
                    failed_gate_names=[str(name) for name in failed_names],
                    error_message=error_message,
                )
                await update_task_status(
                    db,
                    task_rec["id"],
                    status="failed",
                    error_message=error_message,
                )
                await _trace_event(
                    node=node,
                    event_type="gates.failed",
                    status="failed",
                    failure_type=failure_type,
                    details={"failed_gates": failed_names, "error": error_message},
                )
                if node.node_type == "work":
                    failed_milestones += 1
                return {
                    "ok": False,
                    "error": error_message,
                    "infra_failed": bool(gate_result.get("infra_failure")),
                    "failure_type": failure_type,
                }
            run_contract = gate_result.get("run_contract")
            if isinstance(run_contract, dict):
                last_valid_run_contract = run_contract

        await update_task_status(
            db,
            task_rec["id"],
            status="done",
            result_summary=summary,
        )
        indexed = await _index_written_files(node, written)
        work_context[node.node_key] = {
            "task_id": int(task_rec["id"]),
            "milestone_text": milestone_text,
            "files_written": written,
            "summary": summary,
            "indexed": indexed,
        }
        all_written_files.extend(written)
        if node.node_type == "work":
            successful_milestones += 1
        return {"ok": True, "summary": summary}

    async def _critic_executor(node: LoopNode) -> dict[str, Any]:
        payload = node.payload or {}
        work_key = str(payload.get("work_node_key") or "").strip()
        work_data = work_context.get(work_key, {})
        files_written = [str(p) for p in (work_data.get("files_written") or []) if str(p).strip()]
        milestone_text = str(payload.get("milestone_text") or work_data.get("milestone_text") or "").strip()
        gate_summary = ""
        task_id = work_data.get("task_id")
        if isinstance(task_id, int):
            gate_rows = await list_task_gate_results(db, task_id=task_id)
            gate_summary = _gate_rows_summary(gate_rows)

        prompt = build_review_prompt(
            project_name=str(project.get("name") or ""),
            milestone_text=milestone_text or node.title,
            files_written=files_written,
            gate_summary=gate_summary,
        )
        compressed = await _critic_context_bundle(node, milestone_text or node.title)
        if compressed:
            prompt = (
                f"{prompt}\n\nAdditional compressed context (JSON):\n"
                f"{compressed}\n"
            )
        timeout = max(30, int(getattr(cfg, "CONTROL_LOOP_REVIEW_TIMEOUT_SECONDS", 120) or 120))
        result = await send_action(
            "run_coding_agent",
            {
                "agent": str(getattr(cfg, "CONTROL_LOOP_CRITIC_AGENT", "codex") or "codex"),
                "backend": "auto",
                "prompt": prompt,
                "working_dir": working_dir,
                "timeout_seconds": timeout,
            },
            timeout=timeout,
            confirmed=True,
        )
        if result.get("status") == "error":
            return {
                "ok": False,
                "parse_error": True,
                "error": _action_error_text(result, "run_coding_agent"),
                "critic_name": "review",
            }
        if _action_exit_code(result) != 0:
            return {
                "ok": False,
                "parse_error": True,
                "error": _action_excerpt(result),
                "critic_name": "review",
            }
        raw = str(_action_inner_result(result).get("stdout") or "").strip()
        try:
            parsed = parse_critic_response(raw)
        except Exception as exc:
            return {
                "ok": False,
                "parse_error": True,
                "error": f"CRITIC_PARSE_ERROR: {exc}",
                "failure_type": "CRITIC_PARSE_FAILED",
                "critic_name": "review",
            }
        findings = parsed.get("findings", [])
        if bool(getattr(cfg, "CONTROL_LOOP_ARCH_CRITIC_ENABLED", True)):
            arch_rules_path = str(getattr(cfg, "CONTROL_LOOP_ARCH_RULES_FILE", "") or "").strip()
            refs: list[dict[str, Any]] = []
            if arch_rules_path:
                async with db.execute(
                    """
                    SELECT from_path, to_module, ref_kind
                    FROM code_index_refs
                    WHERE project_id = ?
                    ORDER BY id DESC
                    LIMIT 500
                    """,
                    (str(project["id"]),),
                ) as cur:
                    refs = [dict(r) for r in await cur.fetchall()]
            if refs:
                arch_findings = evaluate_architecture_refs(
                    refs=refs,
                    rules=load_arch_rules(arch_rules_path),
                )
                if arch_findings:
                    if not isinstance(findings, list):
                        findings = []
                    findings.extend(arch_findings)
                    await _trace_event(
                        node=node,
                        event_type="critic.architecture",
                        status="failed",
                        failure_type=FAIL_STRICT_GATE,
                        details={"count": len(arch_findings)},
                    )
        blocking = is_blocking(
            findings if isinstance(findings, list) else [],
            threshold=str(getattr(cfg, "CONTROL_LOOP_CRITIC_BLOCK_THRESHOLD", "high") or "high"),
        )
        arch_contract_ok = True
        arch_violation_count = 0
        if loop_v2_enabled and bool(getattr(cfg, "CONTROL_LOOP_ARCH_BLOCKING", True)):
            arch_contract_ok, arch_violation_count = evaluate_architecture_contract(
                findings=findings if isinstance(findings, list) else [],
                max_violations=max(0, int(getattr(cfg, "CONTROL_LOOP_ARCH_MAX_VIOLATIONS", 0) or 0)),
            )
            if not arch_contract_ok:
                blocking = True
                await _trace_event(
                    node=node,
                    event_type="critic.architecture.contract",
                    status="failed",
                    failure_type=FAIL_STRICT_GATE,
                    details={"violations": arch_violation_count},
                )
        summary = "critic passed"
        if blocking:
            summary = "blocking findings detected"
        elif findings:
            summary = "non-blocking findings detected"
        return {
            "ok": bool(parsed.get("passed", True)) and not blocking,
            "blocking": blocking,
            "findings": findings,
            "summary": summary,
            "critic_name": "review",
            "failure_type": FAIL_STRICT_GATE if blocking else "",
            "arch_violation_count": arch_violation_count,
        }

    async def _gate_executor(node: LoopNode) -> dict[str, Any]:
        rows = await list_graph_nodes(db, graph_id=int(graph_id))
        for row in rows:
            if str(row.get("node_type") or "") == "critic" and str(row.get("status") or "") != "done":
                return {"ok": False, "summary": "Not all critic nodes passed", "failure_type": FAIL_STRICT_GATE}
        blocking_count = 0
        async with db.execute(
            """
            SELECT COUNT(1) AS c
            FROM critic_findings cf
            JOIN task_nodes tn ON tn.id = cf.node_id
            WHERE tn.graph_id = ? AND LOWER(cf.severity) IN ('high', 'critical')
            """,
            (int(graph_id),),
        ) as cur:
            row = await cur.fetchone()
            if row:
                blocking_count = int(row[0] or 0)
        passed, reason = validate_completion_contract(
            contract=completion_contract,
            node_rows=rows,
            has_valid_run_contract=bool(last_valid_run_contract),
            blocking_findings_count=blocking_count,
        )
        if not passed:
            return {"ok": False, "summary": reason, "failure_type": FAIL_CONTRACT}
        return {"ok": True, "summary": "All critic nodes passed and completion contract satisfied"}

    async def _record_learning(node: LoopNode, event: str, payload: dict[str, Any]) -> None:
        nonlocal learning_policy
        if not loop_v2_enabled or not bool(getattr(cfg, "CONTROL_LOOP_LEARNING_ENABLED", True)):
            return
        failure_type = str(payload.get("failure_type") or "").strip().upper()
        if not failure_type:
            return
        critic_code = str(payload.get("critic_code") or "").strip()
        pattern_key = build_pattern_key(
            failure_type=failure_type,
            critic_code=critic_code,
            node_type=node.node_type,
        )
        await create_learning_event(
            db,
            project_id=str(project["id"]),
            graph_id=int(graph_id),
            node_id=int(node.node_id),
            failure_type=failure_type,
            critic_code=critic_code,
            pattern_key=pattern_key,
            event={
                "event": event,
                "node_key": node.node_key,
                "node_type": node.node_type,
                "summary": str(payload.get("summary") or payload.get("error") or "")[:220],
            },
        )
        await _trace_event(
            node=node,
            event_type="learning.event",
            status="recorded",
            failure_type=failure_type,
            details={"pattern_key": pattern_key},
        )
        learning_events = await list_learning_events(
            db,
            project_id=str(project["id"]),
            limit=200,
        )
        policy = build_conservative_prompt_policy(
            events=learning_events,
            min_samples=max(1, int(getattr(cfg, "CONTROL_LOOP_LEARNING_MIN_SAMPLES", 5) or 5)),
            apply_mode=str(getattr(cfg, "CONTROL_LOOP_LEARNING_APPLY_MODE", "conservative") or "conservative"),
        )
        if not policy:
            return
        learning_policy = policy
        await upsert_prompt_policy(
            db,
            scope="project",
            project_id=str(project["id"]),
            policy_kind="repair",
            policy=policy,
            source="learning",
            active=True,
        )
        await _trace_event(
            node=node,
            event_type="learning.policy_applied",
            status="active",
            details={"sample_count": int(policy.get("sample_count", 0) or 0)},
        )

    async def _on_node_event(node: LoopNode, event: str, payload: dict[str, Any]) -> None:
        nonlocal active_worker_id
        if loop_v2_enabled and event == "running":
            workers = await list_active_workers(db)
            required_tools = list(node.tools_required or [])
            if not required_tools and isinstance(node.payload, dict):
                required_tools = [
                    str(item).strip()
                    for item in (node.payload.get("tools_required") or [])
                    if str(item).strip()
                ]
            selected_worker, reason = select_worker(
                workers=workers,
                required_capabilities=required_tools,
                default_worker_id=str(
                    getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "worker-primary") or "worker-primary"
                ),
                strategy=str(
                    getattr(cfg, "CONTROL_LOOP_WORKER_POOL_STRATEGY", "capability_priority")
                    or "capability_priority"
                ),
            )
            active_worker_id = selected_worker
            node.worker_id = selected_worker
            if not any(str(item.get("id") or "") == selected_worker for item in workers):
                await upsert_worker_registry(
                    db,
                    worker_id=selected_worker,
                    label="Auto-registered worker",
                    transport="ssh",
                    endpoint={"host": "ssh-tunnel"},
                    capabilities=required_tools or ["code"],
                    status="active",
                    priority=100,
                )
            await create_node_worker_assignment(
                db,
                graph_id=int(graph_id),
                node_id=int(node.node_id),
                worker_id=selected_worker,
                assignment_reason=reason,
            )
            await update_task_node_worker(
                db,
                node_id=int(node.node_id),
                worker_id=selected_worker,
            )
            await _trace_event(
                node=node,
                event_type="worker.assigned",
                status="assigned",
                details={
                    "worker_id": selected_worker,
                    "reason": reason,
                    "required_tools": required_tools,
                },
            )
        phase = "milestone_execution"
        if node.node_type == "critic":
            phase = "quality_gates"
        if node.node_type == "gate":
            phase = "finalization"
        detail = str(payload.get("summary") or payload.get("error") or event).strip()
        await update_tracker(
            phase=phase,
            phase_detail=f"{node.node_key} {event}" + (f": {detail[:140]}" if detail else ""),
            stage=f"{node.node_key}:{node.node_type}",
            attempt=max(0, int(node.attempt_count)),
            graph_id=str(graph_id),
            arch_version=architecture_version,
            node_key=node.node_key,
            node_type=node.node_type,
            worker_id=node.worker_id or active_worker_id,
            critic_name=("review" if node.node_type == "critic" else ""),
        )
        if event == "needs_changes":
            await app.bot.send_message(
                chat_id,
                f"\u26A0\uFE0F Critic blocked {node.node_key}. Creating repair node...",
            )
        await _trace_event(
            node=node,
            event_type=f"node.{event}",
            status=str(payload.get("status") or event),
            failure_type=str(payload.get("failure_type") or ""),
            details=payload,
        )
        await _record_learning(node, event, payload)

    try:
        run_result = await controller.run(
            execute_work=_work_executor,
            execute_critic=_critic_executor,
            execute_gate=_gate_executor,
            on_node_event=_on_node_event,
        )
    except Exception as exc:
        error_text = (str(exc).strip() or type(exc).__name__)[:320]
        failure_type = classify_generation_error(error_text)
        await create_task_node_event(
            db,
            graph_id=int(graph_id),
            node_id=None,
            node_key="",
            event_type="graph.crash",
            status="failed",
            failure_type=failure_type,
            details={"error": error_text},
        )
        run_result = {
            "status": "failed",
            "graph_id": graph_id,
            "error": error_text,
            "failure_type": failure_type,
        }

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

    milestone_summary = (
        f"complete={successful_milestones}, failed={failed_milestones}, skipped={skipped_milestones}"
    )
    graph_status = str(run_result.get("status") or "failed").strip().lower()
    if graph_status != "completed":
        await _trace_event(
            node=None,
            event_type="graph.final",
            status=graph_status,
            failure_type=str(run_result.get("failure_type") or ""),
            details={"summary": milestone_summary, "error": run_result.get("error")},
        )
    if graph_status == "completed" and successful_milestones > 0:
        app.bot_data[f"run_project_{user_id}"] = project["id"]
        app.bot_data[run_files_cache_key] = unique_written
        if strict_mode and last_valid_run_contract:
            app.bot_data[run_contract_cache_key] = last_valid_run_contract
        elif strict_mode:
            app.bot_data.pop(run_contract_cache_key, None)
        await update_tracker(
            phase="finalization",
            phase_detail=f"Graph {graph_id} completed",
            status="completed",
            final_progress=1.0,
            stage="",
            gate="",
            run_contract_status="validated" if strict_mode else "legacy",
            graph_id=str(graph_id),
            arch_version=architecture_version,
            node_key="",
            node_type="",
            worker_id=active_worker_id,
            critic_name="",
        )
        await finalize_tracker(
            status="completed",
            detail=f"Graph {graph_id} completed ({milestone_summary})",
        )
        await app.bot.send_message(
            chat_id,
            f"\U0001F389 <b>{project['name']}</b> coding session complete!\n"
            f"Graph: <code>{graph_id}</code>\n"
            f"\U0001F4C1 <code>{working_dir}</code>\n"
            f"{milestone_summary}\n\n"
            "Use /status to review milestones or run the project now.",
            parse_mode="HTML",
            reply_markup=run_project(),
        )
        return

    if graph_status == "stopped":
        await update_tracker(
            phase="finalization",
            phase_detail=f"Graph {graph_id} stopped",
            status="stopped",
            final_progress=1.0,
            stage="",
            gate="",
            graph_id=str(graph_id),
            arch_version=architecture_version,
            node_key="",
            node_type="",
            worker_id=active_worker_id,
            critic_name="",
        )
        await finalize_tracker(
            status="stopped",
            detail=f"Graph {graph_id} stopped ({milestone_summary})",
        )
        await app.bot.send_message(
            chat_id,
            f"\u23F9 Session stopped.\nGraph: <code>{graph_id}</code>\n{milestone_summary}",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    app.bot_data.pop(run_contract_cache_key, None)
    await update_tracker(
        phase="finalization",
        phase_detail=f"Graph {graph_id} failed",
        status="failed",
        final_progress=1.0,
        stage="",
        gate="",
        graph_id=str(graph_id),
        arch_version=architecture_version,
        node_key="",
        node_type="",
        worker_id=active_worker_id,
        critic_name="",
    )
    await finalize_tracker(
        status="failed",
        detail=f"Graph {graph_id} failed ({milestone_summary})",
    )
    await app.bot.send_message(
        chat_id,
        f"\u26A0\uFE0F <b>{project['name']}</b> session failed.\n"
        f"Graph: <code>{graph_id}</code>\n"
        f"{milestone_summary}\n\n"
        "Tap Retry Coding to run again.",
        parse_mode="HTML",
        reply_markup=retry_coding(project["id"]),
    )


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
    await emit_runtime_trace_async(
        db=db,
        event="coding.loop.enter",
        status="start",
        flow=_runtime_flow(),
        project_id=project_id,
        phase="setup",
        worker_id=str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
        transport="ssh_first",
        runtime_mode="ssh",
        working_dir=working_dir,
        details={
            "strict_mode": strict_mode,
            "effective_profile": effective_profile,
            "stage_chain": list(active_stage_chain),
            "do_github": bool(do_github),
        },
    )

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
            working_dir=working_dir,
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
            await _update_tracker(
                phase="milestone_extraction",
                phase_detail=f"Milestone extraction failed: {err[:120]}",
                status="failed",
                extraction_progress=1.0,
            )
            await _finalize_tracker(
                status="failed",
                detail=f"Milestone extraction failed: {err}",
            )
            await app.bot.send_message(
                chat_id,
                (
                    "Could not extract milestones from the approved plan.\n"
                    f"<code>{html_mod.escape(err)}</code>\n\n"
                    "Please refine or regenerate the plan, then try coding again."
                ),
                parse_mode="HTML",
                reply_markup=retry_coding(project["id"]),
            )
            return
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

        if _use_control_loop_v1(project) and db is not None:
            loop_stage_name = _effective_control_loop_profile(project)
            await _update_tracker(
                phase="milestone_execution",
                phase_detail="Running closed orchestration loop",
                milestone_index=0,
                milestones_total=total,
                stage=loop_stage_name,
            )
            await _run_control_loop_v1(
                app=app,
                chat_id=chat_id,
                user_id=user_id,
                db=db,
                project=project,
                milestones=milestones,
                working_dir=working_dir,
                strict_mode=strict_mode,
                active_stage_chain=active_stage_chain,
                run_files_cache_key=run_files_cache_key,
                run_contract_cache_key=run_contract_cache_key,
                stop_request_cache_key=stop_request_cache_key,
                update_tracker=_update_tracker,
                finalize_tracker=_finalize_tracker,
            )
            return
        if _use_control_loop_v1(project) and db is None:
            logger.warning(
                "Control loop requested but DB handle missing; falling back to legacy milestone loop."
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
                    runtime_value = str(payload.get("runtime") or "").strip() or None
                    queue_value = str(payload.get("queue_mode") or "").strip() or None
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
                        runtime_mode=runtime_value,
                        queue_mode=queue_value,
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
                        app=app,
                        chat_id=chat_id,
                        user_id=user_id,
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
            await emit_runtime_trace_async(
                db=db,
                event="coding.loop.exit",
                status="ok",
                flow=_runtime_flow(),
                project_id=project_id,
                phase="finalization",
                worker_id=str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
                transport="ssh_first",
                runtime_mode="ssh",
                working_dir=working_dir,
                details={
                    "complete": successful_milestones,
                    "failed": failed_milestones,
                    "skipped": skipped_milestones,
                    "strict_mode": strict_mode,
                },
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
            await emit_runtime_trace_async(
                db=db,
                event="coding.loop.exit",
                status="fail",
                level="error",
                flow=_runtime_flow(),
                project_id=project_id,
                phase="finalization",
                worker_id=str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
                transport="ssh_first",
                runtime_mode="ssh",
                error_type="LoopFailure",
                error_code="GENERATION_FAILED",
                error_message="No successful milestones completed.",
                failure_class="GENERATION_FAILED",
                mitigation_hint="Inspect failed milestones and gate outputs in /trace deep.",
                working_dir=working_dir,
                details={
                    "complete": successful_milestones,
                    "failed": failed_milestones,
                    "skipped": skipped_milestones,
                },
            )

    except Exception:
        logger.exception("Coding loop crashed for project %s user %s", project["id"], user_id)
        await emit_runtime_trace_async(
            db=db,
            event="coding.loop.exit",
            status="fail",
            level="error",
            flow=_runtime_flow(),
            project_id=project_id,
            phase="finalization",
            worker_id=str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or ""),
            transport="ssh_first",
            runtime_mode="ssh",
            error_type="LoopCrash",
            error_code="LOOP_CRASH",
            error_message="Unexpected coding loop crash.",
            failure_class="ENVIRONMENT_FAILED",
            mitigation_hint="Inspect traceback and latest debug.bundle event.",
            working_dir=working_dir,
        )
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
    await _emit_runtime(
        context=context,
        event="run_project.request",
        status="start",
        user_id=user_id,
        phase="run",
    )

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
        await _emit_runtime(
            context=context,
            event="run_project.request",
            status="fail",
            user_id=user_id,
            phase="run",
            error_code="PROJECT_NOT_FOUND",
            error_message="No project found to run.",
            failure_class="ENVIRONMENT_FAILED",
        )
        await update.callback_query.message.reply_text(
            "No project found to run.",
            reply_markup=main_menu(),
        )
        return

    strict_mode = _is_strict_project(project)
    run_files_cache_key = _run_files_key(user_id, project["id"])
    run_contract_cache_key = _run_contract_key(user_id, project["id"])

    if not is_worker_available():
        await _emit_runtime(
            context=context,
            event="run_project.request",
            status="fail",
            user_id=user_id,
            project_id=str(project.get("id") or ""),
            phase="run",
            error_code="WORKER_UNAVAILABLE",
            error_message="Worker not connected for run action.",
            failure_class="ENVIRONMENT_FAILED",
        )
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
                await _emit_runtime(
                    context=context,
                    event="run_project.request",
                    status="fail",
                    user_id=user_id,
                    project_id=str(project.get("id") or ""),
                    phase="run",
                    error_code=("ENVIRONMENT_FAILED" if manifest_infra else "CONTRACT_FAILED"),
                    error_message=str(manifest_summary or "Invalid run contract"),
                    failure_class=("ENVIRONMENT_FAILED" if manifest_infra else "CONTRACT_FAILED"),
                    mitigation_hint="Validate skynet_run.json and worker connectivity before running.",
                    working_dir=working_dir,
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
                await _emit_runtime(
                    context=context,
                    event="run_project.request",
                    status="fail",
                    user_id=user_id,
                    project_id=str(project.get("id") or ""),
                    phase="run",
                    error_code="ENVIRONMENT_FAILED",
                    error_message=detail,
                    failure_class="ENVIRONMENT_FAILED",
                    working_dir=working_dir,
                )
                return

            if list_result.get("status") == "error" or _action_exit_code(list_result) != 0:
                detail = _action_error_text(list_result, "list_directory")
                await update.callback_query.message.reply_text(
                    f"Run failed: infrastructure error while listing files: <code>{html_mod.escape(detail[:260])}</code>",
                    parse_mode="HTML",
                    reply_markup=run_project(),
                )
                await _emit_runtime(
                    context=context,
                    event="run_project.request",
                    status="fail",
                    user_id=user_id,
                    project_id=str(project.get("id") or ""),
                    phase="run",
                    error_code="ENVIRONMENT_FAILED",
                    error_message=detail,
                    failure_class="ENVIRONMENT_FAILED",
                    working_dir=working_dir,
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
            await _emit_runtime(
                context=context,
                event="run_project.request",
                status="fail",
                user_id=user_id,
                project_id=str(project.get("id") or ""),
                phase="run",
                error_code="CONTRACT_FAILED",
                error_message="No runnable entry point found.",
                failure_class="CONTRACT_FAILED",
                mitigation_hint="Ensure project includes skynet_run.json or a detectable entrypoint.",
                working_dir=working_dir,
            )
            return

        run_cmd, run_target = resolved

    if not run_cmd:
        await update.callback_query.message.reply_text(
            "No runnable command is available for this project.",
            reply_markup=main_menu(),
        )
        await _emit_runtime(
            context=context,
            event="run_project.request",
            status="fail",
            user_id=user_id,
            project_id=str(project.get("id") or ""),
            phase="run",
            error_code="CONTRACT_FAILED",
            error_message="No runnable command available.",
            failure_class="CONTRACT_FAILED",
            working_dir=working_dir,
        )
        return

    await update.callback_query.message.reply_text(
        f"Running <code>{html_mod.escape(run_target or '')}</code> on your laptop...",
        parse_mode="HTML",
    )
    await _emit_runtime(
        context=context,
        event="run_project.exec",
        status="start",
        user_id=user_id,
        project_id=str(project.get("id") or ""),
        phase="run",
        action_name="exec_command",
        working_dir=working_dir,
        details={"run_target": str(run_target or ""), "strict_mode": strict_mode},
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
        await _emit_runtime(
            context=context,
            event="run_project.exec",
            status=("ok" if int(exit_code or 0) == 0 else "fail"),
            level=("info" if int(exit_code or 0) == 0 else "error"),
            user_id=user_id,
            project_id=str(project.get("id") or ""),
            phase="run",
            action_name="exec_command",
            working_dir=working_dir,
            error_code=("RUN_FAILED" if int(exit_code or 0) != 0 else ""),
            error_message=(stderr or stdout)[:240] if int(exit_code or 0) != 0 else "",
            failure_class=("STRICT_GATE_FAILED" if int(exit_code or 0) != 0 else ""),
            details={"exit_code": int(exit_code or 0), "run_target": str(run_target or "")},
        )
    except Exception as exc:
        await update.callback_query.message.reply_text(
            f"Run failed: {html_mod.escape(str(exc)[:300])}",
            parse_mode="HTML",
            reply_markup=run_markup,
        )
        await _emit_runtime(
            context=context,
            event="run_project.exec",
            status="fail",
            level="error",
            user_id=user_id,
            project_id=str(project.get("id") or ""),
            phase="run",
            action_name="exec_command",
            working_dir=working_dir,
            error_type=type(exc).__name__,
            error_code="ENVIRONMENT_FAILED" if _is_infra_error(str(exc)) else "RUN_FAILED",
            error_message=str(exc)[:300],
            failure_class=("ENVIRONMENT_FAILED" if _is_infra_error(str(exc)) else "STRICT_GATE_FAILED"),
            mitigation_hint="Check worker command execution and run contract target.",
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


def _parse_planner_task_graph_payload(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    nodes_raw = parsed.get("nodes")
    if not isinstance(nodes_raw, list):
        return None
    milestones: list[str] = []
    normalized_nodes: list[dict[str, Any]] = []
    for node in nodes_raw:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("node_type") or node.get("type") or "").strip().lower()
        title = str(node.get("title") or "").strip()
        if not title:
            continue
        if node_type and node_type not in {"work", "milestone"}:
            continue
        deps = [str(dep).strip() for dep in (node.get("deps") or []) if str(dep).strip()]
        try:
            priority = int(node.get("priority") or 200)
        except Exception:
            priority = 200
        normalized = {
            "node_key": str(node.get("node_key") or "").strip(),
            "title": title,
            "node_type": "work",
            "owner": str(node.get("owner") or "codex").strip() or "codex",
            "worker_id": str(node.get("worker_id") or "").strip(),
            "deps": deps,
            "priority": priority,
            "tools_required": list(node.get("tools_required") or []),
            "acceptance": list(node.get("acceptance") or []),
            "risk": dict(node.get("risk") or {}),
            "risk_level": str(node.get("risk_level") or (node.get("risk") or {}).get("level") or "medium"),
        }
        milestones.append(title)
        normalized_nodes.append(normalized)
    if not milestones:
        return None
    return {
        "milestones": milestones,
        "nodes": normalized_nodes,
        "success_contract": parsed.get("success_contract") if isinstance(parsed.get("success_contract"), dict) else {},
        "execution_strategy": parsed.get("execution_strategy") if isinstance(parsed.get("execution_strategy"), dict) else {},
        "parallel_lanes": parsed.get("parallel_lanes") if isinstance(parsed.get("parallel_lanes"), list) else [],
        "risk_assessment": parsed.get("risk_assessment") if isinstance(parsed.get("risk_assessment"), list) else [],
    }


async def _extract_milestones_codex_then_router(
    *,
    router,
    project: dict[str, Any],
    working_dir: str,
) -> list[str]:
    def _deterministic_fallback() -> list[str]:
        plan_text = str(project.get("description") or "").strip()
        from_numbered = _parse_milestones_fallback(plan_text)
        if from_numbered:
            return from_numbered

        bullets: list[str] = []
        for line in plan_text.splitlines():
            match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
            if not match:
                continue
            item = match.group(1).strip()
            if not item:
                continue
            lowered = item.lower()
            if lowered in {"none", "n/a"}:
                continue
            if lowered.startswith("original user requirements"):
                continue
            bullets.append(item)
        if bullets:
            deduped: list[str] = []
            seen: set[str] = set()
            for item in bullets:
                key = item.lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(item)
            return deduped[:4]

        project_name = str(project.get("name") or "project").strip() or "project"
        return [
            f"Implement core functionality for {project_name} from approved requirements",
            "Add tests plus skynet_run.json and verify successful run output",
        ]

    def _reset_loop_graph_hints() -> None:
        project["_loop_success_contract"] = {}
        project["_loop_execution_strategy"] = {}
        project["_loop_parallel_lanes"] = []
        project["_loop_risk_assessment"] = []
        project["_loop_node_specs"] = []

    allow_router_fallback = bool(getattr(cfg, "CONTROL_LOOP_ROUTER_FALLBACK_ENABLED", False))
    if str(getattr(cfg, "PLANNER_PRIMARY_AGENT", "router")).strip().lower() != "codex":
        if allow_router_fallback:
            return await _extract_milestones_router(router, project)
        fallback = _deterministic_fallback()
        _reset_loop_graph_hints()
        return fallback

    plan = project.get("description", "")
    if not plan:
        return []

    # Prefer explicit milestones already present in the approved plan text.
    # This avoids planner-side DAG drift and keeps coding steps requirement-grounded.
    direct_milestones = _parse_milestones_fallback(str(plan))
    if direct_milestones:
        _reset_loop_graph_hints()
        return direct_milestones

    prompt = (
        "You are a project planner. Build an execution DAG for coding milestones.\n"
        "Return ONLY valid JSON, no markdown.\n"
        "Preferred schema:\n"
        "{\"nodes\":[{\"node_key\":\"work_1\",\"title\":\"...\",\"node_type\":\"work\",\"owner\":\"codex\","
        "\"deps\":[],\"priority\":200,\"tools_required\":[\"code\",\"test\"],\"acceptance\":[],\"risk\":{\"level\":\"medium\"}}],"
        "\"success_contract\":{\"required_nodes\":[\"work_1\"],\"required_artifacts\":[\"skynet_run.json\"]},"
        "\"execution_strategy\":{\"mode\":\"adaptive_parallel_x2\"}}\n"
        "Fallback schema: JSON array of milestone strings.\n\n"
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
            await send_action(
                "create_directory",
                {"directory": working_dir},
                timeout=20,
                confirmed=True,
            )
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

        parsed_graph = _parse_planner_task_graph_payload(output)
        if parsed_graph:
            project["_loop_success_contract"] = parsed_graph.get("success_contract") or {}
            project["_loop_execution_strategy"] = parsed_graph.get("execution_strategy") or {}
            project["_loop_parallel_lanes"] = parsed_graph.get("parallel_lanes") or []
            project["_loop_risk_assessment"] = parsed_graph.get("risk_assessment") or []
            project["_loop_node_specs"] = parsed_graph.get("nodes") or []
            return [str(item).strip() for item in (parsed_graph.get("milestones") or []) if str(item).strip()]
        parsed_list = _parse_json_string_list(output)
        if parsed_list:
            _reset_loop_graph_hints()
            return parsed_list
        fallback = _deterministic_fallback()
        _reset_loop_graph_hints()
        logger.warning(
            "milestone.primary.codex_invalid_json project_id=%s fallback_count=%s",
            project.get("id"),
            len(fallback),
        )
        return fallback
    except Exception as exc:
        logger.warning(
            "milestone.primary.failover project_id=%s stage=codex error=%s",
            project.get("id"),
            str(exc)[:220],
        )
        fallback = _deterministic_fallback()
        if fallback:
            _reset_loop_graph_hints()
            logger.warning(
                "milestone.primary.local_fallback project_id=%s fallback_count=%s",
                project.get("id"),
                len(fallback),
            )
            return fallback
        if allow_router_fallback:
            return await _extract_milestones_router(router, project)
        raise


async def _extract_milestones(
    router,
    project: dict[str, Any],
    *,
    working_dir: str | None = None,
) -> list[str]:
    project_id = str(project.get("id") or "unknown")
    # Keep planner/extraction artifacts out of the project workspace.
    effective_working_dir = f"{cfg.WORKER_PROJECTS_DIR}/_planner_sessions/milestones/{project_id}"
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



