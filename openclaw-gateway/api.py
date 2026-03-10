"""
SKYNET Gateway — HTTP API

Lightweight HTTP server on loopback (127.0.0.1:8766) that dispatches
action requests to the connected CHATHAN worker.

Endpoints:

    POST /action           Submit an action request, wait for result.
    POST /emergency-stop   Trigger emergency stop on CHATHAN worker.
    POST /resume           Resume worker after emergency stop.
    GET  /status           Check if a CHATHAN worker is connected.

This API is **only** bound to localhost so it is not reachable from
the internet.  If you need external access, put it behind an
authenticated reverse proxy (nginx + basic-auth, or an ALB).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiosqlite
from aiohttp import web

import gateway_config as bot_config
import gateway_config as cfg
from gateway import (
    get_agent_status,
    is_agent_connected,
    send_action,
    send_emergency_stop,
    send_resume,
)
from ssh_tunnel_executor import get_ssh_executor
from telegram_poller import get_telegram_poller_status

logger = logging.getLogger("skynet.api")


_SSH_ONLY_MODES = {"ssh", "ssh_tunnel", "tunnel", "ssh-only"}
_idempotency_inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
_idempotency_lock = asyncio.Lock()


def _force_ssh_mode(ssh_configured: bool) -> bool:
    """
    Force ssh mode.
    
    Purpose:
    - Implement `_force_ssh_mode` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `ssh_configured`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `bool` when available; otherwise side effects only.
    """

    del ssh_configured
    mode = cfg.get_str("OPENCLAW_EXECUTION_MODE", "").strip().lower()
    return mode in _SSH_ONLY_MODES


def _action_key(task_id: str | None, idempotency_key: str | None) -> str | None:
    """
    Action key.
    
    Purpose:
    - Implement `_action_key` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `task_id`: input used by this function to compute or route work.
    - `idempotency_key`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str | None` when available; otherwise side effects only.
    """

    tid = str(task_id or "").strip()
    key = str(idempotency_key or "").strip()
    if not tid or not key:
        return None
    return f"{tid}:{key}"


def _idempotency_db(request: web.Request) -> aiosqlite.Connection | None:
    """
    Idempotency db.
    
    Purpose:
    - Implement `_idempotency_db` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `aiosqlite.Connection | None` when available; otherwise side effects only.
    """

    return request.app.get("idempotency_db")


async def _load_cached_result(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    """
    Load cached result.
    
    Purpose:
    - Implement `_load_cached_result` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `task_id`: input used by this function to compute or route work.
    - `idempotency_key`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any] | None` when available; otherwise side effects only.
    """

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


async def _store_cached_result(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    idempotency_key: str,
    result: dict[str, Any],
) -> None:
    """
    Store cached result.
    
    Purpose:
    - Implement `_store_cached_result` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    - `task_id`: input used by this function to compute or route work.
    - `idempotency_key`: input used by this function to compute or route work.
    - `result`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    await db.execute(
        """
        INSERT OR REPLACE INTO action_idempotency (
            task_id, idempotency_key, response_json, created_at
        ) VALUES (?, ?, ?, datetime('now'))
        """,
        (task_id, idempotency_key, json.dumps(result, default=str)),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_status(request: web.Request) -> web.Response:
    """
    Handle status.
    
    Purpose:
    - Implement `handle_status` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `web.Response` when available; otherwise side effects only.
    """

    ssh_exec = get_ssh_executor()
    ssh_ok, ssh_detail = await ssh_exec.health_check()
    ssh_configured = ssh_exec.is_configured()
    mode = cfg.get_str("OPENCLAW_EXECUTION_MODE", "").strip().lower()
    force_ssh = mode in _SSH_ONLY_MODES
    diagnostics = ssh_exec.get_diagnostics()
    agent_status = get_agent_status()
    poller_status = get_telegram_poller_status()
    live_e2e_policy = cfg.get_live_e2e_policy()
    execution_mode_effective = "ssh_tunnel" if force_ssh else "agent_preferred"
    websocket_health_ok = bool(agent_status.get("websocket_health_ok", False))
    if force_ssh:
        primary_transport_mode = "ssh_fallback"
    elif websocket_health_ok:
        primary_transport_mode = "websocket_primary"
    elif ssh_configured:
        primary_transport_mode = "ssh_fallback"
    else:
        primary_transport_mode = "unavailable"
    return web.json_response({
        "primary_transport_mode": primary_transport_mode,
        "agent_connected": is_agent_connected(),
        "worker_id": str(agent_status.get("worker_id") or ""),
        "agent_last_hello_at": str(agent_status.get("agent_last_hello_at") or ""),
        "agent_last_heartbeat_at": str(agent_status.get("agent_last_heartbeat_at") or ""),
        "websocket_health_ok": websocket_health_ok,
        "websocket_error_category": str(agent_status.get("websocket_error_category") or ""),
        "websocket_failure_streak": int(agent_status.get("websocket_failure_streak", 0) or 0),
        "fallback_ready": bool(ssh_configured and ssh_ok),
        "fallback_last_reason": str(agent_status.get("fallback_last_reason") or ""),
        "websocket_log_mirror_enabled": bool(agent_status.get("websocket_log_mirror_enabled", False)),
        "websocket_log_mirror_loop_bound": bool(agent_status.get("websocket_log_mirror_loop_bound", False)),
        "websocket_log_mirror_last_send_at": str(agent_status.get("websocket_log_mirror_last_send_at") or ""),
        "websocket_log_mirror_last_ack_at": str(agent_status.get("websocket_log_mirror_last_ack_at") or ""),
        "websocket_log_mirror_last_error": str(agent_status.get("websocket_log_mirror_last_error") or ""),
        "ssh_fallback_enabled": ssh_configured,
        "ssh_fallback_healthy": ssh_ok,
        "ssh_fallback_target": ssh_detail,
        "execution_mode": execution_mode_effective,
        "execution_mode_effective": execution_mode_effective,
        "ssh_health_ok": bool(diagnostics.get("ssh_health_ok", ssh_ok)),
        "ssh_error_category": str(diagnostics.get("ssh_error_category", "")),
        "ssh_failure_streak": int(diagnostics.get("ssh_failure_streak", 0)),
        "ssh_circuit_open_until": int(diagnostics.get("ssh_circuit_open_until", 0)),
        "ssh_endpoint": str(diagnostics.get("ssh_endpoint", f"{ssh_exec.host}:{ssh_exec.port}")),
        "worker_capabilities": list(agent_status.get("worker_capabilities") or []),
        "coding_agents": dict(agent_status.get("coding_agents") or {}),
        "live_e2e_active": bool(live_e2e_policy.get("active", False)),
        "live_e2e_flow": str(live_e2e_policy.get("flow") or ""),
        "live_e2e_allow_fallback": bool(live_e2e_policy.get("allow_fallback", False)),
        "live_e2e_effective_coding_stage_chain": list(
            live_e2e_policy.get("effective_coding_stage_chain") or []
        ),
        **poller_status,
    })


async def handle_action(request: web.Request) -> web.Response:
    """
    POST /action
    Body: {
      "action": "git_status",
      "params": { "working_dir": "..." },
      "task_id": "task-123",                # optional
      "idempotency_key": "claim-token-xyz"  # optional, requires task_id
    }
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"error": "Invalid JSON body."}, status=400,
        )

    action = body.get("action")
    if not action or not isinstance(action, str):
        return web.json_response(
            {"error": "Missing 'action' field."}, status=400,
        )

    params = body.get("params", {})
    confirmed = body.get("confirmed", False) is True
    task_id = body.get("task_id")
    idempotency_key = body.get("idempotency_key")
    action_key = _action_key(task_id, idempotency_key)
    db = _idempotency_db(request)

    if idempotency_key and not task_id:
        return web.json_response(
            {"error": "idempotency_key requires task_id."}, status=400,
        )

    if action_key and db is not None:
        cached = await _load_cached_result(
            db,
            task_id=str(task_id),
            idempotency_key=str(idempotency_key),
        )
        if cached is not None:
            replay = dict(cached)
            replay["idempotent_replay"] = True
            return web.json_response(replay)

    is_owner = False
    inflight_future: asyncio.Future[dict[str, Any]] | None = None
    if action_key:
        async with _idempotency_lock:
            existing = _idempotency_inflight.get(action_key)
            if existing is not None:
                inflight_future = existing
            else:
                loop = asyncio.get_running_loop()
                inflight_future = loop.create_future()
                _idempotency_inflight[action_key] = inflight_future
                is_owner = True

        if not is_owner and inflight_future is not None:
            try:
                result = await inflight_future
            except asyncio.TimeoutError:
                return web.json_response({"error": "Agent did not respond in time."}, status=504)
            except Exception as exc:  # pragma: no cover - defensive fallback
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
            idempotency_key=str(idempotency_key) if idempotency_key else None,
        )
        if action_key and db is not None:
            await _store_cached_result(
                db,
                task_id=str(task_id),
                idempotency_key=str(idempotency_key),
                result=result,
            )
        if is_owner and inflight_future is not None and not inflight_future.done():
            inflight_future.set_result(result)
        return web.json_response(result)
    except asyncio.TimeoutError:
        if is_owner and inflight_future is not None and not inflight_future.done():
            inflight_future.set_exception(asyncio.TimeoutError())
        return web.json_response(
            {"error": "Agent did not respond in time."}, status=504,
        )
    except RuntimeError as exc:
        if is_owner and inflight_future is not None and not inflight_future.done():
            inflight_future.set_exception(exc)
        return web.json_response(
            {"error": str(exc)}, status=503,
        )
    finally:
        if action_key and is_owner:
            async with _idempotency_lock:
                _idempotency_inflight.pop(action_key, None)


async def handle_emergency_stop(request: web.Request) -> web.Response:
    """
    Handle emergency stop.
    
    Purpose:
    - Implement `handle_emergency_stop` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `web.Response` when available; otherwise side effects only.
    """

    if not is_agent_connected() and get_ssh_executor().is_configured():
        return web.json_response({"status": "not_applicable_in_ssh_mode"})
    try:
        await send_emergency_stop()
        return web.json_response({"status": "emergency_stop_sent"})
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)


async def handle_resume(request: web.Request) -> web.Response:
    """
    Handle resume.
    
    Purpose:
    - Implement `handle_resume` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `web.Response` when available; otherwise side effects only.
    """

    if not is_agent_connected() and get_ssh_executor().is_configured():
        return web.json_response({"status": "not_applicable_in_ssh_mode"})
    try:
        await send_resume()
        return web.json_response({"status": "resume_sent"})
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)


async def handle_get_profile(request: web.Request) -> web.Response:
    """
    Handle get profile.
    
    Purpose:
    - Implement `handle_get_profile` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `web.Response` when available; otherwise side effects only.
    """

    telegram_user_id = request.match_info.get("telegram_user_id", "").strip()
    if not telegram_user_id.isdigit():
        return web.json_response({"error": "telegram_user_id must be numeric."}, status=400)

    db = request.app.get("idempotency_db")
    if db is None:
        return web.json_response({"error": "Database not initialized."}, status=503)

    try:
        from db import store

        user = await store.get_user_by_telegram_id(db, int(telegram_user_id))
        if not user:
            return web.json_response({"error": "User not found."}, status=404)
        user_id = int(user["id"])
        facts = await store.list_profile_facts(db, user_id=user_id, active_only=True)
        prefs = await store.get_user_preferences(db, user_id=user_id)
        return web.json_response({
            "user": user,
            "facts": facts,
            "preferences": prefs,
        })
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_forget_profile(request: web.Request) -> web.Response:
    """
    Handle forget profile.
    
    Purpose:
    - Implement `handle_forget_profile` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `web.Response` when available; otherwise side effects only.
    """

    telegram_user_id = request.match_info.get("telegram_user_id", "").strip()
    if not telegram_user_id.isdigit():
        return web.json_response({"error": "telegram_user_id must be numeric."}, status=400)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body."}, status=400)

    key = str(payload.get("key_or_text", "")).strip()
    if not key:
        return web.json_response({"error": "Missing key_or_text."}, status=400)

    db = request.app.get("idempotency_db")
    if db is None:
        return web.json_response({"error": "Database not initialized."}, status=503)

    try:
        from db import store

        user = await store.get_user_by_telegram_id(db, int(telegram_user_id))
        if not user:
            return web.json_response({"error": "User not found."}, status=404)
        removed = await store.forget_profile_facts(
            db,
            user_id=int(user["id"]),
            key_or_text=key,
        )
        await store.add_memory_audit_log(
            db,
            user_id=int(user["id"]),
            action="forget_api",
            target_type="fact",
            target_key=key,
            detail=f"Removed facts: {removed}",
        )
        return web.json_response({"ok": True, "removed": removed})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_set_memory_policy(request: web.Request) -> web.Response:
    """
    Handle set memory policy.
    
    Purpose:
    - Implement `handle_set_memory_policy` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `web.Response` when available; otherwise side effects only.
    """

    telegram_user_id = request.match_info.get("telegram_user_id", "").strip()
    if not telegram_user_id.isdigit():
        return web.json_response({"error": "telegram_user_id must be numeric."}, status=400)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body."}, status=400)

    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return web.json_response({"error": "enabled must be boolean."}, status=400)

    db = request.app.get("idempotency_db")
    if db is None:
        return web.json_response({"error": "Database not initialized."}, status=503)

    try:
        from db import store

        user = await store.get_user_by_telegram_id(db, int(telegram_user_id))
        if not user:
            return web.json_response({"error": "User not found."}, status=404)
        user_id = int(user["id"])
        await store.set_user_memory_enabled(db, user_id=user_id, enabled=enabled)
        await store.add_memory_audit_log(
            db,
            user_id=user_id,
            action="memory_policy_api",
            target_type="policy",
            target_key="memory_enabled",
            detail=f"enabled={enabled}",
        )
        return web.json_response({"ok": True, "enabled": enabled})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

async def _ensure_idempotency_schema(db: aiosqlite.Connection) -> None:
    """
    Ensure idempotency schema.
    
    Purpose:
    - Implement `_ensure_idempotency_schema` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `db`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS action_idempotency (
            task_id          TEXT NOT NULL,
            idempotency_key  TEXT NOT NULL,
            response_json    TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (task_id, idempotency_key)
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_idempotency_created ON action_idempotency(created_at)"
    )
    await db.commit()


async def _cleanup_app(app: web.Application) -> None:
    """
    Cleanup app.
    
    Purpose:
    - Implement `_cleanup_app` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `app`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    db = app.get("idempotency_db")
    if db is not None:
        await db.close()


def create_app(*, idempotency_db: aiosqlite.Connection | None = None) -> web.Application:
    """
    Create app.
    
    Purpose:
    - Implement `create_app` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `idempotency_db`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `web.Application` when available; otherwise side effects only.
    """

    app = web.Application()
    app["idempotency_db"] = idempotency_db
    app.on_cleanup.append(_cleanup_app)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/action", handle_action)
    app.router.add_post("/emergency-stop", handle_emergency_stop)
    app.router.add_post("/resume", handle_resume)
    app.router.add_get("/profile/{telegram_user_id}", handle_get_profile)
    app.router.add_post("/profile/{telegram_user_id}/forget", handle_forget_profile)
    app.router.add_post("/profile/{telegram_user_id}/memory-policy", handle_set_memory_policy)
    return app


async def start_http_api() -> web.AppRunner:
    """Start the HTTP API server and return the runner."""
    idempotency_db = await aiosqlite.connect(bot_config.DB_PATH)
    idempotency_db.row_factory = aiosqlite.Row
    await _ensure_idempotency_schema(idempotency_db)

    app = create_app(idempotency_db=idempotency_db)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.HTTP_HOST, cfg.HTTP_PORT)
    await site.start()
    logger.info("HTTP API listening on http://%s:%d", cfg.HTTP_HOST, cfg.HTTP_PORT)
    return runner

