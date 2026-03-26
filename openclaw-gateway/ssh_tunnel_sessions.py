from __future__ import annotations

import time
from typing import Any

import gateway_config as bot_cfg


def runtime_trace_context(executor) -> dict[str, Any]:
    ctx = getattr(executor._trace_local, "ctx", None)
    if isinstance(ctx, dict):
        return dict(ctx)
    return {}


def register_active_session(executor, session_key: str, **fields: Any) -> None:
    key = str(session_key or "").strip()
    if not key:
        return
    with executor._active_sessions_lock:
        entry = dict(executor._active_sessions.get(key) or {})
        entry.update(fields)
        entry.setdefault("session_key", key)
        entry.setdefault("started_at", time.time())
        executor._active_sessions[key] = entry


def update_active_session(executor, session_key: str, **fields: Any) -> None:
    register_active_session(executor, session_key, **fields)


def get_active_session(executor, session_key: str) -> dict[str, Any]:
    key = str(session_key or "").strip()
    if not key:
        return {}
    with executor._active_sessions_lock:
        return dict(executor._active_sessions.get(key) or {})


def pop_active_session(executor, session_key: str) -> dict[str, Any]:
    key = str(session_key or "").strip()
    if not key:
        return {}
    with executor._active_sessions_lock:
        return dict(executor._active_sessions.pop(key, {}) or {})


def trace_fields(executor, trace_ctx: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload = {
        "trace_id": str(trace_ctx.get("trace_id") or ""),
        "root_trace_id": str(trace_ctx.get("root_trace_id") or trace_ctx.get("trace_id") or ""),
        "parent_span_id": str(trace_ctx.get("span_id") or ""),
        "phase": str(trace_ctx.get("phase") or "ssh_executor"),
        "stage": str(trace_ctx.get("stage") or ""),
        "project_id": str(trace_ctx.get("project_id") or ""),
        "task_id": str(trace_ctx.get("task_id") or ""),
        "graph_id": str(trace_ctx.get("graph_id") or ""),
        "node_key": str(trace_ctx.get("node_key") or ""),
        "node_type": str(trace_ctx.get("node_type") or ""),
        "worker_id": str(trace_ctx.get("worker_id") or ""),
        "transport": "ssh_first",
        "runtime_mode": str(bot_cfg.effective_orchestration_mode() or "legacy").strip().lower(),
        "action_name": str(trace_ctx.get("action_name") or ""),
        "command_hash": str(trace_ctx.get("command_hash") or ""),
        "working_dir": str(trace_ctx.get("working_dir") or ""),
        "session_key": str(trace_ctx.get("session_key") or ""),
        "remote_pid": str(trace_ctx.get("remote_pid") or ""),
        "artifact_count": max(0, int(trace_ctx.get("artifact_count") or 0)),
    }
    payload.update(extra)
    return payload
