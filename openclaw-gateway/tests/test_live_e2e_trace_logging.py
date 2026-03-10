from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import config as gateway_config
import e2e_live
import live_diagnostics
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
    path, trace = live_diagnostics.make_live_trace_logger("unit-telegram-trace")
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

    resolved = live_diagnostics.resolve_container_log_stream_ssh()
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

    resolved = live_diagnostics.resolve_container_log_stream_ssh()
    assert resolved is not None
    assert resolved["host"] == "ec2-fallback"
    assert resolved["user"] == "ubuntu"
    assert resolved["key"] == "C:/keys/fallback.pem"
    assert resolved["port"] == 22


def test_container_log_ssh_resolution_transport_profile_prefers_transport(monkeypatch) -> None:
    monkeypatch.delenv("SKYNET_E2E_CONTAINER_LOG_SSH_HOST", raising=False)
    monkeypatch.delenv("SKYNET_E2E_CONTAINER_LOG_SSH_USER", raising=False)
    monkeypatch.delenv("SKYNET_E2E_CONTAINER_LOG_SSH_KEY", raising=False)
    monkeypatch.setenv("OPENCLAW_TUNNEL_EC2_HOST", "ec2-tunnel")
    monkeypatch.setenv("OPENCLAW_TUNNEL_EC2_USER", "ubuntu")
    monkeypatch.setenv("OPENCLAW_TUNNEL_SSH_KEY", "C:/keys/tunnel.pem")
    monkeypatch.setenv("OPENCLAW_SSH_HOST", "worker-host")
    monkeypatch.setenv("OPENCLAW_SSH_USER", "iamgo")
    monkeypatch.setenv("OPENCLAW_SSH_KEY_PATH", "C:/keys/worker.pem")

    resolved = live_diagnostics.resolve_container_log_stream_ssh("transport")
    assert resolved is not None
    assert resolved["host"] == "worker-host"
    assert resolved["user"] == "iamgo"
    assert resolved["key"] == "C:/keys/worker.pem"


def test_container_log_line_redaction_masks_secrets() -> None:
    raw = (
        "authorization: Bearer ABCDEF123 token=my-token password=supersecret "
        "https://api.telegram.org/bot123456:ABCdefGhIJklMNopQRstUVwxYZ/sendMessage"
    )
    sanitized = live_diagnostics.sanitize_container_log_line(raw, max_chars=500)
    lowered = sanitized.lower()
    assert "abcdef123" not in lowered
    assert "my-token" not in lowered
    assert "supersecret" not in lowered
    assert "bot123456:" not in lowered
    assert "redacted" in lowered


def test_live_e2e_container_log_config_defaults(monkeypatch) -> None:
    for key in (
        "SKYNET_E2E_CONTAINER_LOG_STREAM_ENABLED",
        "SKYNET_E2E_CONTAINER_LOG_REQUIRE_STREAM",
        "SKYNET_E2E_CONTAINER_LOG_SOURCES",
        "SKYNET_E2E_CONTAINER_LOG_MAX_LINE_CHARS",
        "SKYNET_E2E_CONTAINER_LOG_RING_LINES",
        "SKYNET_E2E_CONTAINER_LOG_TAIL_DEFAULT",
        "SKYNET_E2E_CONTAINER_LOG_TAIL_OVERRIDES",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = gateway_config.get_live_e2e_container_log_config()
    assert cfg["stream_enabled"] is True
    assert cfg["require_stream"] is True
    assert cfg["sources"] == ["openclaw-gateway", "skynet-api"]
    assert cfg["max_line_chars"] == 1200
    assert cfg["ring_lines"] == 300
    assert cfg["tail_default"] == 100
    assert cfg["tail_overrides"] == {"openclaw-gateway": 200, "skynet-api": 100}
    assert cfg["ssh_profile"] == gateway_config.LIVE_E2E_DIAGNOSTICS_PROFILE


def test_live_e2e_container_log_config_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_STREAM_ENABLED", "0")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_REQUIRE_STREAM", "false")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_SOURCES", "gateway,worker")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_MAX_LINE_CHARS", "900")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_RING_LINES", "44")
    monkeypatch.setenv("SKYNET_E2E_CONTAINER_LOG_TAIL_DEFAULT", "55")
    monkeypatch.setenv(
        "SKYNET_E2E_CONTAINER_LOG_TAIL_OVERRIDES",
        "gateway=250,worker=70,broken,nope=x,empty=",
    )

    cfg = gateway_config.get_live_e2e_container_log_config()
    assert cfg["stream_enabled"] is False
    assert cfg["require_stream"] is False
    assert cfg["sources"] == ["gateway", "worker"]
    assert cfg["max_line_chars"] == 900
    assert cfg["ring_lines"] == 44
    assert cfg["tail_default"] == 55
    assert cfg["tail_overrides"] == {"gateway": 250, "worker": 70}


def test_live_e2e_policy_derives_required_worker_agents(monkeypatch) -> None:
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_ENABLED", True)
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_FORCE_FOR_ALL", True)
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_PLANNER_AGENT", "qwen")
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_CRITIC_AGENT", "qwen")
    monkeypatch.setenv("SKYNET_E2E_LIVE", "1")
    monkeypatch.setenv("SKYNET_LIVE_E2E_AGENT", "qwen")
    monkeypatch.setenv("SKYNET_LIVE_E2E_ALLOW_FALLBACK", "0")

    policy = gateway_config.get_live_e2e_policy("telegram_real")

    assert policy["active"] is True
    assert policy["required_transport"] == "websocket_primary"
    assert policy["allow_fallback"] is False
    assert policy["effective_coding_stage_chain"] == ["qwen"]
    assert policy["planner_router_fallback_enabled"] is False
    assert policy["control_loop_router_fallback_enabled"] is False
    assert policy["required_coding_agents"] == ["qwen"]
    assert policy["required_planner_agents"] == ["qwen"]
    assert policy["required_worker_agents"] == ["qwen"]
    assert policy["require_telegram_poller"] is True
    assert policy["status_probe_mode"] == "remote_container_http"
    assert policy["qwen_smoke"]["enabled"] is True
    assert policy["qwen_smoke"]["timeout_seconds"] == 45


def test_live_e2e_qwen_smoke_config_defaults(monkeypatch) -> None:
    monkeypatch.delenv("SKYNET_LIVE_E2E_QWEN_SMOKE_ENABLED", raising=False)
    monkeypatch.delenv("SKYNET_LIVE_E2E_QWEN_SMOKE_TIMEOUT_SECONDS", raising=False)

    cfg = gateway_config.get_live_e2e_qwen_smoke_config()
    assert cfg["enabled"] is True
    assert cfg["timeout_seconds"] == 45


def test_live_e2e_qwen_smoke_config_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_LIVE_E2E_QWEN_SMOKE_ENABLED", "0")
    monkeypatch.setenv("SKYNET_LIVE_E2E_QWEN_SMOKE_TIMEOUT_SECONDS", "61")

    cfg = gateway_config.get_live_e2e_qwen_smoke_config()
    assert cfg["enabled"] is False
    assert cfg["timeout_seconds"] == 61


def test_live_e2e_runtime_env_clamps_stage_fallback_when_strict(monkeypatch) -> None:
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_ENABLED", True)
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_FORCE_FOR_ALL", True)
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_PLANNER_AGENT", "qwen")
    monkeypatch.setattr(gateway_config, "CONTROL_LOOP_CRITIC_AGENT", "qwen")
    monkeypatch.setenv("SKYNET_LIVE_E2E_AGENT", "qwen")
    monkeypatch.setenv("SKYNET_LIVE_E2E_ALLOW_FALLBACK", "0")
    monkeypatch.setenv("SKYNET_CODING_FALLBACK_CHAIN", "qwen,codex")
    monkeypatch.setenv("SKYNET_PLANNER_ROUTER_FALLBACK_ENABLED", "1")

    env = gateway_config.get_live_e2e_runtime_env("telegram_real")

    assert env["SKYNET_E2E_LIVE"] == "1"
    assert env["SKYNET_CODING_FALLBACK_CHAIN"] == "qwen"
    assert env["SKYNET_OPENCLAW_STAGE_CHAIN"] == "qwen"
    assert env["SKYNET_PLANNER_ROUTER_FALLBACK_ENABLED"] == "0"
    assert env["SKYNET_CONTROL_LOOP_ROUTER_FALLBACK_ENABLED"] == "0"


def test_live_e2e_cleanup_config_defaults(monkeypatch) -> None:
    for key in (
        "SKYNET_LIVE_E2E_CLEANUP_AFTER_RUN",
        "SKYNET_LIVE_E2E_CLEANUP_TARGETS",
        "SKYNET_LIVE_E2E_CLEANUP_GRACE_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = gateway_config.get_live_e2e_cleanup_config()
    assert cfg["enabled"] is True
    assert cfg["targets"] == ["worker_launcher", "worker_agent"]
    assert cfg["grace_seconds"] == 5


def test_live_e2e_cleanup_config_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_LIVE_E2E_CLEANUP_AFTER_RUN", "0")
    monkeypatch.setenv(
        "SKYNET_LIVE_E2E_CLEANUP_TARGETS",
        "live_runner,worker_launcher,invalid,worker_agent",
    )
    monkeypatch.setenv("SKYNET_LIVE_E2E_CLEANUP_GRACE_SECONDS", "11")

    cfg = gateway_config.get_live_e2e_cleanup_config()
    assert cfg["enabled"] is False
    assert cfg["targets"] == ["live_runner", "worker_launcher", "worker_agent"]
    assert cfg["grace_seconds"] == 11


def test_live_e2e_worker_bootstrap_config_defaults(monkeypatch) -> None:
    for key in (
        "SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_ENABLED",
        "SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_SCRIPT",
        "SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_ENV_FILE",
        "SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_PYTHON",
        "SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_WAIT_SECONDS",
        "SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_POLL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = gateway_config.get_live_e2e_worker_bootstrap_config()
    assert cfg["enabled"] is True
    assert cfg["script"] == "scripts/run_worker_agent.ps1"
    assert cfg["env_file"] == ".env.worker-agent"
    assert cfg["python_path"] == ""
    assert cfg["wait_seconds"] == 60
    assert cfg["poll_seconds"] == 3


def test_live_e2e_worker_bootstrap_config_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_ENABLED", "0")
    monkeypatch.setenv("SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_SCRIPT", "scripts/custom_worker.ps1")
    monkeypatch.setenv("SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_ENV_FILE", ".env.custom-worker")
    monkeypatch.setenv("SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_PYTHON", "E:/MyProjects/skynet/venv/Scripts/python.exe")
    monkeypatch.setenv("SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_WAIT_SECONDS", "75")
    monkeypatch.setenv("SKYNET_LIVE_E2E_WORKER_BOOTSTRAP_POLL_SECONDS", "5")

    cfg = gateway_config.get_live_e2e_worker_bootstrap_config()
    assert cfg["enabled"] is False
    assert cfg["script"] == "scripts/custom_worker.ps1"
    assert cfg["env_file"] == ".env.custom-worker"
    assert cfg["python_path"] == "E:/MyProjects/skynet/venv/Scripts/python.exe"
    assert cfg["wait_seconds"] == 75
    assert cfg["poll_seconds"] == 5


@pytest.mark.asyncio
async def test_container_snapshot_uses_configured_tail_counts(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "diag.pem"
    key_path.write_text("key", encoding="utf-8")
    calls: list[str] = []

    async def _fake_run(cmd: list[str], *, timeout_s: float) -> tuple[int, str, str]:
        calls.append(" ".join(cmd))
        return 0, "2026-03-09T12:00:00Z hello\n", ""

    monkeypatch.setattr(
        live_diagnostics,
        "resolve_container_log_stream_ssh",
        lambda *_args, **_kwargs: {
            "host": "ec2.example",
            "user": "ubuntu",
            "key": str(key_path),
            "key_source": "test",
            "port": 22,
        },
    )
    monkeypatch.setattr(live_diagnostics, "_run_capture_command", _fake_run)

    diagnostics = live_diagnostics.LiveContainerDiagnostics(
        trace_fn=lambda *_args, **_kwargs: None,
        config_override={
            "sources": ["openclaw-gateway", "worker"],
            "stream_enabled": False,
            "tail_default": 55,
            "tail_overrides": {"openclaw-gateway": 200},
        },
    )
    tails, errors = await diagnostics._capture_snapshot_bundle()

    assert not errors
    assert tails["openclaw-gateway"] == ["2026-03-09T12:00:00Z hello"]
    assert tails["worker"] == ["2026-03-09T12:00:00Z hello"]
    assert any("--tail 200 --timestamps openclaw-gateway" in cmd for cmd in calls)
    assert any("--tail 55 --timestamps worker" in cmd for cmd in calls)


def test_live_run_cleanup_manager_terminates_matching_repo_processes(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []
    terminated: list[tuple[int, int]] = []
    repo_root = str(live_diagnostics.REPO_ROOT)

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))

    monkeypatch.setattr(
        live_diagnostics,
        "_list_local_processes",
        lambda: [
            {
                "pid": 4100,
                "ppid": 1,
                "name": "powershell.exe",
                "command_line": f'powershell -File "{repo_root}\\scripts\\run_worker_agent.ps1"',
            },
            {
                "pid": 4200,
                "ppid": 4100,
                "name": "python.exe",
                "command_line": f'"{repo_root}\\venv\\Scripts\\python.exe" "{repo_root}\\openclaw-agent\\main.py"',
            },
            {
                "pid": 4300,
                "ppid": 1,
                "name": "python.exe",
                "command_line": '"C:\\elsewhere\\python.exe" "C:\\elsewhere\\openclaw-agent\\main.py"',
            },
        ],
    )
    monkeypatch.setattr(
        live_diagnostics,
        "_terminate_process_tree",
        lambda pid, *, grace_seconds: terminated.append((pid, grace_seconds)) or {"status": "terminated", "detail": "ok"},
    )

    cleanup = live_diagnostics.LiveRunCleanupManager(
        trace_fn=_trace,
        config_override={
            "enabled": True,
            "targets": ["worker_launcher", "worker_agent"],
            "grace_seconds": 7,
        },
    )
    cleanup.cleanup(reason="unit_test")

    assert terminated == [(4100, 7), (4200, 7)]
    assert any(event == "test.cleanup.start" for event, _fields in events)
    assert any(event == "test.cleanup.end" for event, _fields in events)


def test_live_run_cleanup_manager_terminates_registered_subprocesses(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []
    terminated: list[tuple[int, int]] = []

    class _Proc:
        pid = 5100

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))

    monkeypatch.setattr(live_diagnostics, "_list_local_processes", lambda: [])
    monkeypatch.setattr(
        live_diagnostics,
        "_terminate_process_tree",
        lambda pid, *, grace_seconds: terminated.append((pid, grace_seconds)) or {"status": "terminated", "detail": "ok"},
    )

    cleanup = live_diagnostics.LiveRunCleanupManager(
        trace_fn=_trace,
        config_override={"enabled": True, "targets": ["worker_launcher"], "grace_seconds": 3},
    )
    cleanup.register_subprocess(_Proc(), label="telegram_real_pytest")
    cleanup.cleanup(reason="interrupt")

    assert terminated == [(5100, 3)]
    assert any(
        event == "test.cleanup.item" and fields.get("target") == "registered_subprocess"
        for event, fields in events
    )


@pytest.mark.asyncio
async def test_container_diagnostics_require_stream_fails_fast(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))

    monkeypatch.setattr(live_diagnostics, "resolve_container_log_stream_ssh", lambda *_args, **_kwargs: None)
    diagnostics = live_diagnostics.LiveContainerDiagnostics(
        trace_fn=_trace,
        config_override={
            "sources": ["openclaw-gateway"],
            "stream_enabled": True,
            "require_stream": True,
        },
    )

    with pytest.raises(AssertionError, match="CONTAINER_LOG_STREAM_UNAVAILABLE"):
        await diagnostics.start()
    assert any(event == "container.log.stream.error" for event, _fields in events)


@pytest.mark.asyncio
async def test_container_diagnostics_emit_bundle_on_success_and_failure(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))

    async def _fake_capture(self) -> tuple[dict[str, list[str]], list[str]]:
        return {"openclaw-gateway": ["line"]}, []

    monkeypatch.setattr(
        live_diagnostics.LiveContainerDiagnostics,
        "_capture_snapshot_bundle",
        _fake_capture,
    )
    diagnostics = live_diagnostics.LiveContainerDiagnostics(
        trace_fn=_trace,
        config_override={"sources": ["openclaw-gateway"], "stream_enabled": False},
    )

    await diagnostics.emit_bundle(status="ok", reason="clean_exit")
    await diagnostics.emit_bundle(status="fail", reason="flow_failed")

    assert [event for event, _fields in events] == ["container.log.bundle", "container.log.bundle"]
    assert events[0][1]["status"] == "ok"
    assert events[0][1]["reason"] == "clean_exit"
    assert events[1][1]["status"] == "fail"
    assert events[1][1]["reason"] == "flow_failed"


@pytest.mark.asyncio
async def test_run_live_e2e_preflight_fails_on_transport_mismatch(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "diag.pem"
    key_path.write_text("key", encoding="utf-8")
    events: list[tuple[str, dict]] = []

    async def _fake_local_status(*, url: str, timeout_seconds: int = 10) -> dict[str, object]:
        _ = (url, timeout_seconds)
        return {
            "live_e2e_active": True,
            "live_e2e_flow": "conversation",
            "live_e2e_effective_coding_stage_chain": ["qwen"],
            "primary_transport_mode": "ssh_fallback",
            "agent_connected": True,
            "websocket_health_ok": False,
            "coding_agents": {"qwen": "/usr/bin/qwen"},
        }

    monkeypatch.setattr(
        live_diagnostics,
        "resolve_container_log_stream_ssh",
        lambda *_args, **_kwargs: {
            "host": "ec2.example",
            "user": "ubuntu",
            "key": str(key_path),
            "key_source": "test",
            "port": 22,
        },
    )
    monkeypatch.setattr(live_diagnostics, "fetch_local_gateway_status", _fake_local_status)
    monkeypatch.setattr(
        live_diagnostics,
        "run_qwen_preflight_smoke_probe",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AssertionError, match="PREFLIGHT_TRANSPORT_MISMATCH"):
        await live_diagnostics.run_live_e2e_preflight(
            trace_fn=lambda event, **fields: events.append((event, fields)),
            flow="conversation",
            policy={
                "required_transport": "websocket_primary",
                "allow_fallback": False,
                "required_worker_agents": ["qwen"],
                "container_log": {"ssh_profile": "tunnel"},
                "diagnostics_profile": "tunnel",
                "status_probe_mode": "local_http",
                "require_telegram_poller": False,
                "qwen_smoke": {"enabled": False, "timeout_seconds": 45},
            },
            local_status_url="http://127.0.0.1:8766/status",
        )

    assert any(event == "preflight.start" for event, _fields in events)


@pytest.mark.asyncio
async def test_run_live_e2e_preflight_fails_when_live_policy_inactive(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "diag.pem"
    key_path.write_text("key", encoding="utf-8")

    async def _fake_local_status(*, url: str, timeout_seconds: int = 10) -> dict[str, object]:
        _ = (url, timeout_seconds)
        return {
            "live_e2e_active": False,
            "live_e2e_flow": "conversation",
            "live_e2e_effective_coding_stage_chain": ["qwen"],
            "primary_transport_mode": "websocket_primary",
            "agent_connected": True,
            "websocket_health_ok": True,
            "coding_agents": {"qwen": "/usr/bin/qwen"},
        }

    monkeypatch.setattr(
        live_diagnostics,
        "resolve_container_log_stream_ssh",
        lambda *_args, **_kwargs: {
            "host": "ec2.example",
            "user": "ubuntu",
            "key": str(key_path),
            "key_source": "test",
            "port": 22,
        },
    )
    monkeypatch.setattr(live_diagnostics, "fetch_local_gateway_status", _fake_local_status)
    monkeypatch.setattr(
        live_diagnostics,
        "run_qwen_preflight_smoke_probe",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AssertionError, match="PREFLIGHT_LIVE_POLICY_INACTIVE"):
        await live_diagnostics.run_live_e2e_preflight(
            trace_fn=lambda *_args, **_kwargs: None,
            flow="conversation",
            policy={
                "required_transport": "websocket_primary",
                "allow_fallback": False,
                "required_worker_agents": ["qwen"],
                "container_log": {"ssh_profile": "tunnel"},
                "diagnostics_profile": "tunnel",
                "status_probe_mode": "local_http",
                "require_telegram_poller": False,
                "qwen_smoke": {"enabled": False, "timeout_seconds": 45},
            },
            local_status_url="http://127.0.0.1:8766/status",
        )


@pytest.mark.asyncio
async def test_run_live_e2e_preflight_fails_when_build_revision_mismatches(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "diag.pem"
    key_path.write_text("key", encoding="utf-8")

    async def _fake_local_status(*, url: str, timeout_seconds: int = 10) -> dict[str, object]:
        _ = (url, timeout_seconds)
        return {
            "build_revision": "remote-rev",
            "live_e2e_active": True,
            "live_e2e_flow": "conversation",
            "live_e2e_effective_coding_stage_chain": ["qwen"],
            "primary_transport_mode": "websocket_primary",
            "agent_connected": True,
            "websocket_health_ok": True,
            "coding_agents": {"qwen": "/usr/bin/qwen"},
        }

    monkeypatch.setattr(
        live_diagnostics,
        "resolve_container_log_stream_ssh",
        lambda *_args, **_kwargs: {
            "host": "ec2.example",
            "user": "ubuntu",
            "key": str(key_path),
            "key_source": "test",
            "port": 22,
        },
    )
    monkeypatch.setattr(live_diagnostics, "fetch_local_gateway_status", _fake_local_status)
    monkeypatch.setattr(
        live_diagnostics,
        "run_qwen_preflight_smoke_probe",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AssertionError, match="PREFLIGHT_BUILD_REVISION_MISMATCH"):
        await live_diagnostics.run_live_e2e_preflight(
            trace_fn=lambda *_args, **_kwargs: None,
            flow="conversation",
            policy={
                "required_transport": "websocket_primary",
                "allow_fallback": False,
                "required_worker_agents": ["qwen"],
                "container_log": {"ssh_profile": "tunnel"},
                "diagnostics_profile": "tunnel",
                "status_probe_mode": "local_http",
                "expected_remote_build_revision": "local-rev",
                "require_telegram_poller": False,
                "qwen_smoke": {"enabled": False, "timeout_seconds": 45},
            },
            local_status_url="http://127.0.0.1:8766/status",
        )


@pytest.mark.asyncio
async def test_run_live_e2e_preflight_accepts_short_build_revision_prefix(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "diag.pem"
    key_path.write_text("key", encoding="utf-8")

    async def _fake_local_status(*, url: str, timeout_seconds: int = 10) -> dict[str, object]:
        _ = (url, timeout_seconds)
        return {
            "build_revision": "756aae766bd2401a74b2c3db7a299495008e734a",
            "live_e2e_active": True,
            "live_e2e_flow": "conversation",
            "live_e2e_effective_coding_stage_chain": ["qwen"],
            "primary_transport_mode": "websocket_primary",
            "agent_connected": True,
            "websocket_health_ok": True,
            "coding_agents": {"qwen": "/usr/bin/qwen"},
        }

    monkeypatch.setattr(
        live_diagnostics,
        "resolve_container_log_stream_ssh",
        lambda *_args, **_kwargs: {
            "host": "ec2.example",
            "user": "ubuntu",
            "key": str(key_path),
            "key_source": "test",
            "port": 22,
        },
    )
    monkeypatch.setattr(live_diagnostics, "fetch_local_gateway_status", _fake_local_status)
    smoke = AsyncMock(return_value=None)
    monkeypatch.setattr(live_diagnostics, "run_qwen_preflight_smoke_probe", smoke)

    await live_diagnostics.run_live_e2e_preflight(
        trace_fn=lambda *_args, **_kwargs: None,
        flow="conversation",
        policy={
            "required_transport": "websocket_primary",
            "allow_fallback": False,
            "required_worker_agents": ["qwen"],
            "container_log": {"ssh_profile": "tunnel"},
            "diagnostics_profile": "tunnel",
            "status_probe_mode": "local_http",
            "expected_remote_build_revision": "756aae7",
            "require_telegram_poller": False,
            "qwen_smoke": {"enabled": False, "timeout_seconds": 45},
        },
        local_status_url="http://127.0.0.1:8766/status",
    )

    smoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_live_e2e_preflight_invokes_qwen_smoke_probe(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "diag.pem"
    key_path.write_text("key", encoding="utf-8")
    calls: list[tuple[str, dict]] = []

    async def _fake_local_status(*, url: str, timeout_seconds: int = 10) -> dict[str, object]:
        _ = (url, timeout_seconds)
        return {
            "live_e2e_active": True,
            "live_e2e_flow": "conversation",
            "live_e2e_effective_coding_stage_chain": ["qwen"],
            "primary_transport_mode": "websocket_primary",
            "agent_connected": True,
            "websocket_health_ok": True,
            "coding_agents": {"qwen": "/usr/bin/qwen"},
        }

    async def _fake_qwen_smoke_probe(**kwargs):
        calls.append((str(kwargs.get("flow") or ""), dict(kwargs.get("policy") or {})))

    monkeypatch.setattr(
        live_diagnostics,
        "resolve_container_log_stream_ssh",
        lambda *_args, **_kwargs: {
            "host": "ec2.example",
            "user": "ubuntu",
            "key": str(key_path),
            "key_source": "test",
            "port": 22,
        },
    )
    monkeypatch.setattr(live_diagnostics, "fetch_local_gateway_status", _fake_local_status)
    monkeypatch.setattr(live_diagnostics, "run_qwen_preflight_smoke_probe", _fake_qwen_smoke_probe)

    await live_diagnostics.run_live_e2e_preflight(
        trace_fn=lambda *_args, **_kwargs: None,
        flow="conversation",
        policy={
            "required_transport": "websocket_primary",
            "allow_fallback": False,
            "required_worker_agents": ["qwen"],
            "container_log": {"ssh_profile": "tunnel"},
            "diagnostics_profile": "tunnel",
            "status_probe_mode": "local_http",
            "require_telegram_poller": False,
            "qwen_smoke": {"enabled": True, "timeout_seconds": 45},
        },
        local_status_url="http://127.0.0.1:8766/status",
    )

    assert calls
    assert calls[0][0] == "conversation"


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
