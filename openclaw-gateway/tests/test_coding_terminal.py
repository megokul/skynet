from __future__ import annotations

import asyncio

import pytest

from bot.handlers import coding_terminal


@pytest.mark.asyncio
async def test_request_loop_exit_sets_stop_state_and_cancels_task() -> None:
    bot_data: dict[str, object] = {}
    event = asyncio.Event()
    bot_data["event-7"] = event
    updates: list[dict[str, object]] = []

    async def _update_tracker(**kwargs):
        updates.append(kwargs)

    task = asyncio.create_task(asyncio.sleep(60))
    await coding_terminal.request_loop_exit(
        loop_exit_request={},
        bot_data=bot_data,
        stop_request_cache_key="stop-7",
        user_id=7,
        tracker_finalized=False,
        update_tracker=_update_tracker,
        current_loop_task=task,
        event_key_template="event-{uid}",
        decision_key_template="decision-{uid}",
        status="failed",
        detail="terminal",
        reason="terminal_tracker_state",
        notify_text="notify",
    )

    assert bot_data["stop-7"] is True
    assert bot_data["decision-7"] == "stop"
    assert event.is_set() is True
    assert updates[0]["status"] == "failed"
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_evaluate_tracker_watchdog_terminal_requires_one_poll_grace() -> None:
    state = {
        "status": "failed",
        "phase": "finalization",
        "phase_detail": "done",
        "created_monotonic": 10.0,
        "last_signal_monotonic": 10.0,
    }

    terminal_since, exit_request, stuck_details = coding_terminal.evaluate_tracker_watchdog(
        state=state,
        now=20.0,
        terminal_since=0.0,
        poll_seconds=5,
        stuck_timeout=30,
    )
    assert terminal_since == 20.0
    assert exit_request is None
    assert stuck_details is None

    terminal_since, exit_request, stuck_details = coding_terminal.evaluate_tracker_watchdog(
        state=state,
        now=26.0,
        terminal_since=terminal_since,
        poll_seconds=5,
        stuck_timeout=30,
    )
    assert terminal_since == 20.0
    assert exit_request == {
        "status": "failed",
        "detail": "done",
        "reason": "terminal_tracker_state",
        "notify_text": "",
    }
    assert stuck_details is None


def test_evaluate_tracker_watchdog_returns_stuck_exit_request() -> None:
    terminal_since, exit_request, stuck_details = coding_terminal.evaluate_tracker_watchdog(
        state={
            "status": "running",
            "phase": "milestone_execution",
            "created_monotonic": 10.0,
            "last_signal_monotonic": 10.0,
        },
        now=45.0,
        terminal_since=0.0,
        poll_seconds=5,
        stuck_timeout=30,
    )

    assert terminal_since == 0.0
    assert exit_request is not None
    assert exit_request["status"] == "failed"
    assert exit_request["reason"] == "stuck_tracker_timeout"
    assert stuck_details == {
        "stale_seconds": 35.0,
        "threshold_seconds": 30,
        "phase": "milestone_execution",
    }


def test_cancelled_exit_payload_uses_stop_requested_default() -> None:
    payload = coding_terminal.cancelled_exit_payload({}, stop_requested=True)
    assert payload["cancel_status"] == "stopped"
    assert payload["cancel_detail"] == "Session stopped by user"
    assert payload["exit_status"] == "fail"
    assert payload["failure_class"] == "STOP_REQUESTED"
    assert payload["reason"] == "LOOP_CANCELLED"


def test_clear_loop_runtime_state_removes_current_task_keys() -> None:
    current_task = object()
    bot_data = {
        "stop-7": True,
        "event-7": object(),
        "decision-7": "stop",
        "active-7": "project-1",
        "loop-7": current_task,
    }
    coding_terminal.clear_loop_runtime_state(
        bot_data=bot_data,
        stop_request_cache_key="stop-7",
        event_key_template="event-{uid}",
        decision_key_template="decision-{uid}",
        active_project_key="active-7",
        active_loop_key_template="loop-{uid}",
        user_id=7,
        current_task=current_task,
    )

    assert bot_data == {}
