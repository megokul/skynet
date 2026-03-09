"""
CHATHAN Worker - WebSocket Connection Layer

Maintains a persistent, outbound-only WebSocket connection to the
SKYNET Gateway.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import shutil
import socket
import ssl
import time
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

import agent_config as config
from router.action_router import route
from settings.loader import get_str as _cfg_s

logger = logging.getLogger("chathan.connection")
_SESSION_STARTED_AT = time.monotonic()
_INFLIGHT_ACTIONS = 0
_INFLIGHT_LOCK = asyncio.Lock()
_LOG_STREAM_PATHS: dict[str, str] = {}


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _detect_coding_agents() -> dict[str, str]:
    probes = {
        "codex": _cfg_s("SKYNET_CODEX_BIN") or _cfg_s("OPENCLAW_CODEX_BIN", "codex"),
        "claude": _cfg_s("SKYNET_CLAUDE_BIN") or _cfg_s("OPENCLAW_CLAUDE_BIN", "claude"),
        "cline": _cfg_s("SKYNET_CLINE_BIN") or _cfg_s("OPENCLAW_CLINE_BIN", "cline"),
    }
    detected: dict[str, str] = {}
    for name, binary in probes.items():
        resolved = binary if os.path.isabs(binary) and os.path.exists(binary) else shutil.which(binary)
        if resolved:
            detected[name] = resolved
    return detected


def _hello_payload() -> dict[str, Any]:
    return {
        "type": "agent_hello",
        "worker_id": config.WORKER_ID,
        "agent_version": "1.1.0",
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "capabilities": sorted(config.AUTO_ACTIONS | config.CONFIRM_ACTIONS),
        "allowed_roots": list(config.ALLOWED_ROOTS),
        "coding_agents": _detect_coding_agents(),
        "log_mirror_dir": config.AGENT_LOG_MIRROR_DIR,
    }


async def run_agent() -> None:
    delay = config.RECONNECT_DELAY_SECONDS

    while True:
        try:
            await _connect_and_listen()
            delay = config.RECONNECT_DELAY_SECONDS
        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.InvalidStatusCode,
            OSError,
        ) as exc:
            logger.warning("Connection lost (%s). Reconnecting in %ds...", exc, delay)
        except Exception:
            logger.exception("Unexpected error in connection loop. Reconnecting in %ds...", delay)

        await asyncio.sleep(delay)
        delay = min(delay * 2, config.MAX_RECONNECT_DELAY_SECONDS)


async def _connect_and_listen() -> None:
    if not config.AUTH_TOKEN:
        raise RuntimeError(
            "SKYNET_AUTH_TOKEN is not set. Export it as an environment variable before starting the agent."
        )

    headers = {"Authorization": f"Bearer {config.AUTH_TOKEN}"}
    logger.info("Connecting to %s ...", config.GATEWAY_URL)

    ssl_ctx = None
    if config.GATEWAY_URL.lower().startswith("wss://"):
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(
        config.GATEWAY_URL,
        additional_headers=headers,
        ssl=ssl_ctx,
        ping_interval=config.WS_PING_INTERVAL_SECONDS,
        ping_timeout=config.WS_PING_TIMEOUT_SECONDS,
        max_size=2**20,
    ) as ws:
        logger.info("Connected to gateway.")
        send_lock = asyncio.Lock()
        active_tasks: set[asyncio.Task[Any]] = set()
        await _send_hello(ws, send_lock)
        heartbeat_task = asyncio.create_task(_heartbeat_loop(ws, send_lock))
        try:
            async for raw in ws:
                task = asyncio.create_task(_handle_message(ws, raw, send_lock))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat_task
            for task in list(active_tasks):
                task.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)


async def _send_json(
    ws: ClientConnection,
    send_lock: asyncio.Lock | None,
    payload: dict[str, Any],
) -> None:
    if send_lock is None:
        await ws.send(json.dumps(payload, default=str))
        return
    async with send_lock:
        await ws.send(json.dumps(payload, default=str))


async def _send_hello(ws: ClientConnection, send_lock: asyncio.Lock | None = None) -> None:
    hello = _hello_payload()
    await _send_json(ws, send_lock, hello)
    logger.debug("Sent hello: %s", hello)


async def _heartbeat_loop(ws: ClientConnection, send_lock: asyncio.Lock) -> None:
    interval = max(5, int(getattr(config, "AGENT_HEARTBEAT_SECONDS", 15) or 15))
    while True:
        await asyncio.sleep(interval)
        try:
            async with _INFLIGHT_LOCK:
                queue_depth = int(_INFLIGHT_ACTIONS)
            payload = {
                "type": "agent_heartbeat",
                "worker_id": config.WORKER_ID,
                "ts": _utc_now_iso(),
                "uptime_seconds": int(time.monotonic() - _SESSION_STARTED_AT),
                "coding_agents": _detect_coding_agents(),
                "queue_depth": queue_depth,
            }
            await _send_json(ws, send_lock, payload)
        except Exception:
            logger.debug("Heartbeat send failed; connection loop will handle reconnect.", exc_info=True)
            raise


async def _handle_message(
    ws: ClientConnection,
    raw: str | bytes,
    send_lock: asyncio.Lock | None = None,
) -> None:
    try:
        message: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Received non-JSON frame - ignoring.")
        return

    msg_type = message.get("type", "action_request")

    if msg_type == "emergency_stop":
        logger.critical("EMERGENCY STOP received from gateway.")
        config.EMERGENCY_STOP = True
        await _send_json(ws, send_lock, {
            "type": "emergency_stop_ack",
            "status": "stopped",
            "worker_id": config.WORKER_ID,
        })
        return

    if msg_type == "resume":
        logger.info("RESUME received from gateway.")
        config.EMERGENCY_STOP = False
        await _send_json(ws, send_lock, {
            "type": "resume_ack",
            "status": "resumed",
            "worker_id": config.WORKER_ID,
        })
        return

    if msg_type == "ping":
        await _send_json(ws, send_lock, {"type": "pong", "worker_id": config.WORKER_ID})
        return

    if msg_type == "log_write":
        line_count = _write_log_lines(
            message.get("stream", "runtime"),
            message.get("lines", []),
        )
        await _send_json(ws, send_lock, {
            "type": "log_write_ack",
            "log_id": str(message.get("log_id") or ""),
            "stream": str(message.get("stream") or "runtime"),
            "line_count": int(line_count),
            "ts": _utc_now_iso(),
            "worker_id": config.WORKER_ID,
        })
        return

    if msg_type in ("action_request", "action"):
        await _process_action_request(ws, message, send_lock)
        return

    logger.warning("Unknown message type '%s' - ignoring.", msg_type)


async def _process_action_request(
    ws: ClientConnection,
    message: dict[str, Any],
    send_lock: asyncio.Lock | None,
) -> None:
    global _INFLIGHT_ACTIONS

    if message.get("expect_accept"):
        await _send_json(
            ws,
            send_lock,
            {
                "type": "action_accepted",
                "request_id": str(message.get("request_id") or ""),
                "transport_id": str(message.get("transport_id") or ""),
                "worker_id": config.WORKER_ID,
                "accepted_at": _utc_now_iso(),
            },
        )
    async with _INFLIGHT_LOCK:
        _INFLIGHT_ACTIONS += 1
    try:
        response = await route(message)
    finally:
        async with _INFLIGHT_LOCK:
            _INFLIGHT_ACTIONS = max(0, _INFLIGHT_ACTIONS - 1)
    response["type"] = "action_response"
    response["transport_id"] = str(message.get("transport_id") or response.get("transport_id") or "")
    response["worker_id"] = config.WORKER_ID
    await _send_json(ws, send_lock, response)


def _write_log_lines(stream: str, lines: list[str]) -> int:
    if not lines:
        return 0
    base_dir = config.AGENT_LOG_MIRROR_DIR or os.path.join(os.path.expanduser("~"), "skynet-logs")
    os.makedirs(base_dir, exist_ok=True)
    filename = {
        "runtime": "skynet-runtime.log",
        "control-flow": "skynet-control-flow.log",
        "trace": "skynet.trace.log",
    }.get(stream, f"skynet-{stream}.log")
    path = _LOG_STREAM_PATHS.get(stream)
    if not path:
        path = os.path.join(base_dir, filename)
        _LOG_STREAM_PATHS[stream] = path
    count = 0
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line)
                if not str(line).endswith("\n"):
                    fh.write("\n")
                count += 1
    except Exception:
        logger.debug("Failed to mirror websocket log lines", exc_info=True)
        return 0
    return count


