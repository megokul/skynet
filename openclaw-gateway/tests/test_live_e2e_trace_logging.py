from __future__ import annotations

from pathlib import Path

import e2e_live
import test_e2e_telegram_real_live as telegram_live


def test_live_trace_defaults_to_repo_logs(monkeypatch) -> None:
    monkeypatch.delenv("SKYNET_LIVE_TRACE_FILE", raising=False)
    trace = e2e_live.LiveTrace("unit-live-trace")
    try:
        assert trace.path.exists()
        assert trace.path.parent.name == "logs"
    finally:
        trace.path.unlink(missing_ok=True)


def test_telegram_trace_defaults_to_repo_logs(monkeypatch) -> None:
    monkeypatch.delenv("SKYNET_LIVE_TRACE_FILE", raising=False)
    path, trace = telegram_live._make_live_trace_logger("unit-telegram-trace")
    try:
        assert path.exists()
        assert path.parent.name == "logs"
        trace("unit.event", marker="ok")
    finally:
        path.unlink(missing_ok=True)


def test_runtime_trace_snapshot_reads_explicit_trace_file(monkeypatch, tmp_path: Path) -> None:
    runtime_trace = tmp_path / "runtime.trace.log"
    runtime_trace.write_text("line1\nline2\nline3\n", encoding="utf-8")
    monkeypatch.setenv("SKYNET_E2E_RUNTIME_TRACE_FILE", str(runtime_trace))

    events: list[tuple[str, dict]] = []

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))

    telegram_live._emit_runtime_trace_snapshot(_trace, checkpoint="unit-check", tail_lines=2)

    assert events, "Expected snapshot event to be emitted."
    event, payload = events[-1]
    assert event == "runtime.trace.snapshot"
    assert payload.get("status") == "ok"
    assert payload.get("lines") == 2
    assert payload.get("checkpoint") == "unit-check"
