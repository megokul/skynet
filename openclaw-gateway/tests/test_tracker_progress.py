"""Unit tests for Telegram tracker progress helpers."""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from bot.handlers import coding
from bot.handlers.coding import (
    _render_progress_bar,
    _tracker_default_runtime_mode,
    _tracker_default_transport,
    _tracker_progress_weights,
    _tracker_recompute_percent,
)


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


def test_tracker_defaults_report_ssh_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coding.cfg, "CODING_TRANSPORT", "auto", raising=False)
    monkeypatch.setattr(
        coding.cfg,
        "get_str",
        lambda name, default="": "ssh_tunnel" if name == "OPENCLAW_EXECUTION_MODE" else default,
        raising=False,
    )
    monkeypatch.setattr(coding, "_use_acp_orchestration", lambda: False)

    assert _tracker_default_transport() == "ssh_first"
    assert _tracker_default_runtime_mode() == "ssh"


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
