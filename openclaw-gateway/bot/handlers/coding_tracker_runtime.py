from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


def tracker_reply_markup(*, state: dict[str, Any], tracker_session_controls: Callable[..., Any]):
    return tracker_session_controls(status=str(state.get("status") or "running"))


def tracker_render_text(
    *,
    state: dict[str, Any],
    tracker_state_api: Any,
    bar_width: int,
    stale_warn_seconds_value: int,
    verbose_pipeline: bool,
) -> str:
    return tracker_state_api.tracker_render_text(
        state,
        bar_width=bar_width,
        stale_warn_seconds_value=stale_warn_seconds_value,
        verbose_pipeline=verbose_pipeline,
    )


@dataclass(frozen=True)
class TrackerUpdateRequest:
    phase: str | None = None
    phase_detail: str | None = None
    status: str | None = None
    milestone_index: int | None = None
    milestones_total: int | None = None
    attempt: int | None = None
    stage: str | None = None
    gate: str | None = None
    run_contract_status: str | None = None
    session_id: str | None = None
    runtime_mode: str | None = None
    queue_mode: str | None = None
    graph_id: str | None = None
    arch_version: str | None = None
    node_key: str | None = None
    node_type: str | None = None
    worker_id: str | None = None
    critic_name: str | None = None
    setup_progress: float | None = None
    extraction_progress: float | None = None
    execution_progress: float | None = None
    gates_progress: float | None = None
    final_progress: float | None = None
    heartbeat_elapsed: int | None = None
    transport: str | None = None
    force: bool = False


@dataclass(frozen=True)
class TrackerLifecycleDeps:
    cfg: Any
    logger: Any
    state_api: Any
    use_acp_orchestration: Callable[[], bool]
    tracker_enabled: Callable[[], bool]
    tracker_default_transport: Callable[[], str]
    tracker_default_runtime_mode: Callable[[], str]
    tracker_render_text: Callable[[dict[str, Any]], str]
    tracker_reply_markup: Callable[[dict[str, Any]], Any]
    tracker_state_key: Callable[[int, str], str]
    active_project_key: Callable[[int], str]
    tracker_get_state: Callable[..., dict[str, Any] | None]
    tracker_edit_interval: Callable[[], int]


async def init_tracker_message(
    *,
    deps: TrackerLifecycleDeps,
    app,
    chat_id: int,
    user_id: int,
    project: dict[str, Any],
    working_dir: str,
    strict_mode: bool,
) -> None:
    if not deps.tracker_enabled():
        return
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        return

    now = time.monotonic()
    state = deps.state_api.build_tracker_initial_state(
        project_id=project_id,
        project_name=str(project.get("name") or "").strip(),
        working_dir=working_dir,
        strict_mode=strict_mode,
        transport=deps.tracker_default_transport(),
        runtime_mode=deps.tracker_default_runtime_mode(),
        queue_mode=(
            str(getattr(deps.cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue")
            if deps.use_acp_orchestration()
            else ""
        ),
        now=now,
    )
    text = deps.tracker_render_text(state)
    msg = await app.bot.send_message(
        chat_id,
        text,
        reply_markup=deps.tracker_reply_markup(state),
    )
    state["message_id"] = int(getattr(msg, "message_id", 0) or 0)
    state["last_rendered_text"] = text
    state["last_edit_monotonic"] = now
    app.bot_data[deps.tracker_state_key(user_id, project_id)] = state
    app.bot_data[deps.active_project_key(user_id)] = project_id
    deps.logger.info(
        "telegram.tracker.init project_id=%s task_id=%s phase=%s percent=%s status=%s",
        project_id,
        None,
        state.get("phase"),
        state.get("percent"),
        state.get("status"),
    )


async def update_tracker_message(
    *,
    deps: TrackerLifecycleDeps,
    app,
    chat_id: int,
    user_id: int,
    project_id: str | None,
    request: TrackerUpdateRequest,
) -> None:
    if not deps.tracker_enabled():
        return

    pid = str(project_id or app.bot_data.get(deps.active_project_key(user_id)) or "").strip()
    if not pid:
        return
    state = deps.tracker_get_state(bot_data=app.bot_data, user_id=user_id, project_id=pid)
    if state is None:
        return

    now = time.monotonic()
    prior = dict(state)
    deps.state_api.apply_tracker_updates(
        state,
        now=now,
        default_transport=deps.tracker_default_transport(),
        phase=request.phase,
        phase_detail=request.phase_detail,
        status=request.status,
        milestone_index=request.milestone_index,
        milestones_total=request.milestones_total,
        attempt=request.attempt,
        stage=request.stage,
        gate=request.gate,
        run_contract_status=request.run_contract_status,
        session_id=request.session_id,
        runtime_mode=request.runtime_mode,
        queue_mode=request.queue_mode,
        graph_id=request.graph_id,
        arch_version=request.arch_version,
        node_key=request.node_key,
        node_type=request.node_type,
        worker_id=request.worker_id,
        critic_name=request.critic_name,
        setup_progress=request.setup_progress,
        extraction_progress=request.extraction_progress,
        execution_progress=request.execution_progress,
        gates_progress=request.gates_progress,
        final_progress=request.final_progress,
        heartbeat_elapsed=request.heartbeat_elapsed,
        transport=request.transport,
    )
    prior_percent = int(prior.get("percent", 0) or 0)
    text = deps.tracker_render_text(state)
    if text == str(state.get("last_rendered_text") or "") and not request.force:
        return

    significant_change = any(
        str(prior.get(key) or "") != str(state.get(key) or "")
        for key in (
            "phase",
            "phase_detail",
            "status",
            "milestone_index",
            "milestones_total",
            "attempt",
            "stage",
            "gate",
            "run_contract_status",
            "session_id",
            "runtime_mode",
            "queue_mode",
            "graph_id",
            "arch_version",
            "node_key",
            "node_type",
            "worker_id",
            "critic_name",
            "transport",
        )
    ) or int(state.get("percent", 0) or 0) != prior_percent

    edit_interval = deps.tracker_edit_interval()
    since_last_edit = now - float(state.get("last_edit_monotonic", 0.0) or 0.0)
    if (
        not request.force
        and not significant_change
        and edit_interval > 0
        and since_last_edit < edit_interval
    ):
        return

    message_id = int(state.get("message_id", 0) or 0)
    if message_id <= 0:
        return

    try:
        await app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=deps.tracker_reply_markup(state),
        )
    except Exception as exc:  # pragma: no cover - network behavior
        err_text = str(exc).lower()
        if "message is not modified" in err_text:
            state["last_rendered_text"] = text
            state["last_edit_monotonic"] = now
            return
        if "message to edit not found" in err_text or "message can't be edited" in err_text:
            try:
                replacement = await app.bot.send_message(
                    chat_id,
                    text,
                    reply_markup=deps.tracker_reply_markup(state),
                )
                state["message_id"] = int(getattr(replacement, "message_id", 0) or 0)
                deps.logger.info(
                    "telegram.tracker.replace_message project_id=%s task_id=%s phase=%s percent=%s status=%s",
                    pid,
                    None,
                    state.get("phase"),
                    state.get("percent"),
                    state.get("status"),
                )
            except Exception as send_exc:  # pragma: no cover - network behavior
                deps.logger.warning(
                    "telegram.tracker.error project_id=%s task_id=%s stage=replace error_excerpt=%s",
                    pid,
                    None,
                    str(send_exc)[:220],
                )
                return
        else:
            deps.logger.warning(
                "telegram.tracker.error project_id=%s task_id=%s stage=edit error_excerpt=%s",
                pid,
                None,
                str(exc)[:220],
            )
            return

    state["last_rendered_text"] = text
    state["last_edit_monotonic"] = now
    deps.logger.info(
        "telegram.tracker.update project_id=%s task_id=%s phase=%s percent=%s status=%s stage=%s gate=%s",
        pid,
        None,
        state.get("phase"),
        state.get("percent"),
        state.get("status"),
        state.get("stage"),
        state.get("gate"),
    )


async def finalize_tracker_message(
    *,
    deps: TrackerLifecycleDeps,
    app,
    chat_id: int,
    user_id: int,
    project_id: str,
    status: str,
    detail: str,
) -> None:
    await update_tracker_message(
        deps=deps,
        app=app,
        chat_id=chat_id,
        user_id=user_id,
        project_id=project_id,
        request=TrackerUpdateRequest(
            phase="finalization",
            phase_detail=detail,
            status=status,
            final_progress=1.0,
            stage="",
            gate="",
            force=True,
        ),
    )
    deps.logger.info(
        "telegram.tracker.final project_id=%s task_id=%s phase=%s percent=%s status=%s",
        project_id,
        None,
        "finalization",
        (
            deps.tracker_get_state(
                bot_data=app.bot_data,
                user_id=user_id,
                project_id=project_id,
            )
            or {}
        ).get("percent", 0),
        status,
    )
