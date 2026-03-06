import json

import aiosqlite
import gateway_config as cfg
import pytest
from db.schema import init_db
from runtime_trace import (
    build_artifact_debug_bundle,
    build_process_debug_bundle,
    emit_runtime_trace,
)


def _read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_forensic_redaction_masks_string_bodies(tmp_path, monkeypatch):
    trace_file = tmp_path / "forensic.trace.log"
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_LIVE_FILE", str(trace_file), raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_REDACTION_MODE", "forensic_redacted", raising=False)

    emit_runtime_trace(
        "unit.forensic",
        status="ok",
        details={
            "log_line": (
                "authorization: Bearer SECRET123 "
                "bot123456:ABCdefGhIJklMNopQRstUVwxYZ "
                "-----BEGIN RSA PRIVATE KEY-----hidden-----END RSA PRIVATE KEY-----"
            )
        },
    )

    event = _read_events(trace_file)[0]
    line = event["details"]["details"]["log_line"]
    lowered = str(line).lower()
    assert "secret123" not in lowered
    assert "bot123456" not in lowered
    assert "private key" not in lowered
    assert "redacted" in lowered


def test_process_and_artifact_debug_helpers_are_structured():
    process_bundle = build_process_debug_bundle(
        process_tree=[{"pid": 1, "name": "codex.exe"}],
        prompt_file={"path": "E:/tmp/prompt.txt", "exists": True},
        remote_pid="1",
        stop_cleanup_status="pending",
    )
    artifact_bundle = build_artifact_debug_bundle(
        artifact_snapshot=[{"path": "main.py", "size": 12, "mtime": 100}],
        artifact_count=1,
        files_touched=["main.py"],
    )

    assert process_bundle["remote_pid"] == "1"
    assert process_bundle["prompt_file"]["exists"] is True
    assert artifact_bundle["artifact_count"] == 1
    assert artifact_bundle["files_touched"] == ["main.py"]


@pytest.mark.asyncio
async def test_init_db_upgrades_legacy_runtime_trace_schema(tmp_path):
    db_path = tmp_path / "legacy-runtime-trace.sqlite"

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE runtime_trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                event TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '',
                trace_id TEXT NOT NULL DEFAULT '',
                span_id TEXT NOT NULL DEFAULT '',
                parent_span_id TEXT NOT NULL DEFAULT '',
                flow TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT '',
                graph_id TEXT NOT NULL DEFAULT '',
                node_key TEXT NOT NULL DEFAULT '',
                node_type TEXT NOT NULL DEFAULT '',
                phase TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT '',
                gate TEXT NOT NULL DEFAULT '',
                worker_id TEXT NOT NULL DEFAULT '',
                transport TEXT NOT NULL DEFAULT '',
                runtime_mode TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                telegram_chat_id TEXT NOT NULL DEFAULT '',
                telegram_user_id TEXT NOT NULL DEFAULT '',
                telegram_message_id TEXT NOT NULL DEFAULT '',
                action_name TEXT NOT NULL DEFAULT '',
                command_hash TEXT NOT NULL DEFAULT '',
                working_dir TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        await db.commit()

    db = await init_db(str(db_path))
    try:
        async with db.execute("PRAGMA table_info(runtime_trace_events)") as cur:
            columns = {str(row[1]) for row in await cur.fetchall()}
        assert {"event_id", "root_trace_id", "session_key", "remote_pid", "artifact_count"}.issubset(columns)

        async with db.execute("PRAGMA index_list(runtime_trace_events)") as cur:
            index_rows = await cur.fetchall()
        index_names = {str(row[1]) for row in index_rows}
        assert "idx_runtime_trace_session_created" in index_names
        assert "idx_runtime_trace_project_graph_created" in index_names
    finally:
        await db.close()

