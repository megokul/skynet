from __future__ import annotations

import sys
from pathlib import Path

import pytest

import e2e_live
import test_e2e_telegram_real_live as telegram_real_live


class _Trace:
    path = "trace.log"

    def log(self, *_args, **_kwargs) -> None:
        return None


def test_infer_infra_category_capacity() -> None:
    text = "SSH action failed: Exceeded MaxStartups while opening session"
    assert e2e_live._infer_infra_category(text) == "capacity"


def test_check_env_fails_when_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_SSH_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_SSH_USER", raising=False)
    monkeypatch.setenv("SKYNET_E2E_TRANSPORT", "ssh")
    monkeypatch.setenv("SKYNET_E2E_FAIL_ON_SKIP", "1")

    def _fail(trace, message, *, detail=None):
        raise RuntimeError(f"{message} | {detail}")

    monkeypatch.setattr(e2e_live, "_fail", _fail)
    with pytest.raises(RuntimeError, match="environment validation failed"):
        e2e_live._check_env(_Trace())


def test_check_env_can_skip_when_not_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_SSH_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_SSH_USER", raising=False)
    monkeypatch.setenv("SKYNET_E2E_TRANSPORT", "ssh")
    monkeypatch.setenv("SKYNET_E2E_FAIL_ON_SKIP", "0")

    with pytest.raises(SystemExit) as exc:
        e2e_live._check_env(_Trace())
    assert exc.value.code == 0


def test_terminal_coding_failure_text_detects_tracker_final_failure() -> None:
    text = (
        "Coding Progress [##------------------] 10%\n"
        "Phase: Finalization - Worker unavailable before coding started\n"
        "Status: Failed"
    )
    assert telegram_real_live._terminal_coding_failure_text(text) == text


def test_terminal_coding_failure_text_detects_direct_failure_message() -> None:
    text = "Worker not connected - cannot create project folder."
    assert telegram_real_live._terminal_coding_failure_text(text) == text


def test_strict_stage_policy_violation_detects_fallback_message() -> None:
    text = "⚠️ Stage qwen failed (no runnable files generated). Trying codex..."
    assert (
        telegram_real_live._strict_stage_policy_violation_text(
            text,
            allowed_stages={"qwen"},
        )
        == text
    )


def test_strict_stage_policy_violation_detects_tracker_stage_switch() -> None:
    text = (
        "Coding Progress [####----------------] 20%\n"
        "Phase: Milestone Execution - Running stage codex (2/2)\n"
        "Pipeline: stage=codex | runtime=ssh"
    )
    assert (
        telegram_real_live._strict_stage_policy_violation_text(
            text,
            allowed_stages={"qwen"},
        )
        == text
    )


def test_strict_stage_policy_violation_ignores_orchestration_loop_stage() -> None:
    text = (
        "Coding Progress [####----------------] 20%\n"
        "Phase: Director - Building director contract\n"
        "Pipeline: stage=loop_v2 | runtime=worker_agent | graph=30 | transport=websocket_primary"
    )
    assert (
        telegram_real_live._strict_stage_policy_violation_text(
            text,
            allowed_stages={"qwen"},
        )
        == ""
    )


def test_terminal_bot_failure_text_detects_ai_unavailable() -> None:
    text = "AI is unavailable right now. Please try again."
    assert telegram_real_live._terminal_bot_failure_text(text) == text


@pytest.mark.asyncio
async def test_wait_for_bot_message_raises_terminal_bot_failure() -> None:
    class _Message:
        id = 101
        out = False
        message = "AI is unavailable right now. Please try again."
        buttons = []

    class _Client:
        async def get_messages(self, _bot_entity, limit: int = 20):
            _ = limit
            return [_Message()]

    events: list[tuple[str, dict]] = []

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))

    with pytest.raises(telegram_real_live._TerminalBotFailure, match="Terminal bot failure encountered"):
        await telegram_real_live._wait_for_bot_message(
            _Client(),
            object(),
            0,
            timeout_s=5,
            trace_fn=_trace,
            step="await_plan_flow_round_1",
            predicate=lambda text, btns: bool(text.strip()) or bool(btns),
        )

    assert any(event == "telegram.wait.terminal_failure" for event, _fields in events)


@pytest.mark.asyncio
async def test_maybe_bootstrap_worker_starts_launcher_and_waits_until_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, dict]] = []
    popen_calls: list[list[str]] = []
    sleep_calls: list[int] = []

    script_path = tmp_path / "run_worker_agent.ps1"
    script_path.write_text("Write-Host test\n", encoding="utf-8")
    env_file = tmp_path / ".env.worker-agent"
    env_file.write_text("SKYNET_GATEWAY_URL=ws://127.0.0.1:18765/agent/ws\n", encoding="utf-8")

    class _Proc:
        pid = 4242
        returncode = None

        def poll(self) -> int | None:
            return self.returncode

    class _Cleanup:
        def __init__(self) -> None:
            self.registered: list[tuple[int, str]] = []

        def register_subprocess(self, process, *, label: str) -> None:
            self.registered.append((int(process.pid), label))

    async def _fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)

    statuses = iter(
        [
            {
                "live_e2e_active": True,
                "primary_transport_mode": "unavailable",
                "agent_connected": False,
                "websocket_health_ok": False,
                "coding_agents": {},
            },
            {
                "live_e2e_active": True,
                "primary_transport_mode": "websocket_primary",
                "agent_connected": True,
                "websocket_health_ok": True,
                "coding_agents": {"qwen": "C:/tools/qwen.cmd"},
                "worker_id": "worker-primary",
            },
        ]
    )

    monkeypatch.setattr(
        e2e_live.subprocess,
        "Popen",
        lambda cmd, **kwargs: popen_calls.append(list(cmd)) or _Proc(),
    )

    async def _fake_remote_status(**_kwargs):
        return next(statuses)

    monkeypatch.setattr(e2e_live, "fetch_remote_gateway_status", _fake_remote_status)
    monkeypatch.setattr(e2e_live.asyncio, "sleep", _fake_sleep)

    trace = e2e_live.LiveTrace("unit-worker-bootstrap")
    cleanup = _Cleanup()
    policy = {
        "required_transport": "websocket_primary",
        "required_worker_agents": ["qwen"],
        "status_probe_mode": "remote_container_http",
        "diagnostics_profile": "tunnel",
        "remote_gateway_container": "openclaw-gateway",
        "remote_status_url": "http://localhost:8766/status",
        "worker_bootstrap": {
            "enabled": True,
            "script": str(script_path),
            "env_file": str(env_file),
            "python_path": "",
            "wait_seconds": 30,
            "poll_seconds": 2,
        },
    }
    original_log = trace.log

    def _trace(event: str, **fields) -> None:
        events.append((event, fields))
        original_log(event, **fields)

    monkeypatch.setattr(trace, "log", _trace)
    try:
        await e2e_live._maybe_bootstrap_worker(
            trace,
            cleanup,
            flow="telegram_real",
            policy=policy,
        )
    finally:
        trace.path.unlink(missing_ok=True)

    assert cleanup.registered == [(4242, "worker_bootstrap")]
    assert popen_calls, "Expected worker bootstrap process launch."
    assert "-PythonPath" in popen_calls[0]
    assert sys.executable in popen_calls[0]
    assert sleep_calls == []
    assert any(event == "worker.bootstrap.start" for event, _fields in events)
    assert any(event == "worker.bootstrap.ready" for event, _fields in events)
