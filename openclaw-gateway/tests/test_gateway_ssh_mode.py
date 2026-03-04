from __future__ import annotations

import pytest

import gateway


class _StubSSHExecutor:
    def __init__(self, configured: bool) -> None:
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured

    async def execute_action(self, action, params, confirmed=False):  # pragma: no cover - defensive
        return {"status": "ok", "action": action, "result": {"returncode": 0}}


@pytest.mark.asyncio
async def test_send_action_ssh_mode_requires_configured_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setattr(gateway, "_agent_ws", object())

    import ssh_tunnel_executor

    monkeypatch.setattr(ssh_tunnel_executor, "get_ssh_executor", lambda: _StubSSHExecutor(configured=False))

    with pytest.raises(RuntimeError, match="forces SSH"):
        await gateway.send_action("git_status", {})

