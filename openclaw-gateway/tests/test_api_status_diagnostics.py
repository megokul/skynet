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
    monkeypatch.setattr(gateway_api, "get_ssh_executor", lambda: stub)
    monkeypatch.setattr(gateway_api, "is_agent_connected", lambda: False)

    response = await gateway_api.handle_status(SimpleNamespace())
    payload = json.loads(response.text)

    assert payload["agent_connected"] is False
    assert payload["ssh_fallback_enabled"] is True
    assert payload["ssh_fallback_healthy"] is False
    assert payload["execution_mode"] == "ssh_tunnel"
    assert payload["execution_mode_effective"] == "ssh_tunnel"
    assert payload["ssh_health_ok"] is False
    assert payload["ssh_error_category"] == "capacity"
    assert payload["ssh_failure_streak"] == 3
    assert payload["ssh_circuit_open_until"] == 1234567890
    assert payload["ssh_endpoint"] == "host.docker.internal:2222"

