from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as cfg

_TRACE_LOGGER = logging.getLogger("skynet.trace.mirror")
_FILE_LOCK = threading.Lock()
_LOGGER_LOCK = threading.Lock()
_FAIL_STATUSES = {"fail", "failed", "error"}
_SENSITIVE_TOKENS = (
    "token",
    "password",
    "secret",
    "key",
    "session",
    "auth",
    "cookie",
    "pat",
    "private",
)
_STRING_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bot\d{6,}:[A-Za-z0-9_-]{20,})\b"), "[REDACTED_TELEGRAM_BOT_TOKEN]"),
    (re.compile(r"(?is)-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----"), "[REDACTED_PEM_BLOCK]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret|session)\s*[:=]\s*([^\s,;]+)"), r"\1=[REDACTED]"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()


def _truncate(value: str, limit: int = 1200) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated:{len(text) - limit}]"


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    return bool(lowered) and any(token in lowered for token in _SENSITIVE_TOKENS)


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    mode = str(
        getattr(
            cfg,
            "RUNTIME_TRACE_REDACTION_MODE",
            getattr(cfg, "RUNTIME_TRACE_PAYLOAD_MODE", "forensic_redacted"),
        )
        or "forensic_redacted"
    ).strip().lower()
    if mode == "raw":
        return value

    if isinstance(value, dict):
        return {str(k): _sanitize_value(v, key=str(k)) for k, v in value.items()}

    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]

    if isinstance(value, tuple):
        return [_sanitize_value(item, key=key) for item in value]

    if value is None:
        return None

    if isinstance(value, (int, float, bool)):
        return value

    text = _truncate(str(value), limit=4000)
    if mode == "redacted":
        if _is_sensitive_key(key):
            return "[REDACTED]"
        return text
    if mode == "forensic_redacted":
        if _is_sensitive_key(key):
            return "[REDACTED]"
        return _sanitize_string_body(text)

    # redacted_hash (default)
    if _is_sensitive_key(key):
        return {"redacted": True, "sha1": _sha1_text(text), "len": len(text)}
    return _sanitize_string_body(text)


def _sanitize_string_body(value: str) -> str:
    text = str(value or "")
    for pattern, replacement in _STRING_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _runtime_trace_file_path() -> Path:
    explicit = str(getattr(cfg, "RUNTIME_TRACE_LIVE_FILE", "") or "").strip()
    if explicit:
        return Path(explicit)
    return Path(str(getattr(cfg, "LOG_DIR", ".") or ".")) / "skynet.trace.log"


def _append_line(line: str) -> None:
    target = _runtime_trace_file_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _FILE_LOCK:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
    except Exception:
        # Runtime tracing must never break primary flow.
        pass


def _normalize_flow(flow: str | None) -> str:
    candidate = str(flow or "").strip().lower()
    if candidate in {"telegram_real", "conversation", "direct"}:
        return candidate
    env_flow = str(os.environ.get("SKYNET_LIVE_E2E_FLOW", "") or "").strip().lower()
    if env_flow in {"telegram_real", "conversation", "direct"}:
        return env_flow
    return "direct"


def build_debug_bundle(
    *,
    failure_class: str,
    error_message: str = "",
    causal_chain: list[str] | None = None,
    last_success_event: str = "",
    stdout_tail: str = "",
    stderr_tail: str = "",
    inputs_hashes: dict[str, str] | None = None,
    file_diff_summary: str = "",
    files_touched: list[str] | None = None,
    mitigation_hint: str = "",
    retry_policy_snapshot: dict[str, Any] | None = None,
    process_tree: list[dict[str, Any]] | None = None,
    prompt_file: dict[str, Any] | None = None,
    artifact_snapshot: list[dict[str, Any]] | None = None,
    artifact_count: int | None = None,
    stop_cleanup_status: str = "",
) -> dict[str, Any]:
    stdio_limit = max(256, int(getattr(cfg, "RUNTIME_TRACE_STDIO_TAIL_BYTES", 4000) or 4000))
    include_stdio = bool(getattr(cfg, "RUNTIME_TRACE_INCLUDE_STDIO_TAIL", True))
    return {
        "failure_class": str(failure_class or "UNKNOWN").strip() or "UNKNOWN",
        "error_message": _truncate(error_message, 1500),
        "causal_chain": [str(item).strip() for item in (causal_chain or []) if str(item).strip()],
        "last_success_event": str(last_success_event or "").strip(),
        "stdout_tail": _truncate(stdout_tail, stdio_limit) if include_stdio else "",
        "stderr_tail": _truncate(stderr_tail, stdio_limit) if include_stdio else "",
        "inputs_hashes": dict(inputs_hashes or {}),
        "file_diff_summary": _truncate(file_diff_summary, 1000),
        "files_touched": [str(item).strip() for item in (files_touched or []) if str(item).strip()],
        "mitigation_hint": _truncate(mitigation_hint, 800),
        "retry_policy_snapshot": _sanitize_value(dict(retry_policy_snapshot or {}), key="retry_policy_snapshot"),
        "process_tree": _sanitize_value(list(process_tree or []), key="process_tree"),
        "prompt_file": _sanitize_value(dict(prompt_file or {}), key="prompt_file"),
        "artifact_snapshot": _sanitize_value(list(artifact_snapshot or []), key="artifact_snapshot"),
        "artifact_count": max(0, int(artifact_count or 0)),
        "stop_cleanup_status": _truncate(stop_cleanup_status, 200),
    }


def build_process_debug_bundle(
    *,
    process_tree: list[dict[str, Any]] | None = None,
    prompt_file: dict[str, Any] | None = None,
    remote_pid: str = "",
    stop_cleanup_status: str = "",
) -> dict[str, Any]:
    return {
        "process_tree": _sanitize_value(list(process_tree or []), key="process_tree"),
        "prompt_file": _sanitize_value(dict(prompt_file or {}), key="prompt_file"),
        "remote_pid": str(remote_pid or "").strip(),
        "stop_cleanup_status": _truncate(stop_cleanup_status, 200),
    }


def build_artifact_debug_bundle(
    *,
    artifact_snapshot: list[dict[str, Any]] | None = None,
    artifact_count: int = 0,
    files_touched: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_snapshot": _sanitize_value(list(artifact_snapshot or []), key="artifact_snapshot"),
        "artifact_count": max(0, int(artifact_count or 0)),
        "files_touched": [str(item).strip() for item in (files_touched or []) if str(item).strip()],
    }


def _required_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "event_id": payload.get("event_id") or uuid.uuid4().hex,
        "trace_id": payload.get("trace_id") or uuid.uuid4().hex,
        "root_trace_id": payload.get("root_trace_id") or payload.get("trace_id") or uuid.uuid4().hex,
        "span_id": payload.get("span_id") or uuid.uuid4().hex[:16],
        "parent_span_id": payload.get("parent_span_id") or "",
        "session_key": str(payload.get("session_key") or ""),
        "flow": _normalize_flow(str(payload.get("flow") or "")),
        "project_id": str(payload.get("project_id") or ""),
        "task_id": str(payload.get("task_id") or ""),
        "graph_id": str(payload.get("graph_id") or ""),
        "node_key": str(payload.get("node_key") or ""),
        "node_type": str(payload.get("node_type") or ""),
        "phase": str(payload.get("phase") or ""),
        "stage": str(payload.get("stage") or ""),
        "gate": str(payload.get("gate") or ""),
        "worker_id": str(payload.get("worker_id") or ""),
        "transport": str(payload.get("transport") or ""),
        "runtime_mode": str(payload.get("runtime_mode") or ""),
        "error_type": str(payload.get("error_type") or ""),
        "error_code": str(payload.get("error_code") or ""),
        "error_message": _truncate(str(payload.get("error_message") or ""), 2000),
        "telegram_chat_id": str(payload.get("telegram_chat_id") or ""),
        "telegram_user_id": str(payload.get("telegram_user_id") or ""),
        "telegram_message_id": str(payload.get("telegram_message_id") or ""),
        "action_name": str(payload.get("action_name") or ""),
        "command_hash": str(payload.get("command_hash") or ""),
        "working_dir": str(payload.get("working_dir") or ""),
        "remote_pid": str(payload.get("remote_pid") or ""),
        "artifact_count": max(0, int(payload.get("artifact_count") or 0)),
    }
    return defaults


def emit_runtime_trace(
    event: str,
    *,
    level: str = "info",
    status: str = "ok",
    emit_debug_bundle: bool = True,
    **fields: Any,
) -> dict[str, Any]:
    if not bool(getattr(cfg, "RUNTIME_TRACE_ENABLED", True)):
        return {}

    payload: dict[str, Any] = {
        "ts": _utc_now(),
        "level": str(level or "info").strip().lower(),
        "event": str(event or "unknown").strip() or "unknown",
        "status": str(status or "ok").strip().lower(),
    }
    payload.update(_required_defaults(fields))
    details = dict(fields)
    for key in list(_required_defaults(fields).keys()):
        details.pop(key, None)
    details.pop("event", None)
    details.pop("status", None)
    details.pop("level", None)
    payload["details"] = _sanitize_value(details, key="details")

    line = json.dumps(payload, ensure_ascii=True, default=str)
    _append_line(line)
    with _LOGGER_LOCK:
        _TRACE_LOGGER.info(line)

    if (
        emit_debug_bundle
        and payload.get("status") in _FAIL_STATUSES
        and payload.get("event") != "debug.bundle"
        and bool(getattr(cfg, "RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE", True))
    ):
        debug_bundle = fields.get("debug_bundle")
        if not isinstance(debug_bundle, dict):
            debug_bundle = build_debug_bundle(
                failure_class=str(fields.get("failure_class") or fields.get("error_code") or "UNKNOWN"),
                error_message=str(payload.get("error_message") or ""),
                causal_chain=[str(payload.get("event") or "")],
                mitigation_hint=str(fields.get("mitigation_hint") or "Inspect causal chain and stage/gate traces."),
            )
        emit_runtime_trace(
            "debug.bundle",
            level="error",
            status="fail",
            emit_debug_bundle=False,
            root_trace_id=payload.get("root_trace_id"),
            trace_id=payload.get("trace_id"),
            parent_span_id=payload.get("span_id"),
            session_key=payload.get("session_key"),
            flow=payload.get("flow"),
            project_id=payload.get("project_id"),
            task_id=payload.get("task_id"),
            graph_id=payload.get("graph_id"),
            node_key=payload.get("node_key"),
            node_type=payload.get("node_type"),
            phase=payload.get("phase"),
            stage=payload.get("stage"),
            gate=payload.get("gate"),
            worker_id=payload.get("worker_id"),
            transport=payload.get("transport"),
            runtime_mode=payload.get("runtime_mode"),
            error_type=payload.get("error_type"),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
            action_name=payload.get("action_name"),
            command_hash=payload.get("command_hash"),
            working_dir=payload.get("working_dir"),
            remote_pid=payload.get("remote_pid"),
            artifact_count=payload.get("artifact_count"),
            debug_bundle=debug_bundle,
        )

    return payload


async def emit_runtime_trace_async(
    *,
    db: Any | None = None,
    event: str,
    level: str = "info",
    status: str = "ok",
    **fields: Any,
) -> dict[str, Any]:
    payload = emit_runtime_trace(event, level=level, status=status, emit_debug_bundle=False, **fields)
    if not payload:
        return {}
    if db is not None and bool(getattr(cfg, "RUNTIME_TRACE_DB_ENABLED", True)):
        try:
            from db.store import create_runtime_trace_event

            await create_runtime_trace_event(db, event_payload=payload)
        except Exception:
            pass

    if (
        payload.get("status") in _FAIL_STATUSES
        and bool(getattr(cfg, "RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE", True))
    ):
        debug_bundle = fields.get("debug_bundle")
        if not isinstance(debug_bundle, dict):
            debug_bundle = build_debug_bundle(
                failure_class=str(fields.get("failure_class") or fields.get("error_code") or "UNKNOWN"),
                error_message=str(payload.get("error_message") or ""),
                causal_chain=[str(payload.get("event") or "")],
                mitigation_hint=str(fields.get("mitigation_hint") or "Inspect debug.bundle and stage/gate events."),
            )
        debug_event = emit_runtime_trace(
            "debug.bundle",
            level="error",
            status="fail",
            emit_debug_bundle=False,
            root_trace_id=payload.get("root_trace_id"),
            trace_id=payload.get("trace_id"),
            parent_span_id=payload.get("span_id"),
            session_key=payload.get("session_key"),
            flow=payload.get("flow"),
            project_id=payload.get("project_id"),
            task_id=payload.get("task_id"),
            graph_id=payload.get("graph_id"),
            node_key=payload.get("node_key"),
            node_type=payload.get("node_type"),
            phase=payload.get("phase"),
            stage=payload.get("stage"),
            gate=payload.get("gate"),
            worker_id=payload.get("worker_id"),
            transport=payload.get("transport"),
            runtime_mode=payload.get("runtime_mode"),
            error_type=payload.get("error_type"),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
            action_name=payload.get("action_name"),
            command_hash=payload.get("command_hash"),
            working_dir=payload.get("working_dir"),
            remote_pid=payload.get("remote_pid"),
            artifact_count=payload.get("artifact_count"),
            debug_bundle=debug_bundle,
        )
        if db is not None and bool(getattr(cfg, "RUNTIME_TRACE_DB_ENABLED", True)):
            try:
                from db.store import create_runtime_trace_event

                await create_runtime_trace_event(db, event_payload=debug_event)
            except Exception:
                pass

    return payload


def command_hash(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        return ""
    return _sha1_text(text)


def command_preview(command: str) -> dict[str, str]:
    text = _sanitize_string_body(str(command or ""))
    limit = max(80, int(getattr(cfg, "RUNTIME_TRACE_COMMAND_PREVIEW_CHARS", 2000) or 2000))
    text = _truncate(text, limit=limit)
    return {
        "command_preview": text,
        "command_hash": command_hash(command),
    }
