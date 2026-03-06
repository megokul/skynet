from __future__ import annotations

import asyncio
import json

import pytest

import gateway
import ssh_tunnel_executor


class _SSHStub:
    def __init__(self, *, configured: bool = True, result: dict | None = None) -> None:
        self._configured = configured
        self._result = result or {"status": "success", "result": {"returncode": 0, "stdout": "ssh", "stderr": ""}}
        self.calls: list[tuple[str, dict, bool]] = []

    def is_configured(self) -> bool:
        return self._configured

    async def execute_action(self, action: str, params: dict, confirmed: bool = False) -> dict:
        self.calls.append((action, dict(params), confirmed))
        return dict(self._result)


class _HappyWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        message = json.loads(payload)
        self.sent.append(message)
        loop = asyncio.get_running_loop()
        loop.create_task(
            gateway._on_message(
                json.dumps(
                    {
                        "type": "action_accepted",
                        "request_id": message["request_id"],
                        "transport_id": message.get("transport_id", ""),
                        "worker_id": "worker-primary",
                        "accepted_at": "2026-03-06T12:00:00+00:00",
                    }
                )
            )
        )
        loop.create_task(
            gateway._on_message(
                json.dumps(
                    {
                        "type": "action_response",
                        "request_id": message["request_id"],
                        "transport_id": message.get("transport_id", ""),
                        "worker_id": "worker-primary",
                        "status": "success",
                        "action": message["action"],
                        "result": {"returncode": 0, "stdout": "ws-ok", "stderr": ""},
                    }
                )
            )
        )


class _BrokenWebSocket:
    async def send(self, payload: str) -> None:
        raise RuntimeError("websocket send failed")


class _AcceptThenDisconnectWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._send_count = 0

    async def send(self, payload: str) -> None:
        self._send_count += 1
        message = json.loads(payload)
        self.sent.append(message)
        if self._send_count == 1:
            loop = asyncio.get_running_loop()
            loop.create_task(
                gateway._on_message(
                    json.dumps(
                        {
                            "type": "action_accepted",
                            "request_id": message["request_id"],
                            "transport_id": message.get("transport_id", ""),
                            "worker_id": "worker-primary",
                            "accepted_at": "2026-03-06T12:00:00+00:00",
                        }
                    )
                )
            )
            gateway._agent_ws = None
            gateway.agent_connected.clear()


@pytest.fixture(autouse=True)
def _reset_gateway_state() -> None:
    gateway._agent_ws = None
    gateway._pending.clear()
    gateway._pending_log_acks.clear()
    gateway.agent_connected.clear()
    gateway._agent_state.update(
        {
            "worker_id": "worker-primary",
            "agent_version": "",
            "hostname": "",
            "os": "",
            "capabilities": [],
            "allowed_roots": [],
            "coding_agents": {},
            "log_mirror_dir": "",
            "last_hello_at": "",
            "last_hello_monotonic": 0.0,
            "last_heartbeat_at": "",
            "last_heartbeat_monotonic": 0.0,
            "queue_depth": 0,
        }
    )
    gateway._ws_failure_state.update(
        {
            "error_category": "",
            "failure_streak": 0,
            "last_error": "",
            "last_error_at": "",
            "last_fallback_reason": "",
        }
    )
    gateway._log_mirror_state.update(
        {
            "enabled": False,
            "loop_bound": False,
            "last_send_at": "",
            "last_ack_at": "",
            "last_error": "",
            "ack_required": False,
        }
    )


@pytest.mark.asyncio
async def test_send_action_uses_websocket_primary_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _HappyWebSocket()
    ssh = _SSHStub()
    gateway._agent_ws = ws
    gateway.agent_connected.set()
    gateway._agent_state["last_heartbeat_monotonic"] = asyncio.get_running_loop().time()

    monkeypatch.setattr(ssh_tunnel_executor, "get_ssh_executor", lambda: ssh)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_PRIMARY_ENABLED", True, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_FALLBACK_TO_SSH", True, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_ACCEPT_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_REPLAY_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(gateway.cfg, "get_str", lambda name, default="": "agent_preferred" if name == "OPENCLAW_EXECUTION_MODE" else default, raising=False)
    monkeypatch.setattr(gateway.cfg, "effective_orchestration_mode", lambda: "legacy", raising=False)

    result = await gateway.send_action(
        "check_coding_agents",
        {"project_id": "p1", "task_id": "t1", "graph_id": "g1", "node_key": "N1", "node_type": "work"},
        confirmed=True,
        task_id="t1",
    )

    assert result["status"] == "success"
    assert result["result"]["stdout"] == "ws-ok"
    assert ssh.calls == []
    assert ws.sent[0]["expect_accept"] is True
    assert ws.sent[0]["transport_id"]
    assert ws.sent[0]["idempotency_key"]


@pytest.mark.asyncio
async def test_send_action_falls_back_to_ssh_before_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway._agent_ws = _BrokenWebSocket()
    gateway.agent_connected.set()
    gateway._agent_state["last_heartbeat_monotonic"] = asyncio.get_running_loop().time()
    ssh = _SSHStub()

    monkeypatch.setattr(ssh_tunnel_executor, "get_ssh_executor", lambda: ssh)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_PRIMARY_ENABLED", True, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_FALLBACK_TO_SSH", True, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_ACCEPT_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_REPLAY_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(gateway.cfg, "get_str", lambda name, default="": "agent_preferred" if name == "OPENCLAW_EXECUTION_MODE" else default, raising=False)
    monkeypatch.setattr(gateway.cfg, "effective_orchestration_mode", lambda: "legacy", raising=False)

    result = await gateway.send_action(
        "check_coding_agents",
        {"project_id": "p1", "task_id": "t1"},
        confirmed=True,
        task_id="t1",
    )

    assert result["status"] == "success"
    assert ssh.calls and ssh.calls[0][0] == "check_coding_agents"
    assert gateway.get_agent_status()["fallback_last_reason"] == "websocket_send_failed"


@pytest.mark.asyncio
async def test_mutating_action_does_not_fallback_to_ssh_after_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway._agent_ws = _AcceptThenDisconnectWebSocket()
    gateway.agent_connected.set()
    gateway._agent_state["last_heartbeat_monotonic"] = asyncio.get_running_loop().time()
    ssh = _SSHStub()

    monkeypatch.setattr(ssh_tunnel_executor, "get_ssh_executor", lambda: ssh)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_PRIMARY_ENABLED", True, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_FALLBACK_TO_SSH", True, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_ACCEPT_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(gateway.cfg, "WEBSOCKET_REPLAY_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(gateway.cfg, "get_str", lambda name, default="": "agent_preferred" if name == "OPENCLAW_EXECUTION_MODE" else default, raising=False)
    monkeypatch.setattr(gateway.cfg, "effective_orchestration_mode", lambda: "legacy", raising=False)

    with pytest.raises(RuntimeError, match="WS_RESULT_INDETERMINATE"):
        await gateway.send_action(
            "run_coding_agent",
            {"project_id": "p1", "task_id": "t1"},
            confirmed=True,
            task_id="t1",
            timeout=1,
        )

    assert ssh.calls == []
