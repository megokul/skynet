from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from audit import logger as audit_logger
from connection import websocket_client
from router import action_router


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def test_build_idempotency_key_falls_back_to_transport_id() -> None:
    key = action_router._build_idempotency_key(
        {"transport_id": "transport-1", "idempotency_key": "idem-1"}
    )
    assert key == "transport-1:idem-1"


def test_hello_payload_contains_required_fields() -> None:
    payload = websocket_client._hello_payload()
    assert payload["type"] == "agent_hello"
    assert payload["worker_id"]
    assert "agent_version" in payload
    assert "hostname" in payload
    assert "os" in payload
    assert isinstance(payload["capabilities"], list)
    assert isinstance(payload["allowed_roots"], list)
    assert isinstance(payload["coding_agents"], dict)
    assert "log_mirror_dir" in payload


def test_detect_coding_agents_includes_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(websocket_client, "_cfg_s", lambda _name, default="": default)
    monkeypatch.setattr(
        websocket_client.shutil,
        "which",
        lambda binary: f"C:/bin/{binary}.cmd" if binary in {"codex", "claude", "qwen"} else None,
    )

    detected = websocket_client._detect_coding_agents()

    assert detected["qwen"].endswith("qwen.cmd")


@pytest.mark.asyncio
async def test_log_write_emits_ack_and_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(websocket_client.config, "AGENT_LOG_MIRROR_DIR", str(tmp_path), raising=False)
    ws = _FakeWS()
    await websocket_client._handle_message(
        ws,
        json.dumps({"type": "log_write", "log_id": "log-1", "stream": "trace", "lines": ["hello"]}),
        asyncio.Lock(),
    )

    assert ws.sent[-1]["type"] == "log_write_ack"
    assert ws.sent[-1]["log_id"] == "log-1"
    assert (tmp_path / "skynet.trace.log").read_text(encoding="utf-8").strip() == "hello"


@pytest.mark.asyncio
async def test_action_request_sends_accept_then_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_route(message: dict[str, object]) -> dict[str, object]:
        return {"request_id": str(message.get("request_id") or ""), "status": "success", "action": str(message.get("action") or ""), "result": {"returncode": 0, "stdout": "ok", "stderr": ""}}

    monkeypatch.setattr(websocket_client, "route", _fake_route)
    ws = _FakeWS()
    await websocket_client._handle_message(
        ws,
        json.dumps(
            {
                "type": "action_request",
                "request_id": "req-1",
                "transport_id": "transport-1",
                "expect_accept": True,
                "action": "check_coding_agents",
                "params": {},
                "confirmed": True,
            }
        ),
        asyncio.Lock(),
    )

    assert [item["type"] for item in ws.sent] == ["action_accepted", "action_response"]
    assert ws.sent[0]["transport_id"] == "transport-1"
    assert ws.sent[1]["worker_id"] == websocket_client.config.WORKER_ID


@pytest.mark.asyncio
async def test_heartbeat_loop_sends_worker_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls = {"count": 0}

    async def _fake_sleep(_seconds: float) -> None:
        sleep_calls["count"] += 1
        if sleep_calls["count"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(websocket_client.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(websocket_client.config, "AGENT_HEARTBEAT_SECONDS", 5, raising=False)
    monkeypatch.setattr(websocket_client, "_detect_coding_agents", lambda: {"codex": "codex"})

    ws = _FakeWS()
    with pytest.raises(asyncio.CancelledError):
        await websocket_client._heartbeat_loop(ws, asyncio.Lock())

    assert ws.sent
    assert ws.sent[0]["type"] == "agent_heartbeat"
    assert ws.sent[0]["worker_id"] == websocket_client.config.WORKER_ID
    assert ws.sent[0]["coding_agents"] == {"codex": "codex"}


@pytest.mark.asyncio
async def test_route_survives_audit_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_path: str, _line: str) -> None:
        raise PermissionError("audit locked")

    monkeypatch.setattr(audit_logger, "_append_line", _boom)

    result = await action_router.route(
        {
            "request_id": "req-audit-fail",
            "action": "check_coding_agents",
            "params": {},
            "confirmed": True,
        }
    )

    assert result["status"] == "success"
    assert result["action"] == "check_coding_agents"
