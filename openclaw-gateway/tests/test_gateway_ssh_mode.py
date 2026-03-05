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


def test_is_acp_control_plane_mode_disabled_by_ssh_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setattr(gateway.cfg, "ORCHESTRATION_MODE", "acp_first")
    monkeypatch.setattr(gateway.cfg, "ORCHESTRATION_ALLOW_ACP_WITH_SSH", False)
    monkeypatch.setattr(gateway.cfg, "OPENCLAW_AGENT_HOSTING", "ec2_control")
    assert gateway._is_acp_control_plane_mode() is False


def test_is_acp_control_plane_mode_override_allows_ssh_plus_acp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setattr(gateway.cfg, "ORCHESTRATION_MODE", "acp_first")
    monkeypatch.setattr(gateway.cfg, "ORCHESTRATION_ALLOW_ACP_WITH_SSH", True)
    monkeypatch.setattr(gateway.cfg, "OPENCLAW_AGENT_HOSTING", "ec2_control")
    assert gateway._is_acp_control_plane_mode() is True


@pytest.mark.asyncio
async def test_send_action_ssh_mode_requires_configured_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "ssh_tunnel")
    monkeypatch.setattr(gateway, "_agent_ws", object())

    import ssh_tunnel_executor

    monkeypatch.setattr(ssh_tunnel_executor, "get_ssh_executor", lambda: _StubSSHExecutor(configured=False))

    with pytest.raises(RuntimeError, match="forces SSH"):
        await gateway.send_action("git_status", {})


@pytest.mark.asyncio
async def test_send_action_routes_coding_to_local_orchestration_when_acp_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "_agent_ws", None)
    monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "agent")
    monkeypatch.setattr(gateway.cfg, "ORCHESTRATION_MODE", "acp_first")
    monkeypatch.setattr(gateway.cfg, "OPENCLAW_AGENT_HOSTING", "ec2_control")

    called = {}

    async def _local(action, params):
        called["action"] = action
        called["params"] = params
        return {"status": "success", "result": {"returncode": 0, "stdout": "ok", "stderr": ""}}

    monkeypatch.setattr(gateway, "_run_local_orchestration_action", _local)

    import ssh_tunnel_executor

    monkeypatch.setattr(ssh_tunnel_executor, "get_ssh_executor", lambda: _StubSSHExecutor(configured=True))

    result = await gateway.send_action(
        "run_coding_agent",
        {"agent": "codex", "prompt": "hello", "timeout_seconds": 60},
    )
    assert called["action"] == "run_coding_agent"
    assert result["status"] == "success"
