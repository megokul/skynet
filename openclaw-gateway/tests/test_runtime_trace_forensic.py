import json

import config as cfg
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
