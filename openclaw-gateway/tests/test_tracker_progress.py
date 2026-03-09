"""Unit tests for Telegram tracker progress helpers."""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from bot.handlers import coding
from bot.handlers.coding import (
    _render_progress_bar,
    _tracker_init_message,
    _tracker_default_runtime_mode,
    _tracker_default_transport,
    _tracker_progress_weights,
    _tracker_recompute_percent,
    _tracker_update,
)
from bot.keyboards import CB_MAIN_MENU, CB_MILESTONE_STOP


def test_render_progress_bar_clamps_bounds() -> None:
    bar = _render_progress_bar(percent=150, width=5)
    assert bar.startswith("[")
    assert bar.endswith("]")
    # width is clamped to min 10
    assert len(bar) == 12
    assert "-" not in bar


def test_tracker_progress_weights_non_strict_reallocates_gate_budget() -> None:
    strict_exec, strict_gates = _tracker_progress_weights(strict_mode=True)
    non_strict_exec, non_strict_gates = _tracker_progress_weights(strict_mode=False)
    assert strict_exec == 55
    assert strict_gates == 20
    assert non_strict_exec == 75
    assert non_strict_gates == 0


def test_tracker_percent_is_monotonic() -> None:
    state = {
        "strict_mode": True,
        "setup_progress": 1.0,
        "extraction_progress": 1.0,
        "execution_progress": 1.0,
        "gates_progress": 1.0,
        "final_progress": 0.0,
        "percent": 95,
    }
    updated = _tracker_recompute_percent(state)
    assert updated >= 95

    # Recomputing with lower internals must not decrease percent.
    state["setup_progress"] = 0.0
    state["extraction_progress"] = 0.0
    state["execution_progress"] = 0.0
    state["gates_progress"] = 0.0
    lowered = _tracker_recompute_percent(state)
    assert lowered == updated


def test_tracker_defaults_report_websocket_primary_when_agent_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        coding.cfg,
        "get_str",
        lambda name, default="": "agent_preferred" if name == "OPENCLAW_EXECUTION_MODE" else default,
        raising=False,
    )
    monkeypatch.setattr(coding, "_use_acp_orchestration", lambda: False)
    monkeypatch.setattr(coding, "websocket_primary_available", lambda: True)
    monkeypatch.setattr(coding, "get_agent_status", lambda: {"websocket_health_ok": True})

    assert _tracker_default_transport() == "websocket_primary"
    assert _tracker_default_runtime_mode() == "worker_agent"


@pytest.mark.asyncio
async def test_tracker_update_edits_immediately_on_significant_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "proj-1"
    user_id = 99
    state = {
        "project_id": project_id,
        "message_id": 123,
        "phase": "setup",
        "phase_detail": "Session started",
        "status": "running",
        "milestone_index": 0,
        "milestones_total": 0,
        "attempt": 0,
        "stage": "",
        "gate": "",
        "run_contract_status": "pending",
        "session_id": "",
        "runtime_mode": "ssh",
        "queue_mode": "",
        "graph_id": "",
        "arch_version": "",
        "node_key": "",
        "node_type": "",
        "worker_id": "",
        "critic_name": "",
        "transport": "ssh_first",
        "setup_progress": 0.1,
        "extraction_progress": 0.0,
        "execution_progress": 0.0,
        "gates_progress": 0.0,
        "final_progress": 0.0,
        "percent": 10,
        "last_rendered_text": "setup||10",
        "last_edit_monotonic": 100.0,
        "last_signal_monotonic": 100.0,
        "created_monotonic": 100.0,
    }
    bot = types.SimpleNamespace(edit_message_text=AsyncMock())
    app = types.SimpleNamespace(
        bot=bot,
        bot_data={
            coding._tracker_state_key(user_id, project_id): state,
            coding._active_project_key(user_id): project_id,
        },
    )

    monkeypatch.setattr(coding, "_tracker_enabled", lambda: True)
    monkeypatch.setattr(coding, "_tracker_edit_interval", lambda: 300)
    monkeypatch.setattr(
        coding,
        "_tracker_render_text",
        lambda current: f"{current.get('phase')}|{current.get('stage')}|{current.get('percent')}",
    )

    await coding._tracker_update(
        app=app,
        chat_id=7,
        user_id=user_id,
        project_id=project_id,
        stage="codex",
    )

    bot.edit_message_text.assert_awaited_once()
    assert state["stage"] == "codex"
    assert state["last_rendered_text"] == "setup|codex|10"


@pytest.mark.asyncio
async def test_tracker_init_message_shows_stop_and_exit_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_message = AsyncMock(return_value=types.SimpleNamespace(message_id=321))
    app = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=send_message), bot_data={})

    monkeypatch.setattr(coding, "_tracker_enabled", lambda: True)
    monkeypatch.setattr(coding, "_tracker_default_transport", lambda: "websocket_primary")
    monkeypatch.setattr(coding, "_tracker_default_runtime_mode", lambda: "worker_agent")

    await _tracker_init_message(
        app=app,
        chat_id=7,
        user_id=99,
        project={"id": "proj-1", "name": "SkyApp"},
        working_dir="E:/SKYNET-SANDBOX/Projects/skyapp",
        strict_mode=False,
    )

    markup = send_message.await_args.kwargs["reply_markup"]
    all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert CB_MILESTONE_STOP in all_data


@pytest.mark.asyncio
async def test_tracker_terminal_update_swaps_to_main_menu_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "proj-1"
    user_id = 99
    state = {
        "project_id": project_id,
        "message_id": 123,
        "phase": "milestone_execution",
        "phase_detail": "Running milestone",
        "status": "running",
        "milestone_index": 1,
        "milestones_total": 1,
        "attempt": 0,
        "stage": "",
        "gate": "",
        "run_contract_status": "pending",
        "session_id": "",
        "runtime_mode": "ssh",
        "queue_mode": "",
        "graph_id": "",
        "arch_version": "",
        "node_key": "",
        "node_type": "",
        "worker_id": "",
        "critic_name": "",
        "transport": "ssh_first",
        "setup_progress": 1.0,
        "extraction_progress": 1.0,
        "execution_progress": 1.0,
        "gates_progress": 0.0,
        "final_progress": 0.0,
        "percent": 95,
        "last_rendered_text": "",
        "last_edit_monotonic": 0.0,
        "last_signal_monotonic": 100.0,
        "created_monotonic": 100.0,
    }
    edit_message_text = AsyncMock()
    app = types.SimpleNamespace(
        bot=types.SimpleNamespace(edit_message_text=edit_message_text),
        bot_data={
            coding._tracker_state_key(user_id, project_id): state,
            coding._active_project_key(user_id): project_id,
        },
    )

    monkeypatch.setattr(coding, "_tracker_enabled", lambda: True)
    monkeypatch.setattr(coding, "_tracker_edit_interval", lambda: 0)

    await _tracker_update(
        app=app,
        chat_id=7,
        user_id=user_id,
        project_id=project_id,
        phase="finalization",
        phase_detail="Session failed",
        status="failed",
        final_progress=1.0,
        force=True,
    )

    markup = edit_message_text.await_args.kwargs["reply_markup"]
    all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert CB_MAIN_MENU in all_data
