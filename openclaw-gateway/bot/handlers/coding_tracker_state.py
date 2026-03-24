from __future__ import annotations

import time
from typing import Any


def tracker_state_key(*, template: str, user_id: int, project_id: str) -> str:
    return str(template or "").format(uid=user_id, pid=project_id)


def active_project_key(*, template: str, user_id: int) -> str:
    return str(template or "").format(uid=user_id)


def stop_request_key(*, template: str, user_id: int) -> str:
    return str(template or "").format(uid=user_id)


def tracker_bar_width(raw: int) -> int:
    return max(10, min(int(raw or 20), 40))


def tracker_edit_interval(raw: int) -> int:
    return max(0, int(raw or 3))


def tracker_stale_warn_seconds(raw: int) -> int:
    return max(30, int(raw or 90))


def tracker_stuck_exit_seconds(*, stale_warn_seconds_value: int, raw: int) -> int:
    timeout = int(raw or 0)
    if timeout <= 0:
        return 0
    return max(int(stale_warn_seconds_value), timeout)


def tracker_watchdog_poll_seconds(raw: int) -> int:
    return max(1, int(raw or 5))


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def render_progress_bar(percent: int, width: int) -> str:
    clamped_percent = max(0, min(100, int(percent)))
    safe_width = tracker_bar_width(width)
    filled = int(round((clamped_percent / 100.0) * safe_width))
    return f"[{'#' * filled}{'-' * (safe_width - filled)}]"


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def tracker_progress_weights(*, strict_mode: bool) -> tuple[int, int]:
    if strict_mode:
        return 55, 20
    return 75, 0


def tracker_recompute_percent(state: dict[str, Any]) -> int:
    strict_mode = bool(state.get("strict_mode", False))
    exec_weight, gates_weight = tracker_progress_weights(strict_mode=strict_mode)
    score = (
        10.0 * clamp_unit(float(state.get("setup_progress", 0.0) or 0.0))
        + 10.0 * clamp_unit(float(state.get("extraction_progress", 0.0) or 0.0))
        + float(exec_weight) * clamp_unit(float(state.get("execution_progress", 0.0) or 0.0))
        + float(gates_weight) * clamp_unit(float(state.get("gates_progress", 0.0) or 0.0))
        + 5.0 * clamp_unit(float(state.get("final_progress", 0.0) or 0.0))
    )
    next_percent = max(0, min(100, int(round(score))))
    prior_percent = int(state.get("percent", 0) or 0)
    monotonic = max(prior_percent, next_percent)
    state["percent"] = monotonic
    return monotonic


def tracker_estimate_percent_from_tasks(tasks: list[dict[str, Any]]) -> int:
    if not tasks:
        return 0
    total = len(tasks)
    done = sum(1 for task in tasks if str(task.get("status", "")).lower() == "done")
    failed = sum(1 for task in tasks if str(task.get("status", "")).lower() == "failed")
    running = sum(1 for task in tasks if str(task.get("status", "")).lower() == "running")
    progress = (done + failed + 0.5 * running) / float(total)
    return max(0, min(100, int(round(progress * 100))))


def tracker_get_state(*, bot_data: dict[str, Any], state_key: str) -> dict[str, Any] | None:
    raw = bot_data.get(state_key)
    if isinstance(raw, dict):
        return raw
    return None


def tracker_get_active_state(
    *,
    bot_data: dict[str, Any],
    active_project_state_key: str,
    state_key_template: str,
    user_id: int,
) -> tuple[str, dict[str, Any]] | None:
    project_id = str(bot_data.get(active_project_state_key) or "").strip()
    if not project_id:
        return None
    state = tracker_get_state(
        bot_data=bot_data,
        state_key=tracker_state_key(template=state_key_template, user_id=user_id, project_id=project_id),
    )
    if state is None:
        return None
    return project_id, state


def tracker_is_terminal_status(status: str | None) -> bool:
    return str(status or "").strip().lower() in {"completed", "failed", "stopped"}


def build_tracker_initial_state(
    *,
    project_id: str,
    project_name: str,
    working_dir: str,
    strict_mode: bool,
    transport: str,
    runtime_mode: str,
    queue_mode: str,
    now: float,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "project_id": project_id,
        "project_name": project_name,
        "working_dir": working_dir,
        "strict_mode": strict_mode,
        "transport": transport,
        "run_contract_status": "pending" if strict_mode else "legacy",
        "session_id": "",
        "runtime_mode": runtime_mode,
        "queue_mode": queue_mode,
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
    tracker_recompute_percent(state)
    return state


def apply_tracker_updates(
    state: dict[str, Any],
    *,
    now: float,
    default_transport: str,
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
    transport: str | None = None,
) -> dict[str, Any]:
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
    if transport is not None:
        state["transport"] = str(transport).strip() or state.get("transport", default_transport)
    if heartbeat_elapsed is not None:
        state["last_signal_monotonic"] = now
        if not phase_detail:
            base_detail = str(state.get("phase_detail", "") or "").strip()
            state["phase_detail"] = (
                f"{base_detail} ({heartbeat_elapsed}s elapsed)".strip()
                if base_detail
                else f"{heartbeat_elapsed}s elapsed"
            )
    elif any(
        value is not None
        for value in (
            phase,
            phase_detail,
            status,
            milestone_index,
            milestones_total,
            attempt,
            stage,
            gate,
            run_contract_status,
            session_id,
            runtime_mode,
            queue_mode,
            graph_id,
            arch_version,
            node_key,
            node_type,
            worker_id,
            critic_name,
            transport,
            setup_progress,
            extraction_progress,
            execution_progress,
            gates_progress,
            final_progress,
        )
    ):
        state["last_signal_monotonic"] = now

    for key, raw in (
        ("setup_progress", setup_progress),
        ("extraction_progress", extraction_progress),
        ("execution_progress", execution_progress),
        ("gates_progress", gates_progress),
        ("final_progress", final_progress),
    ):
        if raw is None:
            continue
        state[key] = max(float(state.get(key, 0.0) or 0.0), clamp_unit(float(raw)))

    tracker_recompute_percent(state)
    return state


def tracker_render_text(
    state: dict[str, Any],
    *,
    bar_width: int,
    stale_warn_seconds_value: int,
    verbose_pipeline: bool,
    now: float | None = None,
) -> str:
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
    current = time.monotonic() if now is None else float(now)
    elapsed = format_elapsed(current - created_monotonic)
    stale_seconds = current - float(state.get("last_signal_monotonic", created_monotonic) or created_monotonic)
    stale_notice = ""
    if status.lower() == "running" and stale_seconds >= stale_warn_seconds_value:
        stale_notice = "Signal: still running (no new step yet)."

    lines = [
        f"Coding Progress {render_progress_bar(percent, bar_width)} {percent}%",
        f"Phase: {phase}{(' - ' + detail) if detail else ''}",
    ]
    if milestones_total > 0:
        lines.append(f"Milestone: {max(0, milestone_index)}/{milestones_total}")
    else:
        lines.append("Milestone: preparing")
    if verbose_pipeline:
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
