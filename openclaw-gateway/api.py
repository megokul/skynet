"""
SKYNET Gateway - HTTP API.

Bound to loopback only. External access must go through an authenticated proxy.
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite
from aiohttp import web

import api_action_routes
import api_profile_routes
import api_shared
import api_status_routes
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

_QWEN_PROBE_STATUS = api_shared.QWEN_PROBE_STATUS
_SSH_ONLY_MODES = api_shared.SSH_ONLY_MODES


def _utc_now_iso() -> str:
    return api_shared.utc_now_iso()


def _record_qwen_probe_result(
    *,
    probe_kind: str,
    status: str,
    output_contract: str = "",
    detail: str = "",
) -> None:
    api_shared.record_qwen_probe_result(
        probe_kind=probe_kind,
        status=status,
        output_contract=output_contract,
        detail=detail,
    )


def _maybe_record_qwen_probe(action: str, params: dict[str, Any], result: dict[str, Any]) -> None:
    api_shared.maybe_record_qwen_probe(action, params, result)


def _force_ssh_mode(ssh_configured: bool) -> bool:
    del ssh_configured
    mode = cfg.get_str("OPENCLAW_EXECUTION_MODE", "").strip().lower()
    return mode in _SSH_ONLY_MODES


def _action_key(task_id: str | None, idempotency_key: str | None) -> str | None:
    return api_action_routes.action_key(task_id, idempotency_key)


def _idempotency_db(request: web.Request) -> aiosqlite.Connection | None:
    return api_action_routes.idempotency_db(request)


async def _load_cached_result(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    return await api_action_routes.load_cached_result(
        db,
        task_id=task_id,
        idempotency_key=idempotency_key,
    )


async def _store_cached_result(
    db: aiosqlite.Connection,
    *,
    task_id: str,
    idempotency_key: str,
    result: dict[str, Any],
) -> None:
    await api_action_routes.store_cached_result(
        db,
        task_id=task_id,
        idempotency_key=idempotency_key,
        result=result,
    )


async def handle_status(request: web.Request) -> web.Response:
    return await api_status_routes.handle_status_route(
        request,
        cfg=cfg,
        qwen_probe_status=_QWEN_PROBE_STATUS,
        ssh_only_modes=_SSH_ONLY_MODES,
        get_ssh_executor=get_ssh_executor,
        get_agent_status=get_agent_status,
        is_agent_connected=is_agent_connected,
        get_telegram_poller_status=get_telegram_poller_status,
    )


async def handle_action(request: web.Request) -> web.Response:
    return await api_action_routes.handle_action_route(
        request,
        send_action=send_action,
        maybe_record_qwen_probe=_maybe_record_qwen_probe,
    )


async def handle_emergency_stop(request: web.Request) -> web.Response:
    return await api_action_routes.handle_emergency_stop_route(
        request,
        is_agent_connected=is_agent_connected,
        get_ssh_executor=get_ssh_executor,
        send_emergency_stop=send_emergency_stop,
    )


async def handle_resume(request: web.Request) -> web.Response:
    return await api_action_routes.handle_resume_route(
        request,
        is_agent_connected=is_agent_connected,
        get_ssh_executor=get_ssh_executor,
        send_resume=send_resume,
    )


async def handle_get_profile(request: web.Request) -> web.Response:
    return await api_profile_routes.handle_get_profile_route(request)


async def handle_forget_profile(request: web.Request) -> web.Response:
    return await api_profile_routes.handle_forget_profile_route(request)


async def handle_set_memory_policy(request: web.Request) -> web.Response:
    return await api_profile_routes.handle_set_memory_policy_route(request)


async def _ensure_idempotency_schema(db: aiosqlite.Connection) -> None:
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
    db = app.get("idempotency_db")
    if db is not None:
        await db.close()


def create_app(*, idempotency_db: aiosqlite.Connection | None = None) -> web.Application:
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
