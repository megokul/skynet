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


def test_runtime_trace_snapshot_prefers_trace_mirror_dir(monkeypatch, tmp_path: Path) -> None:
    mirror_dir = tmp_path / "mirror"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    runtime_trace = mirror_dir / "skynet.trace.log"
    runtime_trace.write_text("a\nb\nc\nd\n", encoding="utf-8")
    monkeypatch.delenv("SKYNET_E2E_RUNTIME_TRACE_FILE", raising=False)
    monkeypatch.setenv("SKYNET_TRACE_MIRROR_LOG_DIR", str(mirror_dir))

    events: list[tuple[str, dict]] = []

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))

    telegram_live._emit_runtime_trace_snapshot(_trace, checkpoint="mirror-check", tail_lines=3)

    assert events, "Expected snapshot event to be emitted."
    _event, payload = events[-1]
    assert payload.get("status") == "ok"
    assert payload.get("line_count") == 4
    assert payload.get("lines") == 3
    assert str(payload.get("trace_file") or "").endswith("skynet.trace.log")


def test_container_log_ssh_resolution_prefers_e2e_override(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_SSH_HOST", "ec2.example")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_SSH_USER", "ubuntu")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_SSH_KEY", "C:/keys/e2e.pem")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_SSH_PORT", "2202")
    monkeypatch.setenv("OPENCLAW_TUNNEL_EC2_HOST", "ignored-host")
    monkeypatch.setenv("OPENCLAW_TUNNEL_EC2_USER", "ignored-user")
    monkeypatch.setenv("OPENCLAW_TUNNEL_SSH_KEY", "ignored-key")

    resolved = telegram_live._resolve_container_log_stream_ssh()
    assert resolved is not None
    assert resolved["host"] == "ec2.example"
    assert resolved["user"] == "ubuntu"
    assert resolved["key"] == "C:/keys/e2e.pem"
    assert resolved["port"] == 2202


def test_container_log_ssh_resolution_falls_back_to_tunnel(monkeypatch) -> None:
    monkeypatch.delenv("SKYNET_E2E_CONTAINER_LOG_SSH_HOST", raising=False)
    monkeypatch.delenv("SKYNET_E2E_CONTAINER_LOG_SSH_USER", raising=False)
    monkeypatch.delenv("SKYNET_E2E_CONTAINER_LOG_SSH_KEY", raising=False)
    monkeypatch.setenv("OPENCLAW_TUNNEL_EC2_HOST", "ec2-fallback")
    monkeypatch.setenv("OPENCLAW_TUNNEL_EC2_USER", "ubuntu")
    monkeypatch.setenv("OPENCLAW_TUNNEL_SSH_KEY", "C:/keys/fallback.pem")

    resolved = telegram_live._resolve_container_log_stream_ssh()
    assert resolved is not None
    assert resolved["host"] == "ec2-fallback"
    assert resolved["user"] == "ubuntu"
    assert resolved["key"] == "C:/keys/fallback.pem"
    assert resolved["port"] == 22


def test_container_log_line_redaction_masks_secrets() -> None:
    raw = (
        "authorization: Bearer ABCDEF123 token=my-token password=supersecret "
        "https://api.telegram.org/bot123456:ABCdefGhIJklMNopQRstUVwxYZ/sendMessage"
    )
    sanitized = telegram_live._sanitize_container_log_line(raw, max_chars=500)
    lowered = sanitized.lower()
    assert "abcdef123" not in lowered
    assert "my-token" not in lowered
    assert "supersecret" not in lowered
    assert "bot123456:" not in lowered
    assert "redacted" in lowered


def test_runtime_trace_progress_uses_mtime_and_line_count() -> None:
    progress = telegram_live._RuntimeTraceProgress()
    first = {
        "status": "ok",
        "mtime_iso": "2026-03-06T11:00:00+00:00",
        "line_count": 100,
        "digest": "aaa",
    }
    second_same = {
        "status": "ok",
        "mtime_iso": "2026-03-06T11:00:00+00:00",
        "line_count": 100,
        "digest": "bbb",  # tail-hash differences alone must not reset freshness
    }
    third_grew = {
        "status": "ok",
        "mtime_iso": "2026-03-06T11:00:00+00:00",
        "line_count": 101,
        "digest": "ccc",
    }

    assert progress.observe(first) is True
    assert progress.observe(second_same) is False
    assert progress.observe(third_grew) is True
