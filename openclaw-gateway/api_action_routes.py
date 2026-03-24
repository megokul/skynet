from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import aiosqlite
from aiohttp import web


_idempotency_inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
_idempotency_lock = asyncio.Lock()


def action_key(task_id: str | None, idempotency_key: str | None) -> str | None:
    tid = str(task_id or "").strip()
    key = str(idempotency_key or "").strip()
    if not tid or not key:
        return None
    return f"{tid}:{key}"


def idempotency_db(request: web.Request) -> aiosqlite.Connection | None:
    return request.app.get("idempotency_db")


async def load_cached_result(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    async with db.execute(
        """
        SELECT response_json
        FROM action_idempotency
        WHERE task_id = ? AND idempotency_key = ?
        """,
        (task_id, idempotency_key),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def store_cached_result(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    idempotency_key: str,
    result: dict[str, Any],
) -> None:
    await db.execute(
        """
        INSERT OR REPLACE INTO action_idempotency (
            task_id, idempotency_key, response_json, created_at
        ) VALUES (?, ?, ?, datetime('now'))
        """,
        (task_id, idempotency_key, json.dumps(result, default=str)),
    )
    await db.commit()


async def handle_action_route(
    request: web.Request,
    *,
    send_action: Callable[..., Any],
    maybe_record_qwen_probe: Callable[[str, dict[str, Any], dict[str, Any]], None],
) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body."}, status=400)

    action = body.get("action")
    if not action or not isinstance(action, str):
        return web.json_response({"error": "Missing 'action' field."}, status=400)

    params = body.get("params", {})
    confirmed = body.get("confirmed", False) is True
    task_id = body.get("task_id")
    idempotency_key_value = body.get("idempotency_key")
    key = action_key(task_id, idempotency_key_value)
    db = idempotency_db(request)

    if idempotency_key_value and not task_id:
        return web.json_response({"error": "idempotency_key requires task_id."}, status=400)

    if key and db is not None:
        cached = await load_cached_result(
            db,
            task_id=str(task_id),
            idempotency_key=str(idempotency_key_value),
        )
        if cached is not None:
            replay = dict(cached)
            replay["idempotent_replay"] = True
            return web.json_response(replay)

    is_owner = False
    inflight_future: asyncio.Future[dict[str, Any]] | None = None
    if key:
        async with _idempotency_lock:
            existing = _idempotency_inflight.get(key)
            if existing is not None:
                inflight_future = existing
            else:
                loop = asyncio.get_running_loop()
                inflight_future = loop.create_future()
                _idempotency_inflight[key] = inflight_future
                is_owner = True

        if not is_owner and inflight_future is not None:
            try:
                result = await inflight_future
            except asyncio.TimeoutError:
                return web.json_response({"error": "Agent did not respond in time."}, status=504)
            except Exception as exc:  # pragma: no cover
                return web.json_response({"error": str(exc)}, status=503)
            replay = dict(result)
            replay["idempotent_replay"] = True
            return web.json_response(replay)

    try:
        result = await send_action(
            action,
            params,
            confirmed=confirmed,
            task_id=str(task_id) if task_id else None,
            idempotency_key=str(idempotency_key_value) if idempotency_key_value else None,
        )
        maybe_record_qwen_probe(action, params, result if isinstance(result, dict) else {})
        if key and db is not None:
            await store_cached_result(
                db,
                task_id=str(task_id),
                idempotency_key=str(idempotency_key_value),
                result=result,
            )
        if is_owner and inflight_future is not None and not inflight_future.done():
            inflight_future.set_result(result)
        return web.json_response(result)
    except asyncio.TimeoutError:
        maybe_record_qwen_probe(
            action,
            params,
            {"result": {"returncode": 1, "output_contract": "timeout", "stderr": "timeout"}},
        )
        if is_owner and inflight_future is not None and not inflight_future.done():
            inflight_future.set_exception(asyncio.TimeoutError())
        return web.json_response({"error": "Agent did not respond in time."}, status=504)
    except RuntimeError as exc:
        maybe_record_qwen_probe(
            action,
            params,
            {
                "result": {
                    "returncode": 1,
                    "output_contract": "gateway_runtime_error",
                    "stderr": str(exc),
                }
            },
        )
        if is_owner and inflight_future is not None and not inflight_future.done():
            inflight_future.set_exception(exc)
        return web.json_response({"error": str(exc)}, status=503)
    finally:
        if key and is_owner:
            async with _idempotency_lock:
                _idempotency_inflight.pop(key, None)


async def handle_emergency_stop_route(
    request: web.Request,
    *,
    is_agent_connected: Callable[[], bool],
    get_ssh_executor: Callable[[], Any],
    send_emergency_stop: Callable[[], Any],
) -> web.Response:
    del request
    if not is_agent_connected() and get_ssh_executor().is_configured():
        return web.json_response({"status": "not_applicable_in_ssh_mode"})
    try:
        await send_emergency_stop()
        return web.json_response({"status": "emergency_stop_sent"})
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)


async def handle_resume_route(
    request: web.Request,
    *,
    is_agent_connected: Callable[[], bool],
    get_ssh_executor: Callable[[], Any],
    send_resume: Callable[[], Any],
) -> web.Response:
    del request
    if not is_agent_connected() and get_ssh_executor().is_configured():
        return web.json_response({"status": "not_applicable_in_ssh_mode"})
    try:
        await send_resume()
        return web.json_response({"status": "resume_sent"})
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)
