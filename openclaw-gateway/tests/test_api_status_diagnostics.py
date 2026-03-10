from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import api as gateway_api


class _StubSSHExecutor:
    host = "host.docker.internal"
    port = 2222

    def __init__(self, *, configured: bool, ok: bool, detail: str) -> None:
        self._configured = configured
        self._ok = ok
        self._detail = detail

    def is_configured(self) -> bool:
        return self._configured

    async def health_check(self) -> tuple[bool, str]:
        return self._ok, self._detail

    def get_diagnostics(self) -> dict[str, object]:
        return {
            "ssh_health_ok": self._ok,
            "ssh_error_category": "capacity",
            "ssh_failure_streak": 3,
            "ssh_circuit_open_until": 1234567890,
            "ssh_endpoint": "host.docker.internal:2222",
        }


@pytest.mark.asyncio
async def test_status_includes_ssh_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSSHExecutor(configured=True, ok=False, detail="SSH circuit open")
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setenv("SKYNET_E2E_LIVE", "1")
    monkeypatch.setattr(gateway_api, "get_ssh_executor", lambda: stub)
    monkeypatch.setattr(gateway_api, "is_agent_connected", lambda: False)
    monkeypatch.setattr(
        gateway_api,
        "get_telegram_poller_status",
        lambda: {
            "telegram_poller_state": "blocked",
            "telegram_poller_lease_name": "telegram-poller:test",
            "telegram_poller_lease_owner": "gw-remote",
            "telegram_poller_last_conflict_at": "",
            "telegram_poller_conflict_count": 0,
            "telegram_poller_lock_healthy": False,
            "telegram_poller_last_error": "foreign_lease_active",
            "telegram_poller_lease_enabled": True,
            "telegram_poller_gateway_id": "gw-local",
        },
    )
    monkeypatch.setattr(
        gateway_api,
        "get_agent_status",
        lambda: {
            "worker_id": "",
            "agent_last_hello_at": "",
            "agent_last_heartbeat_at": "",
            "websocket_health_ok": False,
            "websocket_error_category": "disconnected",
            "websocket_failure_streak": 2,
            "fallback_last_reason": "agent_disconnected",
            "websocket_log_mirror_enabled": True,
            "websocket_log_mirror_loop_bound": True,
            "websocket_log_mirror_last_send_at": "2026-03-06T10:00:00+00:00",
            "websocket_log_mirror_last_ack_at": "2026-03-06T10:00:01+00:00",
            "websocket_log_mirror_last_error": "",
            "worker_capabilities": [],
            "coding_agents": {},
        },
    )

    response = await gateway_api.handle_status(SimpleNamespace())
    payload = json.loads(response.text)

    assert payload["primary_transport_mode"] == "ssh_fallback"
    assert payload["agent_connected"] is False
    assert payload["websocket_health_ok"] is False
    assert payload["fallback_last_reason"] == "agent_disconnected"
    assert payload["websocket_log_mirror_enabled"] is True
    assert payload["websocket_log_mirror_loop_bound"] is True
    assert payload["ssh_fallback_enabled"] is True
    assert payload["ssh_fallback_healthy"] is False
    assert payload["execution_mode"] == "ssh_tunnel"
    assert payload["execution_mode_effective"] == "ssh_tunnel"
    assert payload["ssh_health_ok"] is False
    assert payload["ssh_error_category"] == "capacity"
    assert payload["ssh_failure_streak"] == 3
    assert payload["ssh_circuit_open_until"] == 1234567890
    assert payload["ssh_endpoint"] == "host.docker.internal:2222"
    assert payload["telegram_poller_state"] == "blocked"
    assert payload["telegram_poller_lease_owner"] == "gw-remote"
    assert payload["telegram_poller_lock_healthy"] is False
    assert payload["live_e2e_active"] is True
    assert payload["live_e2e_effective_coding_stage_chain"] == ["qwen"]


@pytest.mark.asyncio
async def test_status_prefers_websocket_primary_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSSHExecutor(configured=True, ok=True, detail="SSH healthy")
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "agent_preferred")
    monkeypatch.setenv("SKYNET_E2E_LIVE", "1")
    monkeypatch.setattr(gateway_api, "get_ssh_executor", lambda: stub)
    monkeypatch.setattr(gateway_api, "is_agent_connected", lambda: True)
    monkeypatch.setattr(
        gateway_api,
        "get_telegram_poller_status",
        lambda: {
            "telegram_poller_state": "running",
            "telegram_poller_lease_name": "telegram-poller:test",
            "telegram_poller_lease_owner": "openclaw",
            "telegram_poller_last_conflict_at": "",
            "telegram_poller_conflict_count": 0,
            "telegram_poller_lock_healthy": True,
            "telegram_poller_last_error": "",
            "telegram_poller_lease_enabled": True,
            "telegram_poller_gateway_id": "openclaw",
        },
    )
    monkeypatch.setattr(
        gateway_api,
        "get_agent_status",
        lambda: {
            "worker_id": "worker-primary",
            "agent_last_hello_at": "2026-03-06T10:00:00+00:00",
            "agent_last_heartbeat_at": "2026-03-06T10:00:01+00:00",
            "websocket_health_ok": True,
            "websocket_error_category": "",
            "websocket_failure_streak": 0,
            "fallback_last_reason": "",
            "websocket_log_mirror_enabled": True,
            "websocket_log_mirror_loop_bound": True,
            "websocket_log_mirror_last_send_at": "2026-03-06T10:00:02+00:00",
            "websocket_log_mirror_last_ack_at": "2026-03-06T10:00:03+00:00",
            "websocket_log_mirror_last_error": "",
            "worker_capabilities": ["run_coding_agent"],
            "coding_agents": {"codex": "codex"},
        },
    )

    response = await gateway_api.handle_status(SimpleNamespace())
    payload = json.loads(response.text)

    assert payload["primary_transport_mode"] == "websocket_primary"
    assert payload["fallback_ready"] is True
    assert payload["worker_id"] == "worker-primary"
    assert payload["websocket_health_ok"] is True
    assert payload["telegram_poller_state"] == "running"
    assert payload["telegram_poller_lock_healthy"] is True
    assert payload["live_e2e_active"] is True
