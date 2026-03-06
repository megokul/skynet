import json

import pytest

import config as cfg
from db.schema import init_db
from db.store import list_runtime_trace_events
from runtime_trace import command_preview, emit_runtime_trace, emit_runtime_trace_async


def _read_events(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_runtime_trace_required_fields_and_redaction(tmp_path, monkeypatch):
    trace_file = tmp_path / "skynet.trace.log"
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_LIVE_FILE", str(trace_file), raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_REDACTION_MODE", "redacted_hash", raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_PAYLOAD_MODE", "redacted_hash", raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE", True, raising=False)

    emit_runtime_trace(
        "unit.runtime.start",
        status="start",
        project_id="p1",
        task_id="42",
        details={"api_key": "super-secret-value", "plain": "visible"},
    )

    events = _read_events(trace_file)
    assert len(events) == 1
    event = events[0]
    required = {
        "ts",
        "level",
        "event",
        "trace_id",
        "span_id",
        "parent_span_id",
        "flow",
        "project_id",
        "task_id",
        "graph_id",
        "node_key",
        "node_type",
        "phase",
        "stage",
        "gate",
        "worker_id",
        "transport",
        "runtime_mode",
        "status",
        "event_id",
        "error_type",
        "error_code",
        "error_message",
        "telegram_chat_id",
        "telegram_user_id",
        "telegram_message_id",
        "action_name",
        "command_hash",
        "working_dir",
        "root_trace_id",
        "session_key",
        "remote_pid",
        "artifact_count",
        "details",
    }
    assert required.issubset(set(event.keys()))
    assert event["event"] == "unit.runtime.start"
    assert event["project_id"] == "p1"
    redacted = event["details"]["details"]["api_key"]
    assert isinstance(redacted, dict)
    assert redacted.get("redacted") is True
    assert event["details"]["details"]["plain"] == "visible"


def test_runtime_trace_fail_emits_debug_bundle(tmp_path, monkeypatch):
    trace_file = tmp_path / "skynet.trace.log"
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_LIVE_FILE", str(trace_file), raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE", True, raising=False)

    emit_runtime_trace(
        "unit.runtime.fail",
        status="fail",
        error_code="UNIT_FAIL",
        error_message="failure for test",
    )

    events = _read_events(trace_file)
    names = [event.get("event") for event in events]
    assert "unit.runtime.fail" in names
    assert "debug.bundle" in names
    bundle = next(event for event in events if event.get("event") == "debug.bundle")
    details = bundle.get("details") or {}
    debug_bundle = details.get("debug_bundle") or {}
    assert debug_bundle.get("failure_class") in {"UNIT_FAIL", "UNKNOWN"}


@pytest.mark.asyncio
async def test_runtime_trace_async_persists_to_db(tmp_path, monkeypatch):
    trace_file = tmp_path / "skynet.trace.log"
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_LIVE_FILE", str(trace_file), raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_DB_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE", True, raising=False)

    db = await init_db(":memory:")
    try:
        await emit_runtime_trace_async(
            db=db,
            event="unit.runtime.db",
            status="ok",
            project_id="proj-1",
            phase="unit_test",
        )
        rows = await list_runtime_trace_events(db, project_id="proj-1", limit=10)
        assert rows
        assert any(str(row.get("event") or "") == "unit.runtime.db" for row in rows)
    finally:
        await db.close()


def test_command_preview_hashes_and_truncates(monkeypatch):
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_COMMAND_PREVIEW_CHARS", 24, raising=False)
    payload = command_preview("token=abc123 " + ("x" * 200))
    assert payload["command_hash"]
    assert len(payload["command_preview"]) <= 120
    assert "abc123" not in payload["command_preview"]
