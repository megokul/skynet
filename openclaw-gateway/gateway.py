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
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection

import gateway_config as cfg
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
_pending: dict[str, dict[str, Any]] = {}
_pending_lock = asyncio.Lock()

# Event that signals at least one agent is connected.
agent_connected = asyncio.Event()

_pending_log_acks: dict[str, asyncio.Future[dict[str, Any]]] = {}
_pending_log_acks_lock = asyncio.Lock()

_SAFE_FALLBACK_ACTIONS = {
    "file_read",
    "list_directory",
    "git_status",
    "web_search",
    "check_coding_agents",
}
_MUTATING_ACTIONS = {
    "run_coding_agent",
    "exec_command",
    "file_write",
    "create_directory",
    "install_dependencies",
    "run_tests",
    "lint_project",
    "build_project",
    "git_init",
    "git_add_all",
    "git_commit",
    "git_push",
    "gh_create_repo",
    "docker_build",
    "docker_compose_up",
    "close_app",
}

_agent_state: dict[str, Any] = {
    "worker_id": "",
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
_ws_failure_state: dict[str, Any] = {
    "error_category": "",
    "failure_streak": 0,
    "last_error": "",
    "last_error_at": "",
    "last_fallback_reason": "",
}
_log_mirror_state: dict[str, Any] = {
    "enabled": False,
    "loop_bound": False,
    "last_send_at": "",
    "last_ack_at": "",
    "last_error": "",
    "ack_required": False,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_ws_error(category: str, message: str) -> None:
    _ws_failure_state["error_category"] = str(category or "").strip()
    _ws_failure_state["last_error"] = str(message or "").strip()
    _ws_failure_state["last_error_at"] = _utc_now_iso()
    _ws_failure_state["failure_streak"] = int(_ws_failure_state.get("failure_streak", 0) or 0) + 1


def _clear_ws_error() -> None:
    _ws_failure_state["error_category"] = ""
    _ws_failure_state["last_error"] = ""
    _ws_failure_state["last_error_at"] = ""
    _ws_failure_state["failure_streak"] = 0


def _set_fallback_reason(reason: str) -> None:
    _ws_failure_state["last_fallback_reason"] = str(reason or "").strip()


def _heartbeat_monotonic() -> float:
    return float(
        _agent_state.get("last_heartbeat_monotonic")
        or _agent_state.get("last_hello_monotonic")
        or 0.0
    )


def websocket_heartbeat_fresh() -> bool:
    if _agent_ws is None:
        return False
    last_signal = _heartbeat_monotonic()
    if last_signal <= 0:
        return False
    stale_after = max(15, int(getattr(cfg, "WEBSOCKET_HEARTBEAT_STALE_SECONDS", 45) or 45))
    return (time.monotonic() - last_signal) <= stale_after


def websocket_primary_available() -> bool:
    if not bool(getattr(cfg, "WEBSOCKET_PRIMARY_ENABLED", True)):
        return False
    mode = str(cfg.get_str("OPENCLAW_EXECUTION_MODE", "") or "").strip().lower()
    if mode in {"ssh", "ssh_tunnel", "tunnel", "ssh-only"}:
        return False
    return _agent_ws is not None and websocket_heartbeat_fresh()


def get_agent_status() -> dict[str, Any]:
    return {
        "agent_connected": _agent_ws is not None,
        "worker_id": str(_agent_state.get("worker_id") or ""),
        "agent_last_hello_at": str(_agent_state.get("last_hello_at") or ""),
        "agent_last_heartbeat_at": str(_agent_state.get("last_heartbeat_at") or ""),
        "websocket_health_ok": websocket_primary_available(),
        "websocket_error_category": str(_ws_failure_state.get("error_category") or ""),
        "websocket_failure_streak": int(_ws_failure_state.get("failure_streak", 0) or 0),
        "fallback_last_reason": str(_ws_failure_state.get("last_fallback_reason") or ""),
        "websocket_log_mirror_enabled": bool(_log_mirror_state.get("enabled", False)),
        "websocket_log_mirror_loop_bound": bool(_log_mirror_state.get("loop_bound", False)),
        "websocket_log_mirror_last_send_at": str(_log_mirror_state.get("last_send_at") or ""),
        "websocket_log_mirror_last_ack_at": str(_log_mirror_state.get("last_ack_at") or ""),
        "websocket_log_mirror_last_error": str(_log_mirror_state.get("last_error") or ""),
        "worker_capabilities": list(_agent_state.get("capabilities") or []),
        "coding_agents": dict(_agent_state.get("coding_agents") or {}),
    }


def set_websocket_log_mirror_state(
    *,
    enabled: bool | None = None,
    loop_bound: bool | None = None,
    last_error: str | None = None,
    ack_required: bool | None = None,
) -> None:
    if enabled is not None:
        _log_mirror_state["enabled"] = bool(enabled)
    if loop_bound is not None:
        _log_mirror_state["loop_bound"] = bool(loop_bound)
    if last_error is not None:
        _log_mirror_state["last_error"] = str(last_error or "").strip()
    if ack_required is not None:
        _log_mirror_state["ack_required"] = bool(ack_required)


def _update_agent_state(message: dict[str, Any], *, heartbeat: bool) -> None:
    now_iso = _utc_now_iso()
    now_mono = time.monotonic()
    _agent_state["worker_id"] = str(message.get("worker_id") or _agent_state.get("worker_id") or "").strip()
    _agent_state["agent_version"] = str(message.get("agent_version") or _agent_state.get("agent_version") or "").strip()
    _agent_state["hostname"] = str(message.get("hostname") or _agent_state.get("hostname") or "").strip()
    _agent_state["os"] = str(message.get("os") or _agent_state.get("os") or "").strip()
    if "capabilities" in message:
        _agent_state["capabilities"] = list(message.get("capabilities") or [])
    if "allowed_roots" in message:
        _agent_state["allowed_roots"] = list(message.get("allowed_roots") or [])
    if "coding_agents" in message:
        coding_agents = message.get("coding_agents") or {}
        _agent_state["coding_agents"] = dict(coding_agents) if isinstance(coding_agents, dict) else {}
    if "log_mirror_dir" in message:
        _agent_state["log_mirror_dir"] = str(message.get("log_mirror_dir") or "").strip()
    if heartbeat:
        _agent_state["last_heartbeat_at"] = now_iso
        _agent_state["last_heartbeat_monotonic"] = now_mono
        _agent_state["queue_depth"] = int(message.get("queue_depth", _agent_state.get("queue_depth", 0)) or 0)
    else:
        _agent_state["last_hello_at"] = now_iso
        _agent_state["last_hello_monotonic"] = now_mono
        _agent_state["last_heartbeat_at"] = now_iso
        _agent_state["last_heartbeat_monotonic"] = now_mono


def _transport_primary_mode(ssh_configured: bool) -> str:
    if websocket_primary_available():
        return "websocket_primary"
    if ssh_configured:
        return "ssh_fallback"
    return "unavailable"


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


def _action_is_mutating(action: str) -> bool:
    name = str(action or "").strip()
    return name in _MUTATING_ACTIONS


async def _dispatch_via_ssh(
    *,
    ssh_exec,
    action: str,
    params: dict[str, Any],
    confirmed: bool,
    trace_id: str,
    parent_span_id: str,
    stage: str,
    project_id: str,
    task_id: str,
    graph_id: str,
    node_key: str,
    node_type: str,
    command_hash_value: str,
    working_dir: str,
    runtime_mode: str,
    route_reason: str,
) -> dict[str, Any]:
    response = await ssh_exec.execute_action(action, params, confirmed=confirmed)
    inner = response.get("result", {}) if isinstance(response, dict) else {}
    is_fail = (
        (not isinstance(response, dict))
        or str(response.get("status") or "").strip().lower() == "error"
        or int(inner.get("returncode", 0) or 0) != 0
    )
    emit_runtime_trace(
        "gateway.action.dispatch",
        status="fail" if is_fail else "ok",
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        phase="gateway_dispatch",
        stage=stage,
        project_id=project_id,
        task_id=task_id,
        graph_id=graph_id,
        node_key=node_key,
        node_type=node_type,
        action_name=str(action or "").strip(),
        command_hash=command_hash_value,
        working_dir=working_dir,
        runtime_mode=runtime_mode,
        transport="ssh_fallback",
        error_code="SSH_ACTION_FAILED" if is_fail else "",
        error_message=str(response.get("error") or inner.get("stderr") or "")[:1200] if isinstance(response, dict) else "invalid action response",
        details={"route_reason": route_reason},
    )
    return response


async def _wait_for_log_ack(log_id: str, timeout_seconds: int) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    async with _pending_log_acks_lock:
        _pending_log_acks[log_id] = future
    try:
        return await asyncio.wait_for(future, timeout=max(1, timeout_seconds))
    finally:
        async with _pending_log_acks_lock:
            _pending_log_acks.pop(log_id, None)


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
    Send an action request to the worker transport and wait for the response.

    Transport precedence:
    1. Local ACP orchestration when explicitly enabled for control-plane-only actions.
    2. WebSocket worker transport when healthy and not SSH-forced.
    3. SSH fallback when websocket is unavailable or pre-accept send fails.
    """
    from ssh_tunnel_executor import get_ssh_executor  # lazy import

    _params = dict(params or {})
    _project_id = str(_params.get("project_id") or "")
    _graph_id = str(_params.get("graph_id") or "")
    _node_key = str(_params.get("node_key") or "")
    _node_type = str(_params.get("node_type") or "")
    _stage = str(_params.get("agent") or _params.get("stage") or "").strip().lower()
    _session_key = str(_params.get("session_key") or "").strip()
    _cmd_hash = command_hash(str(_params.get("command") or _params.get("prompt") or ""))
    _working_dir = str(
        _params.get("working_dir")
        or _params.get("project_dir")
        or _params.get("directory")
        or ""
    )
    _runtime_mode = str(cfg.effective_orchestration_mode() or "legacy").strip().lower()
    _trace_id = uuid.uuid4().hex
    _span_id = uuid.uuid4().hex[:16]
    _transport_id = str(_params.get("transport_id") or uuid.uuid4().hex)
    _idempotency_key = str(idempotency_key or _params.get("idempotency_key") or uuid.uuid4().hex)
    _params["transport_id"] = _transport_id
    _params["idempotency_key"] = _idempotency_key
    if task_id and not _params.get("task_id"):
        _params["task_id"] = str(task_id)

    emit_runtime_trace(
        "gateway.action.dispatch",
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
        session_key=_session_key,
        action_name=str(action or "").strip(),
        command_hash=_cmd_hash,
        working_dir=_working_dir,
        runtime_mode=_runtime_mode,
        transport="pending",
        details={
            "timeout": int(timeout),
            "confirmed": bool(confirmed),
            "idempotency_key": _idempotency_key,
            "transport_id": _transport_id,
        },
    )

    _ssh_exec = get_ssh_executor()
    _ssh_configured = _ssh_exec.is_configured()
    _force_ssh = cfg.get_str("OPENCLAW_EXECUTION_MODE", "").lower() in (
        "ssh", "ssh_tunnel", "tunnel", "ssh-only",
    )
    _fallback_enabled = bool(getattr(cfg, "WEBSOCKET_FALLBACK_TO_SSH", True))
    _accept_timeout = max(1, int(getattr(cfg, "WEBSOCKET_ACCEPT_TIMEOUT_SECONDS", 3) or 3))
    _replay_timeout = max(1, int(getattr(cfg, "WEBSOCKET_REPLAY_TIMEOUT_SECONDS", 30) or 30))
    _coding_actions = {"run_coding_agent", "check_coding_agents", "configure_coding_agent"}
    _use_local_orchestration = (
        _is_acp_control_plane_mode()
        and action in _coding_actions
        and not _force_ssh
    )

    def _emit_transport_select(transport: str, reason: str, *, route_mode: str = "") -> None:
        emit_runtime_trace(
            "gateway.transport.select",
            status="ok",
            trace_id=_trace_id,
            parent_span_id=_span_id,
            phase="gateway_dispatch",
            stage=_stage,
            project_id=_project_id,
            task_id=str(task_id or ""),
            graph_id=_graph_id,
            node_key=_node_key,
            node_type=_node_type,
            session_key=_session_key,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport=transport,
            details={
                "reason": reason,
                "route_mode": route_mode,
                "ssh_configured": _ssh_configured,
                "worker_id": str(_agent_state.get("worker_id") or ""),
            },
        )

    async def _ssh_fallback(route_reason: str) -> dict[str, Any]:
        _set_fallback_reason(route_reason)
        emit_runtime_trace(
            "gateway.transport.fallback",
            status="ok",
            trace_id=_trace_id,
            parent_span_id=_span_id,
            phase="gateway_dispatch",
            stage=_stage,
            project_id=_project_id,
            task_id=str(task_id or ""),
            graph_id=_graph_id,
            node_key=_node_key,
            node_type=_node_type,
            session_key=_session_key,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="ssh_fallback",
            details={"reason": route_reason},
        )
        _emit_transport_select("ssh_fallback", route_reason, route_mode="ssh")
        return await _dispatch_via_ssh(
            ssh_exec=_ssh_exec,
            action=action,
            params=_params,
            confirmed=confirmed,
            trace_id=_trace_id,
            parent_span_id=_span_id,
            stage=_stage,
            project_id=_project_id,
            task_id=str(task_id or ""),
            graph_id=_graph_id,
            node_key=_node_key,
            node_type=_node_type,
            command_hash_value=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            route_reason=route_reason,
        )

    def _build_message(request_id_value: str) -> dict[str, Any]:
        message = {
            "type": "action_request",
            "request_id": request_id_value,
            "action": action,
            "params": dict(_params),
            "confirmed": confirmed,
            "transport_id": _transport_id,
            "idempotency_key": _idempotency_key,
            "expect_accept": True,
            "worker_id": str(_agent_state.get("worker_id") or ""),
        }
        if task_id:
            message["task_id"] = str(task_id)
        return message

    if _use_local_orchestration:
        _emit_transport_select("acp_local", "acp_control_plane_mode", route_mode="acp")
        logger.info(
            "Routing action via local OpenClaw orchestration adapter (action=%s mode=%s)",
            action,
            cfg.effective_orchestration_mode(),
        )
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
                session_key=_session_key,
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
                session_key=_session_key,
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

    if _force_ssh:
        if not _ssh_configured:
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
                session_key=_session_key,
                action_name=str(action or "").strip(),
                command_hash=_cmd_hash,
                working_dir=_working_dir,
                runtime_mode=_runtime_mode,
                transport="ssh_fallback",
                error_code="SSH_NOT_CONFIGURED",
                error_message="OPENCLAW_EXECUTION_MODE forces SSH, but SSH tunnel executor is not configured.",
            )
            raise RuntimeError("OPENCLAW_EXECUTION_MODE forces SSH, but SSH tunnel executor is not configured.")
        _emit_transport_select("ssh_fallback", "execution_mode_forced_ssh", route_mode="ssh")
        return await _ssh_fallback("execution_mode_forced_ssh")

    if not websocket_primary_available():
        if _ssh_configured and _fallback_enabled:
            if _agent_ws is None:
                return await _ssh_fallback("no_worker_connected")
            if not websocket_heartbeat_fresh():
                return await _ssh_fallback("heartbeat_stale")
            return await _ssh_fallback("websocket_unavailable")
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
            session_key=_session_key,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="websocket_primary",
            error_code="NO_AGENT_CONNECTED" if _agent_ws is None else "WEBSOCKET_UNAVAILABLE",
            error_message="No worker agent connected and SSH fallback is unavailable."
            if _agent_ws is None
            else "Worker websocket is unavailable and SSH fallback is disabled or not configured.",
        )
        raise RuntimeError(
            "No worker agent connected and SSH fallback is unavailable."
            if _agent_ws is None
            else "Worker websocket is unavailable and SSH fallback is disabled or not configured."
        )

    _emit_transport_select("websocket_primary", "agent_connected", route_mode="websocket")
    request_id = str(uuid.uuid4())
    message = _build_message(request_id)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    accepted_event = asyncio.Event()
    pending_entry = {
        "future": future,
        "accepted": False,
        "accepted_event": accepted_event,
        "transport_id": _transport_id,
        "idempotency_key": _idempotency_key,
        "action": str(action or "").strip(),
        "message": dict(message),
        "session_key": _session_key,
        "worker_id": str(_agent_state.get("worker_id") or ""),
    }

    async with _pending_lock:
        _pending[request_id] = pending_entry

    try:
        if _agent_ws is None:
            raise RuntimeError("Websocket primary selected but no worker connection is active.")
        try:
            await _agent_ws.send(json.dumps(message))
        except Exception:
            if _ssh_configured and _fallback_enabled:
                return await _ssh_fallback("websocket_send_failed")
            raise
        _clear_ws_error()
        logger.info("Sent websocket action '%s' (req=%s transport_id=%s).", action, request_id, _transport_id)

        accepted = False
        try:
            await asyncio.wait_for(accepted_event.wait(), timeout=_accept_timeout)
            accepted = True
        except asyncio.TimeoutError:
            accepted = False

        if accepted:
            emit_runtime_trace(
                "gateway.websocket.action.accepted",
                status="ok",
                trace_id=_trace_id,
                parent_span_id=_span_id,
                phase="gateway_dispatch",
                stage=_stage,
                project_id=_project_id,
                task_id=str(task_id or ""),
                graph_id=_graph_id,
                node_key=_node_key,
                node_type=_node_type,
                session_key=_session_key,
                action_name=str(action or "").strip(),
                command_hash=_cmd_hash,
                working_dir=_working_dir,
                runtime_mode=_runtime_mode,
                transport="websocket_primary",
                details={"request_id": request_id, "transport_id": _transport_id},
            )

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except Exception as exc:
            accepted = bool(pending_entry.get("accepted")) or accepted_event.is_set()
            if accepted and _action_is_mutating(action):
                emit_runtime_trace(
                    "gateway.websocket.action.replay",
                    status="start",
                    trace_id=_trace_id,
                    parent_span_id=_span_id,
                    phase="gateway_dispatch",
                    stage=_stage,
                    project_id=_project_id,
                    task_id=str(task_id or ""),
                    graph_id=_graph_id,
                    node_key=_node_key,
                    node_type=_node_type,
                    session_key=_session_key,
                    action_name=str(action or "").strip(),
                    command_hash=_cmd_hash,
                    working_dir=_working_dir,
                    runtime_mode=_runtime_mode,
                    transport="websocket_primary",
                    details={
                        "request_id": request_id,
                        "transport_id": _transport_id,
                        "reason": type(exc).__name__,
                    },
                )
                try:
                    await asyncio.wait_for(agent_connected.wait(), timeout=_replay_timeout)
                except asyncio.TimeoutError:
                    _set_ws_error("replay_timeout", str(exc))
                    raise RuntimeError(
                        f"WS_RESULT_INDETERMINATE transport_id={_transport_id} action={action} reconnect timeout"
                    ) from exc
                if not websocket_primary_available() or _agent_ws is None:
                    _set_ws_error("replay_unavailable", str(exc))
                    raise RuntimeError(
                        f"WS_RESULT_INDETERMINATE transport_id={_transport_id} action={action} websocket replay unavailable"
                    ) from exc
                replay_request_id = str(uuid.uuid4())
                replay_message = _build_message(replay_request_id)
                replay_future: asyncio.Future[dict[str, Any]] = loop.create_future()
                replay_entry = dict(pending_entry)
                replay_entry["future"] = replay_future
                replay_entry["message"] = dict(replay_message)
                replay_entry["accepted"] = False
                replay_entry["accepted_event"] = asyncio.Event()
                async with _pending_lock:
                    _pending[replay_request_id] = replay_entry
                try:
                    await _agent_ws.send(json.dumps(replay_message))
                    result = await asyncio.wait_for(replay_future, timeout=timeout)
                    emit_runtime_trace(
                        "gateway.websocket.action.replay",
                        status="ok",
                        trace_id=_trace_id,
                        parent_span_id=_span_id,
                        phase="gateway_dispatch",
                        stage=_stage,
                        project_id=_project_id,
                        task_id=str(task_id or ""),
                        graph_id=_graph_id,
                        node_key=_node_key,
                        node_type=_node_type,
                        session_key=_session_key,
                        action_name=str(action or "").strip(),
                        command_hash=_cmd_hash,
                        working_dir=_working_dir,
                        runtime_mode=_runtime_mode,
                        transport="websocket_primary",
                        details={
                            "request_id": replay_request_id,
                            "transport_id": _transport_id,
                            "replayed": True,
                        },
                    )
                finally:
                    async with _pending_lock:
                        _pending.pop(replay_request_id, None)
            elif _ssh_configured and _fallback_enabled:
                return await _ssh_fallback(
                    "websocket_send_failed" if not accepted else "websocket_result_failed_pre_replay_fallback"
                )
            else:
                raise

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
            session_key=_session_key,
            worker_id=str(result.get("worker_id") or _agent_state.get("worker_id") or ""),
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="websocket_primary",
            error_code="AGENT_ACTION_FAILED" if is_fail else "",
            error_message=str(result.get("error") or inner.get("stderr") or "")[:1200] if isinstance(result, dict) else "invalid action response",
            details={"request_id": request_id, "transport_id": _transport_id},
        )
        return result

    except asyncio.TimeoutError:
        _set_ws_error("action_timeout", f"Timed out waiting for action response (transport_id={_transport_id}).")
        if _ssh_configured and _fallback_enabled and not _action_is_mutating(action):
            return await _ssh_fallback("websocket_timeout_safe_fallback")
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
            session_key=_session_key,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="websocket_primary",
            error_code="ACTION_TIMEOUT",
            error_type="TimeoutError",
            error_message=f"Timed out waiting for action response (transport_id={_transport_id}).",
            details={"request_id": request_id, "timeout": int(timeout), "transport_id": _transport_id},
            debug_bundle=build_debug_bundle(
                failure_class="ACTION_TIMEOUT",
                error_message=f"Timed out waiting for action response (transport_id={_transport_id}).",
                causal_chain=["gateway.transport.select", "gateway.action.dispatch"],
                mitigation_hint="Check worker heartbeat, websocket replay state, and agent execution progress.",
            ),
        )
        raise
    except Exception as exc:
        _set_ws_error(type(exc).__name__, str(exc))
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
            session_key=_session_key,
            action_name=str(action or "").strip(),
            command_hash=_cmd_hash,
            working_dir=_working_dir,
            runtime_mode=_runtime_mode,
            transport="websocket_primary",
            error_code="GATEWAY_SEND_ACTION_ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc)[:1200],
            details={"request_id": request_id, "transport_id": _transport_id},
            debug_bundle=build_debug_bundle(
                failure_class="GATEWAY_SEND_ACTION_ERROR",
                error_message=str(exc),
                causal_chain=["gateway.transport.select", "gateway.action.dispatch"],
                mitigation_hint="Check websocket transport selection, agent heartbeat, and replay state.",
            ),
        )
        raise
    finally:
        async with _pending_lock:
            _pending.pop(request_id, None)


async def send_log_write(stream: str, lines: list[str]) -> None:
    """Send log batch to the connected worker agent for local mirroring."""
    if not bool(getattr(cfg, "LOG_ENABLE_WEBSOCKET_MIRROR", True)):
        return
    if _agent_ws is None:
        _log_mirror_state["last_error"] = "no_agent_connected"
        return
    log_id = uuid.uuid4().hex
    ack_required = bool(getattr(cfg, "LOG_WEBSOCKET_REQUIRE_ACK", True))
    timeout_seconds = max(1, int(getattr(cfg, "LOG_WEBSOCKET_ACK_TIMEOUT_SECONDS", 5) or 5))
    try:
        emit_runtime_trace(
            "websocket.log_write.start",
            status="start",
            phase="gateway_logging",
            transport="websocket_primary",
            worker_id=str(_agent_state.get("worker_id") or ""),
            details={"stream": str(stream or ""), "line_count": len(lines), "log_id": log_id},
        )
        await _agent_ws.send(json.dumps({"type": "log_write", "stream": stream, "lines": lines, "log_id": log_id}))
        _log_mirror_state["last_send_at"] = _utc_now_iso()
        _log_mirror_state["last_error"] = ""
        if ack_required:
            ack = await _wait_for_log_ack(log_id, timeout_seconds)
            _log_mirror_state["last_ack_at"] = _utc_now_iso()
            emit_runtime_trace(
                "websocket.log_write.ack",
                status="ok",
                phase="gateway_logging",
                transport="websocket_primary",
                worker_id=str(ack.get("worker_id") or _agent_state.get("worker_id") or ""),
                details={
                    "stream": str(ack.get("stream") or stream or ""),
                    "line_count": int(ack.get("line_count", len(lines)) or len(lines)),
                    "log_id": log_id,
                },
            )
        emit_runtime_trace(
            "websocket.log_write.ok",
            status="ok",
            phase="gateway_logging",
            transport="websocket_primary",
            worker_id=str(_agent_state.get("worker_id") or ""),
            details={"stream": str(stream or ""), "line_count": len(lines), "log_id": log_id},
        )
    except Exception as exc:
        _log_mirror_state["last_error"] = str(exc)[:500]
        emit_runtime_trace(
            "websocket.log_write.fail",
            status="fail",
            phase="gateway_logging",
            transport="websocket_primary",
            worker_id=str(_agent_state.get("worker_id") or ""),
            error_type=type(exc).__name__,
            error_code="WEBSOCKET_LOG_WRITE_FAILED",
            error_message=str(exc)[:1200],
            details={"stream": str(stream or ""), "line_count": len(lines), "log_id": log_id},
        )


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
    return websocket_primary_available() or get_ssh_executor().is_configured()


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
        _set_ws_error("disconnected", str(exc))
        _set_fallback_reason("agent_disconnected")
        logger.info("Agent disconnected (%s).", exc)
    finally:
        async with _agent_lock:
            _agent_ws = None
            agent_connected.clear()
        # Cancel any pending futures so callers don't hang.
        async with _pending_lock:
            for rid, meta in _pending.items():
                future = meta.get("future") if isinstance(meta, dict) else None
                if future is not None and not future.done():
                    future.set_exception(RuntimeError("Agent disconnected."))
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
        _update_agent_state(msg, heartbeat=False)
        _clear_ws_error()
        logger.info(
            "Agent hello received. worker_id=%s capabilities=%s",
            _agent_state.get("worker_id") or "",
            msg.get("capabilities", []),
        )
        emit_runtime_trace(
            "agent.connection.hello",
            status="ok",
            phase="gateway_transport",
            transport="websocket_primary",
            worker_id=str(_agent_state.get("worker_id") or ""),
            details={
                "hostname": str(_agent_state.get("hostname") or ""),
                "os": str(_agent_state.get("os") or ""),
                "capabilities": list(_agent_state.get("capabilities") or []),
                "allowed_roots": list(_agent_state.get("allowed_roots") or []),
                "coding_agents": dict(_agent_state.get("coding_agents") or {}),
            },
        )
        return

    if msg_type == "agent_heartbeat":
        _update_agent_state(msg, heartbeat=True)
        emit_runtime_trace(
            "agent.connection.heartbeat",
            status="ok",
            phase="gateway_transport",
            transport="websocket_primary",
            worker_id=str(_agent_state.get("worker_id") or ""),
            details={
                "queue_depth": int(_agent_state.get("queue_depth", 0) or 0),
                "coding_agents": dict(_agent_state.get("coding_agents") or {}),
            },
        )
        return

    if msg_type == "action_accepted":
        request_id = str(msg.get("request_id") or "").strip()
        async with _pending_lock:
            pending = _pending.get(request_id)
        if isinstance(pending, dict):
            pending["accepted"] = True
            accepted_event = pending.get("accepted_event")
            if isinstance(accepted_event, asyncio.Event):
                accepted_event.set()
        _update_agent_state(msg, heartbeat=True)
        emit_runtime_trace(
            "gateway.websocket.action.accepted",
            status="ok",
            phase="gateway_dispatch",
            transport="websocket_primary",
            worker_id=str(msg.get("worker_id") or _agent_state.get("worker_id") or ""),
            details={
                "request_id": request_id,
                "transport_id": str(msg.get("transport_id") or ""),
                "accepted_at": str(msg.get("accepted_at") or ""),
            },
        )
        return

    if msg_type == "action_response":
        request_id = str(msg.get("request_id", "") or "")
        async with _pending_lock:
            pending = _pending.get(request_id)
        future = pending.get("future") if isinstance(pending, dict) else None
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

    if msg_type == "log_write_ack":
        log_id = str(msg.get("log_id") or "").strip()
        if log_id:
            async with _pending_log_acks_lock:
                future = _pending_log_acks.get(log_id)
            if future and not future.done():
                future.set_result(msg)
        _log_mirror_state["last_ack_at"] = _utc_now_iso()
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

