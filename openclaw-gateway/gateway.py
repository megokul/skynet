"""
SKYNET Gateway — WebSocket Core

Accepts exactly one CHATHAN worker connection at a time.
Provides an internal API for other components (HTTP API, Telegram) to
enqueue action requests and await responses.

Authentication: The CHATHAN worker must send ``Authorization: Bearer <token>``
in the WebSocket upgrade headers.  Connections without a valid token
are rejected immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import os
import uuid
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection

import config as cfg
from orchestration.openclaw_runner import get_openclaw_runner
from runtime_trace import build_debug_bundle, command_hash, emit_runtime_trace

logger = logging.getLogger("skynet.gateway")

# ---------------------------------------------------------------------------
# Agent connection state
# ---------------------------------------------------------------------------

# The single connected agent (or None).
_agent_ws: ServerConnection | None = None
_agent_lock = asyncio.Lock()

# Maps request_id → Future that resolves with the agent's response.
_pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
_pending_lock = asyncio.Lock()

# Event that signals at least one agent is connected.
agent_connected = asyncio.Event()


def _is_acp_control_plane_mode() -> bool:
    return (
        str(cfg.effective_orchestration_mode() or "legacy").strip().lower() == "acp_first"
        and str(getattr(cfg, "OPENCLAW_AGENT_HOSTING", "ec2_control") or "ec2_control").strip().lower() == "ec2_control"
    )


def _parse_stage_chain(raw: str) -> list[str]:
    stages: list[str] = []
    for token in str(raw or "").split(","):
        stage = token.strip().lower()
        if not stage:
            continue
        if stage == "claude_ollama":
            stage = "claude"
        if stage not in {"codex", "claude", "cline"}:
            continue
        if stage in stages:
            continue
        stages.append(stage)
    return stages or ["codex", "claude", "cline"]


async def _run_local_orchestration_action(
    action: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    runner = get_openclaw_runner()

    if action == "check_coding_agents":
        chain = _parse_stage_chain(getattr(cfg, "OPENCLAW_STAGE_CHAIN", "codex,claude,cline"))
        available, reasons = runner.available_stages(chain)
        binary_by_stage = {
            "codex": str(getattr(cfg, "OPENCLAW_CODEX_BIN", "codex") or "codex"),
            "claude": str(getattr(cfg, "OPENCLAW_CLAUDE_BIN", "claude") or "claude"),
            "cline": str(getattr(cfg, "OPENCLAW_CLINE_BIN", "cline") or "cline"),
        }
        lines: list[str] = []
        for stage in chain:
            binary = binary_by_stage.get(stage, stage)
            if stage in available:
                lines.append(f"{stage}: available ({binary})")
            else:
                lines.append(f"{stage}: unavailable ({reasons.get(stage, f'expected binary: {binary}')})")
        return {"status": "success", "result": {"returncode": 0, "stdout": "\n".join(lines), "stderr": ""}}

    if action == "configure_coding_agent":
        return {
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout": "Control-plane ACP mode does not require runtime provider reconfiguration via gateway.",
                "stderr": "",
            },
        }

    if action == "run_coding_agent":
        agent = str(params.get("agent") or "").strip().lower()
        stage = "claude" if agent == "claude" else agent
        timeout_seconds = int(params.get("timeout_seconds", getattr(cfg, "OPENCLAW_SESSION_TIMEOUT_SECONDS", 1800)) or 1800)
        prompt = str(params.get("prompt") or "")
        model = str(params.get("model") or "").strip()
        backend = str(params.get("backend") or "native").strip().lower()
        if stage not in {"codex", "claude", "cline"}:
            return {
                "status": "error",
                "error": f"Unsupported control-plane stage '{stage}'.",
            }

        session = await runner.start_session(
            phase="gateway_action",
            project_id=str(params.get("project_id") or ""),
            task_id=None,
            stage=stage,
            runtime=str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
            queue_mode=str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue"),
        )
        result = await runner.run_prompt(
            session_id=str(session.get("session_id") or ""),
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            stage=stage,
            model=model,
            backend=backend,
        )
        result = dict(result or {})
        result.setdefault("session_id", str(session.get("session_id") or ""))
        result.setdefault("runtime", str(getattr(cfg, "OPENCLAW_RUNTIME", "acp") or "acp"))
        result.setdefault("queue_mode", str(getattr(cfg, "OPENCLAW_QUEUE_MODE", "require_empty_queue") or "require_empty_queue"))
        return {"status": "success", "result": result}

    return {"status": "error", "error": f"Unsupported local orchestration action '{action}'."}


# ---------------------------------------------------------------------------
# Public interface — used by HTTP API and CLI
# ---------------------------------------------------------------------------

async def send_action(
    action: str,
    params: dict[str, Any] | None = None,
    timeout: int = cfg.ACTION_TIMEOUT_SECONDS,
    confirmed: bool = False,
    task_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """
    Send an action request to the connected agent and wait for the response.

    If *confirmed* is True, the agent skips its local terminal prompt
    (approval was already collected remotely, e.g. via Telegram).

    If ``OPENCLAW_EXECUTION_MODE=ssh`` is set, or no WebSocket agent is
    connected but SSH is configured, the action is routed via the SSH
    tunnel executor to the worker laptop instead.

    Raises ``RuntimeError`` if no agent is connected and SSH is not configured.
    Raises ``asyncio.TimeoutError`` if the agent doesn't reply in time.
    """
    from ssh_tunnel_executor import get_ssh_executor  # lazy — avoids circular import at module load

    _params = dict(params or {})
    _project_id = str(_params.get("project_id") or "")
    _graph_id = str(_params.get("graph_id") or "")
    _node_key = str(_params.get("node_key") or "")
    _node_type = str(_params.get("node_type") or "")
    _stage = str(_params.get("agent") or _params.get("stage") or "").strip().lower()
    _cmd_hash = command_hash(str(_params.get("command") or _params.get("prompt") or ""))
    _working_dir = str(
        _params.get("working_dir")
        or _params.get("project_dir")
        or _params.get("directory")
        or ""
    )
    _runtime_mode = str(cfg.effective_orchestration_mode() or "legacy").strip().lower()
    _transport = "websocket"
    _trace_id = uuid.uuid4().hex
    _span_id = uuid.uuid4().hex[:16]
    _trace_payload = dict(
        event="gateway.action.dispatch",
        status="start",
        trace_id=_trace_id,
        span_id=_span_id,
        parent_span_id="",
        phase="gateway_dispatch",
        stage=_stage,
        project_id=_project_id,
        task_id=str(task_id or ""),
        graph_id=_graph_id,
        node_key=_node_key,
        node_type=_node_type,
        action_name=str(action or "").strip(),
        command_hash=_cmd_hash,
        working_dir=_working_dir,
        runtime_mode=_runtime_mode,
        transport=_transport,
        details={
            "timeout": int(timeout),
            "confirmed": bool(confirmed),
            "idempotency_key": str(idempotency_key or ""),
        },
    )
    emit_runtime_trace(**_trace_payload)

    _ssh_exec = get_ssh_executor()
    _force_ssh = cfg.get_str("OPENCLAW_EXECUTION_MODE", "").lower() in (
        "ssh", "ssh_tunnel", "tunnel", "ssh-only",
    )
    _coding_actions = {"run_coding_agent", "check_coding_agents", "configure_coding_agent"}
    _use_local_orchestration = (
        _is_acp_control_plane_mode()
        and action in _coding_actions
        and not _force_ssh
    )
    if _use_local_orchestration:
        logger.info(
            "Routing action via local OpenClaw orchestration adapter (action=%s mode=%s)",
            action,
            cfg.effective_orchestration_mode(),
        )
        _trace_payload["transport"] = "acp_local"
        try:
            response = await _run_local_orchestration_action(action, _params)
            inner = response.get("result", {}) if isinstance(response, dict) else {}
            is_fail = (
                (not isinstance(response, dict))
                or str(response.get("status") or "").strip().lower() == "error"
                or int(inner.get("returncode", 0) or 0) != 0
            )
            emit_runtime_trace(
                "gateway.action.dispatch",
                status="fail" if is_fail else "ok",
                trace_id=_trace_id,
                parent_span_id=_span_id,
                phase="gateway_dispatch",
                stage=_stage,
                project_id=_project_id,
                task_id=str(task_id or ""),
                graph_id=_graph_id,
                node_key=_node_key,
                node_type=_node_type,
                action_name=str(action or "").strip(),
                command_hash=_cmd_hash,
                working_dir=_working_dir,
                runtime_mode=_runtime_mode,
                transport="acp_local",
                error_code="GATEWAY_ACTION_FAILED" if is_fail else "",
                error_message=str(response.get("error") or inner.get("stderr") or "")[:1200] if isinstance(response, dict) else "invalid action response",
                details={"route": "local_orchestration"},
            )
            return response
        except Exception as exc:
            emit_runtime_trace(
                "gateway.action.dispatch",
                status="fail",
                trace_id=_trace_id,
                parent_span_id=_span_id,
                phase="gateway_dispatch",
                stage=_stage,
                project_id=_project_id,
                task_id=str(task_id or ""),
                graph_id=_graph_id,
                node_key=_node_key,
                node_type=_node_type,
                action_name=str(action or "").strip(),
                command_hash=_cmd_hash,
                working_dir=_working_dir,
                runtime_mode=_runtime_mode,
                transport="acp_local",
                error_type=type(exc).__name__,
                error_code="GATEWAY_LOCAL_ROUTE_ERROR",
                error_message=str(exc)[:1200],
                debug_bundle=build_debug_bundle(
                    failure_class="GATEWAY_LOCAL_ROUTE_ERROR",
                    error_message=str(exc),
                    causal_chain=["gateway.action.dispatch"],
                    mitigation_hint="Validate local ACP runner health and action parameters.",
                ),
                details={"route": "local_orchestration"},
            )
            raise

    _prefer_ssh_for_coding = (
        cfg.CODING_TRANSPORT == "ssh_first"
        and action in _coding_actions
        and _ssh_exec.is_configured()
    )
    _ssh_selected_reason = ""
    if _force_ssh:
        _ssh_selected_reason = "execution_mode_ssh_tunnel"
    elif _prefer_ssh_for_coding:
        _ssh_selected_reason = "coding_transport_ssh_first"
    elif _agent_ws is None:
        _ssh_selected_reason = "no_worker_connected"

    if _ssh_selected_reason:
        _ssh_configured = _ssh_exec.is_configured()
        if _ssh_configured:
            logger.info(
                "Routing action via SSH tunnel executor (action=%s execution_mode=%s ssh_configured=%s ssh_selected_reason=%s)",
                action,
                cfg.get_str("OPENCLAW_EXECUTION_MODE", "").strip().lower(),
                _ssh_configured,
                _ssh_selected_reason,
            )
            _trace_payload["transport"] = "ssh_first"
            try:
                response = await _ssh_exec.execute_action(action, _params, confirmed=confirmed)
                inner = response.get("result", {}) if isinstance(response, dict) else {}
                is_fail = (
                    (not isinstance(response, dict))
                    or str(response.get("status") or "").strip().lower() == "error"
                    or int(inner.get("returncode", 0) or 0) != 0
                )
                emit_runtime_trace(
                    "gateway.action.dispatch",
                    status="fail" if is_fail else "ok",
                    trace_id=_trace_id,
                    parent_span_id=_span_id,
                    phase="gateway_dispatch",
                    stage=_stage,
                    project_id=_project_id,
                    task_id=str(task_id or ""),
                    graph_id=_graph_id,
                    node_key=_node_key,
                    node_type=_node_type,
                    action_name=str(action or "").strip(),
                    command_hash=_cmd_hash,
                    working_dir=_working_dir,
                    runtime_mode=_runtime_mode,
                    transport="ssh_first",
                    error_code="SSH_ACTION_FAILED" if is_fail else "",
                    error_message=str(response.get("error") or inner.get("stderr") or "")[:1200] if isinstance(response, dict) else "invalid action response",
                    details={"route_reason": _ssh_selected_reason},
                )
                return response
            except Exception as exc:
                emit_runtime_trace(
                    "gateway.action.dispatch",
                    status="fail",
                    trace_id=_trace_id,
                    parent_span_id=_span_id,
                    phase="gateway_dispatch",
                    stage=_stage,
                    project_id=_project_id,
                    task_id=str(task_id or ""),
                    graph_id=_graph_id,
                    node_key=_node_key,
                    node_type=_node_type,
                    action_name=str(action or "").strip(),
                    command_hash=_cmd_hash,
                    working_dir=_working_dir,
                    runtime_mode=_runtime_mode,
                    transport="ssh_first",
                    error_type=type(exc).__name__,
                    error_code="SSH_ACTION_ROUTE_ERROR",
                    error_message=str(exc)[:1200],
                    debug_bundle=build_debug_bundle(
                        failure_class="SSH_ACTION_ROUTE_ERROR",
                        error_message=str(exc),
                        causal_chain=["gateway.action.dispatch"],
                        mitigation_hint="Inspect SSH transport diagnostics and executor trace events.",
                    ),
                    details={"route_reason": _ssh_selected_reason},
                )
                raise
        if _force_ssh:
            emit_runtime_trace(
                "gateway.action.dispatch",
                status="fail",
                trace_id=_trace_id,
                parent_span_id=_span_id,
                phase="gateway_dispatch",
                stage=_stage,
                project_id=_project_id,
                task_id=str(task_id or ""),
                graph_id=_graph_id,
                node_key=_node_key,
                node_type=_node_type,
                action_name=str(action or "").strip(),
                command_hash=_cmd_hash,
                working_dir=_working_dir,
                runtime_mode=_runtime_mode,
                transport="ssh_first",
                error_code="SSH_NOT_CONFIGURED",
                error_message="OPENCLAW_EXECUTION_MODE forces SSH, but SSH tunnel executor is not configured.",
            )
            raise RuntimeError("OPENCLAW_EXECUTION_MODE forces SSH, but SSH tunnel executor is not configured.")
        if _agent_ws is None:
            emit_runtime_trace(
                "gateway.action.dispatch",
                status="fail",
                trace_id=_trace_id,
                parent_span_id=_span_id,
                phase="gateway_dispatch",
                stage=_stage,
                project_id=_project_id,
                task_id=str(task_id or ""),
                graph_id=_graph_id,
                node_key=_node_key,
                node_type=_node_type,
                action_name=str(action or "").strip(),
                command_hash=_cmd_hash,
                working_dir=_working_dir,
                runtime_mode=_runtime_mode,
                transport="websocket",
                error_code="NO_AGENT_CONNECTED",
                error_message="No agent connected.",
            )
            raise RuntimeError("No agent connected.")

    request_id = str(uuid.uuid4())
    message = {
        "type": "action_request",
        "request_id": request_id,
        "action": action,
        "params": params or {},
        "confirmed": confirmed,
    }
    if task_id:
        message["task_id"] = task_id
    if idempotency_key:
        message["idempotency_key"] = idempotency_key

    # Create a future for the response.
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()

    async with _pending_lock:
        _pending[request_id] = future

    try:
        await _agent_ws.send(json.dumps(message))
        logger.info("Sent action '%s' (req=%s) to agent.", action, request_id)

        result = await asyncio.wait_for(future, timeout=timeout)
        inner = result.get("result", {}) if isinstance(result, dict) else {}
        is_fail = (
            (not isinstance(result, dict))
            or str(result.get("status") or "").strip().lower() == "error"
            or int(inner.get("returncode", 0) or 0) != 0
        )
        emit_runtime_trace(
            "gateway.action.dispatch",
            status="fail" if is_fail else "ok",
            trace_id=_trace_id,
            parent_span_id=_span_id,
            phase="gateway_dispatch",
            stage=_stage,
            project_id=_project_id,
            task_id=str(task_id or ""),
            graph_id=_graph_id,
            node_key=_node_key,
            node_type=_node_type,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="websocket",
            error_code="AGENT_ACTION_FAILED" if is_fail else "",
            error_message=str(result.get("error") or inner.get("stderr") or "")[:1200] if isinstance(result, dict) else "invalid action response",
            details={"request_id": request_id},
        )
        return result

    except asyncio.TimeoutError:
        logger.warning("Timed out waiting for response to req=%s", request_id)
        emit_runtime_trace(
            "gateway.action.dispatch",
            status="fail",
            trace_id=_trace_id,
            parent_span_id=_span_id,
            phase="gateway_dispatch",
            stage=_stage,
            project_id=_project_id,
            task_id=str(task_id or ""),
            graph_id=_graph_id,
            node_key=_node_key,
            node_type=_node_type,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="websocket",
            error_code="ACTION_TIMEOUT",
            error_type="TimeoutError",
            error_message=f"Timed out waiting for action response (request_id={request_id}).",
            details={"request_id": request_id, "timeout": int(timeout)},
            debug_bundle=build_debug_bundle(
                failure_class="ACTION_TIMEOUT",
                error_message=f"Timed out waiting for action response (request_id={request_id}).",
                causal_chain=["gateway.action.dispatch"],
                mitigation_hint="Check worker connectivity and long-running command progress.",
            ),
        )
        raise
    except Exception as exc:
        emit_runtime_trace(
            "gateway.action.dispatch",
            status="fail",
            trace_id=_trace_id,
            parent_span_id=_span_id,
            phase="gateway_dispatch",
            stage=_stage,
            project_id=_project_id,
            task_id=str(task_id or ""),
            graph_id=_graph_id,
            node_key=_node_key,
            node_type=_node_type,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="websocket",
            error_code="GATEWAY_SEND_ACTION_ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc)[:1200],
            details={"request_id": request_id},
            debug_bundle=build_debug_bundle(
                failure_class="GATEWAY_SEND_ACTION_ERROR",
                error_message=str(exc),
                causal_chain=["gateway.action.dispatch"],
                mitigation_hint="Check gateway websocket and pending request lifecycle.",
            ),
        )
        raise

    finally:
        async with _pending_lock:
            _pending.pop(request_id, None)


async def send_emergency_stop() -> None:
    """Send the emergency stop control message to the agent."""
    if _agent_ws is None:
        raise RuntimeError("No agent connected.")
    await _agent_ws.send(json.dumps({"type": "emergency_stop"}))
    logger.critical("Emergency stop sent to agent.")


async def send_resume() -> None:
    """Send the resume control message to the agent."""
    if _agent_ws is None:
        raise RuntimeError("No agent connected.")
    await _agent_ws.send(json.dumps({"type": "resume"}))
    logger.info("Resume sent to agent.")


def is_agent_connected() -> bool:
    """
    Is agent connected.
    
    Purpose:
    - Implement `is_agent_connected` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `bool` when available; otherwise side effects only.
    """

    return _agent_ws is not None


def is_worker_available() -> bool:
    """Return True if work can be dispatched — via WebSocket agent OR SSH tunnel."""
    from ssh_tunnel_executor import get_ssh_executor  # lazy import
    return _agent_ws is not None or get_ssh_executor().is_configured()


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

async def _handler(ws: ServerConnection) -> None:
    """Handle one agent WebSocket connection."""
    global _agent_ws

    # ---- Authenticate ----
    # websockets >= 14 exposes request headers via ws.request
    token = _extract_token(ws)
    if token != cfg.AUTH_TOKEN:
        logger.warning("Rejected connection: invalid token.")
        await ws.close(4001, "Unauthorized")
        return

    # ---- Accept (single agent at a time) ----
    async with _agent_lock:
        if _agent_ws is not None:
            logger.warning("Rejected connection: another agent already connected.")
            await ws.close(4002, "Another agent is already connected")
            return
        _agent_ws = ws
        agent_connected.set()

    remote = ws.remote_address
    logger.info("Agent connected from %s", remote)

    try:
        async for raw in ws:
            await _on_message(raw)
    except websockets.exceptions.ConnectionClosed as exc:
        logger.info("Agent disconnected (%s).", exc)
    finally:
        async with _agent_lock:
            _agent_ws = None
            agent_connected.clear()
        # Cancel any pending futures so callers don't hang.
        async with _pending_lock:
            for rid, fut in _pending.items():
                if not fut.done():
                    fut.set_exception(RuntimeError("Agent disconnected."))
            _pending.clear()
        logger.info("Agent connection cleaned up.")


def _extract_token(ws: ServerConnection) -> str:
    """Pull the Bearer token from the upgrade request headers."""
    try:
        auth = ws.request.headers.get("Authorization", "")
    except AttributeError:
        return ""
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


async def _on_message(raw: str | bytes) -> None:
    """Route an inbound message from the agent."""
    try:
        msg: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Non-JSON frame from agent — ignoring.")
        return

    msg_type = msg.get("type", "")

    if msg_type == "agent_hello":
        caps = msg.get("capabilities", [])
        logger.info("Agent hello received. Capabilities: %s", caps)
        return

    if msg_type == "action_response":
        request_id = msg.get("request_id", "")
        async with _pending_lock:
            future = _pending.get(request_id)
        if future and not future.done():
            future.set_result(msg)
        else:
            logger.warning("Response for unknown/expired request_id=%s", request_id)
        return

    if msg_type in ("emergency_stop_ack", "resume_ack"):
        logger.info("Agent acknowledged: %s", msg_type)
        return

    if msg_type == "pong":
        return

    logger.debug("Unhandled agent message type: %s", msg_type)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _build_ssl_context() -> ssl.SSLContext | None:
    """Load TLS cert/key if they exist, otherwise run without TLS."""
    if os.path.isfile(cfg.TLS_CERT) and os.path.isfile(cfg.TLS_KEY):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cfg.TLS_CERT, cfg.TLS_KEY)
        logger.info("TLS enabled (cert=%s).", cfg.TLS_CERT)
        return ctx
    logger.info(
        "TLS cert/key not found (%s, %s). Running WITHOUT TLS — "
        "use setup_tls.sh to generate certificates.",
        cfg.TLS_CERT,
        cfg.TLS_KEY,
    )
    return None


async def start_ws_server() -> websockets.asyncio.server.Server:
    """Start the WebSocket server and return the server object."""
    ssl_ctx = _build_ssl_context()

    server = await websockets.serve(
        _handler,
        cfg.WS_HOST,
        cfg.WS_PORT,
        ssl=ssl_ctx,
        ping_interval=cfg.WS_PING_INTERVAL,
        ping_timeout=cfg.WS_PING_TIMEOUT,
        max_size=2**20,
    )

    proto = "wss" if ssl_ctx else "ws"
    logger.info("WebSocket server listening on %s://0.0.0.0:%d", proto, cfg.WS_PORT)
    return server
