"""Unit tests for Telegram tracker progress helpers."""

from __future__ import annotations

from bot.handlers.coding import (
    _render_progress_bar,
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
