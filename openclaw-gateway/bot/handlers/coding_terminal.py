from __future__ import annotations

import asyncio
from typing import Any


async def request_loop_exit(
    *,
    loop_exit_request: dict[str, Any],
    bot_data: dict[str, Any],
    stop_request_cache_key: str,
    user_id: int,
    tracker_finalized: bool,
    update_tracker,
    current_loop_task: asyncio.Task[Any] | None,
    event_key_template: str,
    decision_key_template: str,
    status: str,
    detail: str,
    reason: str,
    notify_text: str = "",
) -> None:
    loop_exit_request["status"] = str(status).strip().lower()
    loop_exit_request["detail"] = str(detail).strip()
    loop_exit_request["notify_text"] = str(notify_text).strip()
    loop_exit_request["reason"] = str(reason).strip()
    bot_data[stop_request_cache_key] = True
    event_key = event_key_template.format(uid=user_id)
    decision_key = decision_key_template.format(uid=user_id)
    event = bot_data.get(event_key)
    if event is not None:
        bot_data[decision_key] = "stop"
        event.set()
    if not tracker_finalized:
        await update_tracker(
            phase="finalization",
            phase_detail=detail,
            status=status,
            final_progress=1.0,
            stage="",
            gate="",
            force=True,
        )
    if current_loop_task is not None and not current_loop_task.done():
        current_loop_task.cancel()


def evaluate_tracker_watchdog(
    *,
    state: dict[str, Any],
    now: float,
    terminal_since: float,
    poll_seconds: int,
    stuck_timeout: int,
) -> tuple[float, dict[str, str] | None, dict[str, Any] | None]:
    status = str(state.get("status") or "").strip().lower()
    phase = str(state.get("phase") or "").strip().lower()
    created = float(state.get("created_monotonic", now) or now)
    last_signal = float(state.get("last_signal_monotonic", created) or created)
    stale_seconds = max(0.0, now - last_signal)

    if status in {"completed", "failed", "stopped"} and phase == "finalization":
        if terminal_since <= 0.0:
            return now, None, None
        if (now - terminal_since) >= poll_seconds:
            return (
                terminal_since,
                {
                    "status": status,
                    "detail": str(state.get("phase_detail") or "Tracker reached terminal state").strip(),
                    "reason": "terminal_tracker_state",
                    "notify_text": "",
                },
                None,
            )
        return terminal_since, None, None

    if (
        stuck_timeout > 0
        and status == "running"
        and phase not in {"milestone_review", "finalization"}
        and stale_seconds >= stuck_timeout
    ):
        return (
            0.0,
            {
                "status": "failed",
                "detail": f"No progress signal for {int(stale_seconds)}s; stopping stuck session",
                "reason": "stuck_tracker_timeout",
                "notify_text": (
                    "Coding session stopped because it appeared stuck. "
                    "Use /status to inspect the last completed step."
                ),
            },
            {
                "stale_seconds": round(stale_seconds, 1),
                "threshold_seconds": stuck_timeout,
                "phase": phase or "finalization",
            },
        )
    return 0.0, None, None


def cancelled_exit_payload(
    loop_exit_request: dict[str, Any],
    *,
    stop_requested: bool,
) -> dict[str, str]:
    cancel_status = str(loop_exit_request.get("status") or "").strip().lower()
    if cancel_status not in {"failed", "stopped", "completed"}:
        cancel_status = "stopped" if stop_requested else "failed"
    cancel_detail = str(loop_exit_request.get("detail") or "").strip()
    if not cancel_detail:
        cancel_detail = (
            "Session stopped by user" if cancel_status == "stopped" else "Coding loop cancelled before clean exit"
        )
    notify_text = str(loop_exit_request.get("notify_text") or "").strip()
    exit_status = "ok" if cancel_status == "completed" else "fail"
    failure_class = ""
    if cancel_status != "completed":
        failure_class = "STOP_REQUESTED" if cancel_status == "stopped" else "ENVIRONMENT_FAILED"
    return {
        "cancel_status": cancel_status,
        "cancel_detail": cancel_detail,
        "notify_text": notify_text,
        "exit_status": exit_status,
        "failure_class": failure_class,
        "reason": str(loop_exit_request.get("reason") or "LOOP_CANCELLED"),
    }


def clear_loop_runtime_state(
    *,
    bot_data: dict[str, Any],
    stop_request_cache_key: str,
    event_key_template: str,
    decision_key_template: str,
    active_project_key: str,
    active_loop_key_template: str,
    user_id: int,
    current_task: asyncio.Task[Any] | None,
) -> None:
    bot_data.pop(stop_request_cache_key, None)
    bot_data.pop(event_key_template.format(uid=user_id), None)
    bot_data.pop(decision_key_template.format(uid=user_id), None)
    bot_data.pop(active_project_key, None)
    loop_key = active_loop_key_template.format(uid=user_id)
    if bot_data.get(loop_key) is current_task:
        bot_data.pop(loop_key, None)
