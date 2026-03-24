from __future__ import annotations

import json

import aiosqlite
import pytest

import api_action_routes


class _Request:
    def __init__(self, body: dict[str, object], db: aiosqlite.Connection) -> None:
        self._body = body
        self.app = {"idempotency_db": db}

    async def json(self) -> dict[str, object]:
        return dict(self._body)


async def _create_idempotency_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """
        CREATE TABLE action_idempotency (
            task_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            response_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (task_id, idempotency_key)
        )
        """
    )
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_handle_action_route_replays_cached_idempotent_response() -> None:
    db = await _create_idempotency_db()
    send_calls: list[dict[str, object]] = []
    body = {
        "action": "check_coding_agents",
        "params": {"project_id": "proj-1"},
        "confirmed": True,
        "task_id": "task-1",
        "idempotency_key": "idem-1",
    }

    async def _send_action(action: str, params: dict[str, object], **kwargs) -> dict[str, object]:
        send_calls.append({"action": action, "params": dict(params), **kwargs})
        return {"status": "success", "result": {"returncode": 0, "stdout": "ok", "stderr": ""}}

    def _record_probe(_action: str, _params: dict[str, object], _result: dict[str, object]) -> None:
        return None

    first = await api_action_routes.handle_action_route(
        _Request(body, db),
        send_action=_send_action,
        maybe_record_qwen_probe=_record_probe,
    )
    second = await api_action_routes.handle_action_route(
        _Request(body, db),
        send_action=_send_action,
        maybe_record_qwen_probe=_record_probe,
    )

    first_payload = json.loads(first.text)
    second_payload = json.loads(second.text)

    assert send_calls == [
        {
            "action": "check_coding_agents",
            "params": {"project_id": "proj-1"},
            "confirmed": True,
            "task_id": "task-1",
            "idempotency_key": "idem-1",
        }
    ]
    assert first_payload == {
        "status": "success",
        "result": {"returncode": 0, "stdout": "ok", "stderr": ""},
    }
    assert second_payload == {
        "status": "success",
        "result": {"returncode": 0, "stdout": "ok", "stderr": ""},
        "idempotent_replay": True,
    }

    await db.close()
