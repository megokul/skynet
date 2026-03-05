from __future__ import annotations

import time
from unittest.mock import MagicMock

import paramiko
import pytest

from ssh_tunnel_executor import SSHTunnelExecutor


@pytest.fixture
def ssh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setenv("OPENCLAW_SSH_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("OPENCLAW_SSH_HOST", "example-host")
    monkeypatch.setenv("OPENCLAW_SSH_PORT", "2222")
    monkeypatch.setenv("OPENCLAW_SSH_USER", "tester")
    monkeypatch.setenv("OPENCLAW_SSH_KEY_PATH", "")
    monkeypatch.setenv("OPENCLAW_SSH_PASSWORD", "")
    monkeypatch.setenv("OPENCLAW_SSH_MAX_PARALLEL", "2")
    monkeypatch.setenv("OPENCLAW_SSH_CIRCUIT_BREAKER_SECONDS", "60")
    monkeypatch.setenv("OPENCLAW_SSH_CAPACITY_BACKOFF_SECONDS", "30")
    monkeypatch.setenv("OPENCLAW_SSH_HEALTH_PROBE_TIMEOUT", "6")


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("Exceeded MaxStartups while opening session", "capacity"),
        ("Permission denied (publickey).", "auth"),
        ("Error reading SSH protocol banner", "banner"),
        ("Operation timed out", "timeout"),
        ("Name or service not known", "unreachable"),
        ("something else", "unknown"),
    ],
)
def test_classify_ssh_error(ssh_env: None, detail: str, expected: str) -> None:
    executor = SSHTunnelExecutor()
    assert executor._classify_ssh_error(detail) == expected


def test_capacity_circuit_breaker_opens(ssh_env: None) -> None:
    executor = SSHTunnelExecutor()
    executor._record_ssh_failure("capacity")
    assert executor._circuit_remaining_seconds() == 0

    executor._record_ssh_failure("capacity")
    remaining = executor._circuit_remaining_seconds()
    assert remaining > 0

    diagnostics = executor.get_diagnostics()
    assert diagnostics["ssh_error_category"] == "capacity"
    assert diagnostics["ssh_failure_streak"] >= 2
    assert diagnostics["ssh_circuit_open_until"] > int(time.time())


def test_auth_failure_does_not_retry(ssh_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"connect": 0}

    class _FakeClient:
        def load_system_host_keys(self) -> None:
            return None

        def set_missing_host_key_policy(self, _policy) -> None:
            return None

        def connect(self, **_kwargs) -> None:
            calls["connect"] += 1
            raise paramiko.AuthenticationException("Authentication failed.")

        def close(self) -> None:
            return None

    monkeypatch.setattr(paramiko, "SSHClient", _FakeClient)
    executor = SSHTunnelExecutor()
    with pytest.raises(RuntimeError, match="authentication failed"):
        executor._connect(max_retries=3)
    assert calls["connect"] == 1


@pytest.mark.asyncio
async def test_health_check_short_circuits_when_breaker_open(ssh_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = SSHTunnelExecutor()
    executor._record_ssh_failure("capacity")
    executor._record_ssh_failure("capacity")

    def _should_not_probe() -> None:
        raise AssertionError("probe should not run while circuit is open")

    monkeypatch.setattr(executor, "_probe_sync", _should_not_probe)
    ok, detail = await executor.health_check()
    assert not ok
    assert "circuit open" in detail.lower()


@pytest.mark.parametrize(
    ("mode", "expected_tokens"),
    [
        ("danger_full_access", ["--dangerously-bypass-approvals-and-sandbox"]),
        ("workspace_write", ["--sandbox", "workspace-write"]),
        ("read_only", ["--sandbox", "read-only"]),
    ],
)
def test_codex_command_args_follow_write_mode(
    ssh_env: None,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_tokens: list[str],
) -> None:
    monkeypatch.setenv("SKYNET_CODEX_WRITE_MODE", mode)
    executor = SSHTunnelExecutor()
    args = executor._build_codex_command_args(binary="codex", prompt="hello world")
    assert args[:3] == ["codex", "exec", "--skip-git-repo-check"]
    for token in expected_tokens:
        assert token in args
    assert args[-1] == "hello world"


def test_codex_read_only_signature_is_promoted_to_setup_error(
    ssh_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKYNET_CODEX_WRITE_MODE", "workspace_write")
    executor = SSHTunnelExecutor()
    executor.remote_os = "linux"

    monkeypatch.setattr(executor, "_snapshot_working_tree", lambda **kwargs: [])
    monkeypatch.setattr(executor, "_diff_snapshots", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        executor,
        "_run_command",
        lambda *_args, **_kwargs: {
            "returncode": 0,
            "stdout": "approval: never\nsandbox: read-only\ncannot write files in this mode",
            "stderr": "",
        },
    )

    result = executor._run_coding_agent_native(
        client=MagicMock(),
        agent="codex",
        prompt="implement feature",
        cwd="E:/SKYNET-SANDBOX/Projects/tmp",
        timeout=120,
        model="",
    )
    assert int(result.get("returncode", 0)) == 1
    assert "CODEX_WRITE_BLOCKED" in str(result.get("stderr", ""))
