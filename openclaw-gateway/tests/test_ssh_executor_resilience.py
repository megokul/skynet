from __future__ import annotations

import time

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
