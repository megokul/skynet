"""
SKYNET Gateway - SSH Tunnel Action Executor

Fallback execution path when no OpenClaw worker is connected.
Runs allowlisted actions directly on a remote laptop over SSH.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import shlex
import stat
import json
import threading
import time
import uuid
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import paramiko

_log = logging.getLogger(__name__)

import gateway_config as bot_cfg
from runtime_trace import (
    build_artifact_debug_bundle,
    build_debug_bundle,
    build_process_debug_bundle,
    command_hash,
    command_preview,
    emit_runtime_trace,
)
from skynet.prompt_library import load_prompt
from skynet.qwen_cli import (
    build_qwen_command_args,
    qwen_profile_for_task_mode,
)
from skynet.qwen_runtime import normalize_qwen_result, normalize_qwen_task_mode
from search.web_search import WebSearcher
from ssh_tunnel_config import load_ssh_executor_config
from ssh_tunnel_health import SSHHealthTracker
import ssh_tunnel_sessions
from ssh_tunnel_support import (
    build_linux_command as _build_linux_command,
    build_windows_command as _build_windows_command,
    is_allowed_path as _is_allowed_path,
    norm_remote_path as _norm_remote_path,
    ps_quote as _ps_quote,
    sanitize_powershell_output as _sanitize_powershell_output,
)


def _looks_like_ssh_infra_error(detail: str) -> bool:
    lowered = str(detail or "").strip().lower()
    markers = (
        "ssh",
        "socket",
        "timed out",
        "timeout",
        "connection refused",
        "connection reset",
        "banner",
        "auth",
        "authentication",
        "permission denied",
        "name or service not known",
        "network is unreachable",
        "maxstartups",
    )
    return any(marker in lowered for marker in markers)


class SSHTunnelExecutor:
    """Remote action executor using SSH."""

    _PATH_KEYS = {"working_dir", "directory", "file", "path", "project_dir"}

    def __init__(self) -> None:
        config = load_ssh_executor_config()

        self.enabled = config.enabled
        self.host = config.host
        self.port = config.port
        self.username = config.username
        self.password = config.password
        self.key_path = config.key_path
        self.connect_timeout = config.connect_timeout
        self.command_timeout = config.command_timeout
        self.remote_os = config.remote_os
        self.strict_host_key = config.strict_host_key
        self.allowed_roots = list(config.allowed_roots)

        self._searcher = WebSearcher(config.brave_search_api_key)
        self._coding_bins = dict(config.coding_bins)
        self._coding_prefix = {name: list(tokens) for name, tokens in config.coding_prefix.items()}
        self._codex_write_mode = config.codex_write_mode
        self._qwen_policy = dict(config.qwen_policy)
        self._closeable_apps = dict(config.closeable_apps)
        self._health_cache_seconds = config.health_cache_seconds
        self._last_health_at = 0.0
        self._last_health: tuple[bool, str] = (False, "SSH health not checked yet")
        self._max_parallel = config.max_parallel
        self._circuit_breaker_seconds = config.circuit_breaker_seconds
        self._capacity_backoff_seconds = config.capacity_backoff_seconds
        self._health_probe_timeout = config.health_probe_timeout
        self._trace_local = threading.local()
        self._active_sessions: dict[str, dict[str, Any]] = {}
        self._active_sessions_lock = threading.Lock()
        self._parallel_sem = threading.BoundedSemaphore(self._max_parallel)
        self._health = SSHHealthTracker(
            circuit_breaker_seconds=config.circuit_breaker_seconds,
            capacity_backoff_seconds=config.capacity_backoff_seconds,
        )
        self._cline_auto_switch = config.cline_auto_switch
        self._cline_provider_priority = list(config.cline_provider_priority)
        self._cline_provider_base_urls = dict(config.cline_provider_base_urls)
        self._claude_permission_mode = config.claude_permission_mode
        self._claude_disable_slash_commands = config.claude_disable_slash_commands
        self._claude_dangerously_skip_permissions = config.claude_dangerously_skip_permissions

    def is_configured(self) -> bool:
        return self.enabled and bool(self.username and self.host)

    def _classify_ssh_error(self, detail: str) -> str:
        return self._health.classify_error(detail)

    def _retry_delay_for_category(self, category: str, attempt: int) -> int:
        return self._health.retry_delay_for_category(category, attempt)

    def _record_ssh_success(self) -> None:
        self._health.record_success()

    def _record_ssh_failure(self, category: str) -> None:
        self._health.record_failure(category)

    def _circuit_remaining_seconds(self) -> int:
        return self._health.circuit_remaining_seconds()

    def get_diagnostics(self) -> dict[str, Any]:
        return self._health.diagnostics(
            configured=self.is_configured(),
            endpoint=f"{self.host}:{self.port}",
            healthy=bool(self._last_health[0]),
        )

    async def health_check(self) -> tuple[bool, str]:

        if not self.is_configured():
            self._last_health = (False, "SSH executor not configured")
            return self._last_health

        remaining = self._circuit_remaining_seconds()
        if remaining > 0:
            detail = f"SSH circuit open ({self.host}:{self.port}), retry after {remaining}s"
            self._last_health = (False, detail)
            self._last_health_at = time.time()
            return self._last_health

        now = time.time()
        if now - self._last_health_at < max(self._health_cache_seconds, 1):
            return self._last_health

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._probe_sync)
            self._record_ssh_success()
            self._last_health = (True, f"{self.username}@{self.host}:{self.port}")
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            category = self._classify_ssh_error(detail)
            self._record_ssh_failure(category)
            self._last_health = (False, detail)
        self._last_health_at = now
        return self._last_health

    async def execute_action(
        self,
        action: str,
        params: dict[str, Any],
        confirmed: bool = True,
    ) -> dict[str, Any]:

        del confirmed
        raw_params = dict(params or {})
        project_id = str(raw_params.get("project_id") or "")
        task_id = str(raw_params.get("task_id") or "")
        graph_id = str(raw_params.get("graph_id") or "")
        node_key = str(raw_params.get("node_key") or "")
        node_type = str(raw_params.get("node_type") or "")
        stage = str(raw_params.get("agent") or raw_params.get("stage") or "").strip().lower()
        working_dir = str(
            raw_params.get("working_dir")
            or raw_params.get("project_dir")
            or raw_params.get("directory")
            or ""
        )
        cmd_hash = command_hash(str(raw_params.get("command") or raw_params.get("prompt") or ""))
        worker_id = str(
            raw_params.get("worker_id")
            or getattr(bot_cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "")
            or "worker-primary"
        )
        session_key = str(raw_params.get("session_key") or "").strip()
        action_trace_id = uuid.uuid4().hex
        action_span_id = uuid.uuid4().hex[:16]
        base_trace = {
            "trace_id": action_trace_id,
            "root_trace_id": action_trace_id,
            "span_id": action_span_id,
            "parent_span_id": "",
            "phase": "ssh_executor",
            "stage": stage,
            "project_id": project_id,
            "task_id": task_id,
            "graph_id": graph_id,
            "node_key": node_key,
            "node_type": node_type,
            "worker_id": worker_id,
            "transport": "ssh_first",
            "runtime_mode": str(bot_cfg.effective_orchestration_mode() or "legacy").strip().lower(),
            "action_name": str(action or "").strip(),
            "command_hash": cmd_hash,
            "working_dir": working_dir,
            "session_key": session_key,
        }
        emit_runtime_trace(
            "ssh.action.dispatch",
            status="start",
            details={
                "host": self.host,
                "port": int(self.port),
                "auth_mode": "key+password" if (self.key_path and self.password) else ("key" if self.key_path else ("password" if self.password else "agent")),
                "remote_os": self.remote_os,
            },
            **base_trace,
        )

        if not self.is_configured():
            emit_runtime_trace(
                "ssh.action.dispatch",
                status="fail",
                error_code="SSH_NOT_CONFIGURED",
                error_message="SSH fallback is not configured.",
                details={"host": self.host, "port": int(self.port)},
                **base_trace,
            )
            return {"status": "error", "action": action, "error": "SSH fallback is not configured."}

        remaining = self._circuit_remaining_seconds()
        if remaining > 0:
            diag = self.get_diagnostics()
            category = str(diag.get("ssh_error_category") or "unknown")
            endpoint = str(diag.get("ssh_endpoint") or f"{self.host}:{self.port}")
            err = (
                "SSH action failed: "
                f"SSH_INFRA_CIRCUIT {endpoint} - circuit open after {category} failures. "
                f"Retry after {remaining}s."
            )
            emit_runtime_trace(
                "ssh.action.dispatch",
                status="fail",
                error_type="RuntimeError",
                error_code="SSH_INFRA_CIRCUIT",
                error_message=err,
                details={
                    "host": self.host,
                    "port": int(self.port),
                    "error_category": category,
                    "retry_after_s": int(remaining),
                },
                debug_bundle=build_debug_bundle(
                    failure_class="SSH_INFRA_CIRCUIT",
                    error_message=err,
                    causal_chain=["ssh.action.dispatch"],
                    mitigation_hint="Wait for circuit cooldown or fix repeated SSH infra errors.",
                ),
                **base_trace,
            )
            return {
                "status": "error",
                "action": action,
                "error_category": category,
                "retry_after_s": remaining,
                "error": err,
            }

        params = dict(params or {})
        for key in self._PATH_KEYS:
            if isinstance(params.get(key), str):
                val = _norm_remote_path(params[key], self.remote_os)
                if not _is_allowed_path(val, self.allowed_roots, self.remote_os):
                    error_text = f"Path '{params[key]}' is outside OPENCLAW_SSH_ALLOWED_ROOTS."
                    emit_runtime_trace(
                        "ssh.action.dispatch",
                        status="fail",
                        error_code="PATH_OUTSIDE_ALLOWED_ROOTS",
                        error_message=error_text,
                        details={
                            "path_key": key,
                            "candidate_path": str(params.get(key)),
                            "normalized_path": val,
                            "allowed_roots": list(self.allowed_roots),
                        },
                        **base_trace,
                    )
                    return {
                        "status": "error",
                        "action": action,
                        "error": error_text,
                    }
                params[key] = val

        if action == "web_search":
            try:
                query = str(params.get("query") or "").strip()
                if not query:
                    raise ValueError("Missing required parameter: 'query'")
                raw_num = params.get("num_results", 5)
                num = int(raw_num) if isinstance(raw_num, (int, str)) else 5
                num = min(max(num, 1), 10)
                output = await self._searcher.search(query, num)
                emit_runtime_trace(
                    "ssh.action.dispatch",
                    status="ok",
                    details={"result_returncode": 0},
                    **base_trace,
                )
                return {
                    "status": "ok",
                    "action": action,
                    "result": {"returncode": 0, "stdout": output, "stderr": ""},
                }
            except Exception as exc:
                error_text = f"Web search failed: {exc}"
                emit_runtime_trace(
                    "ssh.action.dispatch",
                    status="fail",
                    error_type=type(exc).__name__,
                    error_code="WEB_SEARCH_FAILED",
                    error_message=error_text,
                    details={"query_hash": command_hash(str(params.get("query") or ""))},
                    **base_trace,
                )
                return {
                    "status": "ok",
                    "action": action,
                    "result": {"returncode": 1, "stdout": "", "stderr": error_text},
                }

        loop = asyncio.get_running_loop()
        try:
            trace_ctx = dict(base_trace)
            result = await loop.run_in_executor(None, self._execute_sync, action, params, trace_ctx)
            self._record_ssh_success()
            rc = int(result.get("returncode", 0) or 0) if isinstance(result, dict) else 0
            is_fail = rc != 0
            emit_runtime_trace(
                "ssh.action.dispatch",
                status="fail" if is_fail else "ok",
                error_code="SSH_ACTION_NONZERO" if is_fail else "",
                error_message=str(result.get("stderr") or "")[:1200] if is_fail and isinstance(result, dict) else "",
                details={"result_returncode": rc},
                **base_trace,
            )
            return {"status": "ok", "action": action, "result": result}
        except Exception as exc:
            err_type = type(exc).__name__
            err_msg = str(exc).strip()
            if not err_msg:
                err_msg = err_type
            else:
                err_msg = f"{err_type}: {err_msg}"
            is_infra = isinstance(exc, (OSError, TimeoutError, paramiko.SSHException)) or _looks_like_ssh_infra_error(err_msg)
            if is_infra:
                category = self._classify_ssh_error(err_msg)
                self._record_ssh_failure(category)
                remaining_after = self._circuit_remaining_seconds()
                endpoint = f"{self.host}:{self.port}"
                infra_code = f"SSH_INFRA_{category.upper()}"
                err_msg = f"{infra_code} {endpoint} - {err_msg}"
                payload: dict[str, Any] = {
                    "status": "error",
                    "action": action,
                    "error_category": category,
                    "error": f"SSH action failed: {err_msg}",
                }
                if remaining_after > 0:
                    payload["retry_after_s"] = remaining_after
                emit_runtime_trace(
                    "ssh.action.dispatch",
                    status="fail",
                    error_type=err_type,
                    error_code=infra_code,
                    error_message=f"SSH action failed: {err_msg}",
                    details={
                        "host": self.host,
                        "port": int(self.port),
                        "error_category": category,
                        "retry_after_s": int(remaining_after),
                        "auth_mode": "key+password" if (self.key_path and self.password) else ("key" if self.key_path else ("password" if self.password else "agent")),
                    },
                    debug_bundle=build_debug_bundle(
                        failure_class=infra_code,
                        error_message=f"SSH action failed: {err_msg}",
                        causal_chain=["ssh.action.dispatch"],
                        mitigation_hint="Inspect tunnel health, SSH auth, and endpoint reachability.",
                    ),
                    **base_trace,
                )
                _log.error("SSH infra action '%s' failed (%s): %s", action, category, err_msg, exc_info=True)
                return payload

            emit_runtime_trace(
                "ssh.action.dispatch",
                status="fail",
                error_type=err_type,
                error_code="SSH_ACTION_ERROR",
                error_message=f"SSH action failed: {err_msg}",
                details={"host": self.host, "port": int(self.port)},
                debug_bundle=build_debug_bundle(
                    failure_class="SSH_ACTION_ERROR",
                    error_message=f"SSH action failed: {err_msg}",
                    causal_chain=["ssh.action.dispatch"],
                    mitigation_hint="Inspect action inputs and remote command stderr.",
                ),
                **base_trace,
            )
            _log.error("SSH action '%s' failed: %s", action, err_msg, exc_info=True)
            return {"status": "error", "action": action, "error": f"SSH action failed: {err_msg}"}

    def _probe_sync(self) -> None:

        remaining = self._circuit_remaining_seconds()
        if remaining > 0:
            raise RuntimeError(f"SSH circuit open, retry after {remaining}s")

        slot_timeout = max(1, self._health_probe_timeout)
        acquired = self._parallel_sem.acquire(timeout=slot_timeout)
        if not acquired:
            raise RuntimeError(
                "SSH health probe skipped: OPENCLAW_SSH_MAX_PARALLEL limit reached."
            )
        try:
            client = self._connect(max_retries=1, timeout_override=self._health_probe_timeout)
            try:
                probe_args = ["cmd", "/c", "echo", "ok"] if self.remote_os == "windows" else ["sh", "-lc", "echo ok"]
                command = self._build_command(probe_args, cwd=None)
                _, stdout, stderr = client.exec_command(command, timeout=self._health_probe_timeout)
                _ = stdout.read()
                _ = stderr.read()
            finally:
                client.close()
        finally:
            self._parallel_sem.release()

    def _connect(self, *, max_retries: int = 3, timeout_override: int | None = None) -> paramiko.SSHClient:

        retries = max(1, int(max_retries))
        last_exc: Exception | None = None
        connect_timeout = max(1, int(timeout_override or self.connect_timeout))
        trace_ctx = self._runtime_trace_context()

        for attempt in range(1, retries + 1):
            emit_runtime_trace(
                "ssh.connect.attempt",
                status="start",
                trace_id=str(trace_ctx.get("trace_id") or ""),
                parent_span_id=str(trace_ctx.get("span_id") or ""),
                phase="ssh_connect",
                stage=str(trace_ctx.get("stage") or ""),
                project_id=str(trace_ctx.get("project_id") or ""),
                task_id=str(trace_ctx.get("task_id") or ""),
                graph_id=str(trace_ctx.get("graph_id") or ""),
                node_key=str(trace_ctx.get("node_key") or ""),
                node_type=str(trace_ctx.get("node_type") or ""),
                worker_id=str(trace_ctx.get("worker_id") or ""),
                transport="ssh_first",
                runtime_mode=str(bot_cfg.effective_orchestration_mode() or "legacy").strip().lower(),
                details={
                    "attempt": int(attempt),
                    "retries": int(retries),
                    "host": self.host,
                    "port": int(self.port),
                    "username": self.username,
                    "timeout": int(connect_timeout),
                    "strict_host_key": bool(self.strict_host_key),
                    "auth_mode": "key+password" if (self.key_path and self.password) else ("key" if self.key_path else ("password" if self.password else "agent")),
                },
            )
            client = paramiko.SSHClient()
            if self.strict_host_key:
                client.load_system_host_keys()
            else:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            kwargs: dict[str, Any] = {
                "hostname": self.host,
                "port": self.port,
                "username": self.username,
                "timeout": connect_timeout,
                "auth_timeout": connect_timeout,
                "banner_timeout": connect_timeout,
                "look_for_keys": True,
                "allow_agent": True,
            }
            if self.key_path:
                kwargs["key_filename"] = self.key_path
            if self.password:
                kwargs["password"] = self.password

            try:
                client.connect(**kwargs)
            except (OSError, paramiko.SSHException) as exc:
                last_exc = exc
                client.close()
                err_detail = str(exc).strip() or type(exc).__name__
                category = self._classify_ssh_error(err_detail)
                emit_runtime_trace(
                    "ssh.connect.attempt",
                    status="fail",
                    trace_id=str(trace_ctx.get("trace_id") or ""),
                    parent_span_id=str(trace_ctx.get("span_id") or ""),
                    phase="ssh_connect",
                    stage=str(trace_ctx.get("stage") or ""),
                    project_id=str(trace_ctx.get("project_id") or ""),
                    task_id=str(trace_ctx.get("task_id") or ""),
                    graph_id=str(trace_ctx.get("graph_id") or ""),
                    node_key=str(trace_ctx.get("node_key") or ""),
                    node_type=str(trace_ctx.get("node_type") or ""),
                    worker_id=str(trace_ctx.get("worker_id") or ""),
                    transport="ssh_first",
                    runtime_mode=str(bot_cfg.effective_orchestration_mode() or "legacy").strip().lower(),
                    error_type=type(exc).__name__,
                    error_code=f"SSH_CONNECT_{category.upper()}",
                    error_message=err_detail,
                    details={
                        "attempt": int(attempt),
                        "retries": int(retries),
                        "host": self.host,
                        "port": int(self.port),
                        "category": category,
                    },
                )
                if category == "auth":
                    _log.error("SSH authentication failed to %s:%s (%s)", self.host, self.port, err_detail)
                    raise RuntimeError(
                        f"SSH authentication failed to {self.host}:{self.port}: {err_detail}"
                    ) from exc
                if attempt < retries:
                    delay = self._retry_delay_for_category(category, attempt)
                    _log.warning(
                        "SSH connect attempt %d/%d failed [%s] (%s), retrying in %ds...",
                        attempt, retries, category, err_detail, delay,
                    )
                    time.sleep(delay)
                    continue
                _log.error("SSH connect failed after %d attempts [%s]: %s", retries, category, err_detail)
                raise RuntimeError(
                    f"SSH connect failed to {self.host}:{self.port} after {retries} attempts: {err_detail}"
                ) from exc

            # Warm up the transport by opening (then closing) an SFTP session.
            # Some SSH servers/tunnels close the transport after the first exec
            # channel exits, causing "No existing session" on subsequent calls.
            # Opening SFTP first stabilises the transport for the session lifetime.
            try:
                sftp = client.open_sftp()
                sftp.close()
            except Exception:
                pass  # best effort - if SFTP probe fails, proceed anyway

            emit_runtime_trace(
                "ssh.connect.attempt",
                status="ok",
                trace_id=str(trace_ctx.get("trace_id") or ""),
                parent_span_id=str(trace_ctx.get("span_id") or ""),
                phase="ssh_connect",
                stage=str(trace_ctx.get("stage") or ""),
                project_id=str(trace_ctx.get("project_id") or ""),
                task_id=str(trace_ctx.get("task_id") or ""),
                graph_id=str(trace_ctx.get("graph_id") or ""),
                node_key=str(trace_ctx.get("node_key") or ""),
                node_type=str(trace_ctx.get("node_type") or ""),
                worker_id=str(trace_ctx.get("worker_id") or ""),
                transport="ssh_first",
                runtime_mode=str(bot_cfg.effective_orchestration_mode() or "legacy").strip().lower(),
                details={
                    "attempt": int(attempt),
                    "host": self.host,
                    "port": int(self.port),
                },
            )
            return client

        raise last_exc  # unreachable, but keeps mypy happy

    def _runtime_trace_context(self) -> dict[str, Any]:
        return ssh_tunnel_sessions.runtime_trace_context(self)

    def _register_active_session(self, session_key: str, **fields: Any) -> None:
        ssh_tunnel_sessions.register_active_session(self, session_key, **fields)

    def _update_active_session(self, session_key: str, **fields: Any) -> None:
        ssh_tunnel_sessions.update_active_session(self, session_key, **fields)

    def _get_active_session(self, session_key: str) -> dict[str, Any]:
        return ssh_tunnel_sessions.get_active_session(self, session_key)

    def _pop_active_session(self, session_key: str) -> dict[str, Any]:
        return ssh_tunnel_sessions.pop_active_session(self, session_key)

    def _trace_fields(self, trace_ctx: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return ssh_tunnel_sessions.trace_fields(self, trace_ctx, **extra)

    def _read_channel_stream(
        self,
        *,
        channel: Any,
        trace_ctx: dict[str, Any],
        cmd_hash: str,
        cwd: str | None,
        timeout: int,
    ) -> tuple[str, str, int]:
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_total = 0
        stderr_total = 0
        chunk_limit = max(1024, int(getattr(bot_cfg, "RUNTIME_TRACE_STDIO_CHUNK_BYTES", 16000) or 16000))
        end_at = time.time() + float(max(30, int(timeout or self.command_timeout)))

        emit_runtime_trace(
            "ssh.command.exit.wait",
            status="start",
            details={"timeout_seconds": int(timeout or self.command_timeout)},
            **self._trace_fields(trace_ctx, phase="ssh_command", working_dir=str(cwd or "")),
        )
        while True:
            progress = False
            while channel.recv_ready():
                data = channel.recv(min(chunk_limit, 4096))
                if not data:
                    break
                chunk = data.decode("utf-8", errors="replace")
                if self.remote_os == "windows":
                    chunk = _sanitize_powershell_output(chunk)
                stdout_chunks.append(chunk)
                stdout_total += len(chunk)
                progress = True
                emit_runtime_trace(
                    "ssh.command.stdout.chunk",
                    status="ok",
                    details={"chunk_len": len(chunk), "stdout_total": stdout_total, "chunk_preview": chunk[:800]},
                    **self._trace_fields(
                        trace_ctx,
                        phase="ssh_command",
                        command_hash=cmd_hash,
                        working_dir=str(cwd or ""),
                    ),
                )
            while channel.recv_stderr_ready():
                data = channel.recv_stderr(min(chunk_limit, 4096))
                if not data:
                    break
                chunk = data.decode("utf-8", errors="replace")
                if self.remote_os == "windows":
                    chunk = _sanitize_powershell_output(chunk)
                stderr_chunks.append(chunk)
                stderr_total += len(chunk)
                progress = True
                emit_runtime_trace(
                    "ssh.command.stderr.chunk",
                    status="ok",
                    details={"chunk_len": len(chunk), "stderr_total": stderr_total, "chunk_preview": chunk[:800]},
                    **self._trace_fields(
                        trace_ctx,
                        phase="ssh_command",
                        command_hash=cmd_hash,
                        working_dir=str(cwd or ""),
                    ),
                )
            if channel.exit_status_ready():
                while channel.recv_ready():
                    data = channel.recv(min(chunk_limit, 4096))
                    if not data:
                        break
                    chunk = data.decode("utf-8", errors="replace")
                    if self.remote_os == "windows":
                        chunk = _sanitize_powershell_output(chunk)
                    stdout_chunks.append(chunk)
                    stdout_total += len(chunk)
                while channel.recv_stderr_ready():
                    data = channel.recv_stderr(min(chunk_limit, 4096))
                    if not data:
                        break
                    chunk = data.decode("utf-8", errors="replace")
                    if self.remote_os == "windows":
                        chunk = _sanitize_powershell_output(chunk)
                    stderr_chunks.append(chunk)
                    stderr_total += len(chunk)
                break
            if time.time() >= end_at:
                raise TimeoutError(f"SSH command timed out after {int(timeout or self.command_timeout)}s")
            if not progress:
                time.sleep(0.25)

        rc = int(channel.recv_exit_status())
        emit_runtime_trace(
            "ssh.command.exit.wait",
            status="ok" if rc == 0 else "fail",
            error_code="SSH_COMMAND_NONZERO" if rc != 0 else "",
            error_message=("".join(stderr_chunks))[:1200] if rc != 0 else "",
            details={"returncode": rc, "stdout_len": stdout_total, "stderr_len": stderr_total},
            **self._trace_fields(trace_ctx, phase="ssh_command", command_hash=cmd_hash, working_dir=str(cwd or "")),
        )
        return "".join(stdout_chunks), "".join(stderr_chunks), rc

    def _prompt_file_state(
        self,
        *,
        client: paramiko.SSHClient,
        prompt_path: str,
        pid_path: str = "",
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "path": prompt_path,
            "exists": False,
            "size": 0,
            "mtime": 0,
            "pid_path": pid_path,
            "pid_exists": False,
            "remote_pid": "",
        }
        sftp = client.open_sftp()
        try:
            try:
                stat_result = sftp.stat(prompt_path)
                state["exists"] = True
                state["size"] = int(getattr(stat_result, "st_size", 0) or 0)
                state["mtime"] = int(getattr(stat_result, "st_mtime", 0) or 0)
            except OSError:
                pass
            if pid_path:
                try:
                    stat_result = sftp.stat(pid_path)
                    state["pid_exists"] = True
                    with sftp.open(pid_path, "r") as fh:
                        state["remote_pid"] = str((fh.read() or "")).strip()
                    state["pid_size"] = int(getattr(stat_result, "st_size", 0) or 0)
                except OSError:
                    pass
        finally:
            sftp.close()
        return state

    def _runtime_probe(
        self,
        *,
        client: paramiko.SSHClient,
        session_key: str,
        working_dir: str | None,
        stage: str,
        started_at: str = "",
    ) -> dict[str, Any]:
        del started_at
        session = self._get_active_session(session_key)
        prompt_path = str(session.get("prompt_path") or "")
        pid_path = str(session.get("pid_path") or "")
        prompt_state = self._prompt_file_state(client=client, prompt_path=prompt_path, pid_path=pid_path) if prompt_path else {}
        remote_pid = str(prompt_state.get("remote_pid") or session.get("remote_pid") or "").strip()
        topk = max(1, int(getattr(bot_cfg, "RUNTIME_TRACE_PROCESS_SNAPSHOT_TOPK", 25) or 25))
        process_tree: list[dict[str, Any]] = []
        if self.remote_os == "windows":
            ps = (
                "$names=@('cmd.exe','powershell.exe','pwsh.exe','node.exe','codex.exe','python.exe'); "
                f"$wd={_ps_quote(str(working_dir or ''))}; "
                f"$pp={_ps_quote(prompt_path)}; "
                "Get-CimInstance Win32_Process | "
                "Where-Object { $names -contains $_.Name } | "
                "Select-Object -First "
                f"{topk} "
                "ProcessId,ParentProcessId,Name,CreationDate,CommandLine | ConvertTo-Json -Compress"
            )
            probe = self._run_command(
                client,
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                cwd=None,
                timeout=max(5, int(getattr(bot_cfg, "RUNTIME_TRACE_REMOTE_PROBE_TIMEOUT_SECONDS", 8) or 8)),
            )
            raw = str(probe.get("stdout") or "").strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        parsed = [parsed]
                    if isinstance(parsed, list):
                        for item in parsed:
                            if not isinstance(item, dict):
                                continue
                            process_tree.append(
                                {
                                    "pid": int(item.get("ProcessId") or 0),
                                    "ppid": int(item.get("ParentProcessId") or 0),
                                    "name": str(item.get("Name") or ""),
                                    "created": str(item.get("CreationDate") or ""),
                                    "command_line": str(item.get("CommandLine") or "")[:800],
                                }
                            )
                except Exception:
                    process_tree = []
        artifact_snapshot: list[dict[str, Any]] = []
        artifact_count = 0
        if working_dir:
            current_snapshot = self._snapshot_working_tree(client=client, working_dir=working_dir)
            before_snapshot = session.get("before_snapshot") if isinstance(session.get("before_snapshot"), dict) else {}
            changed = self._diff_snapshots(before_snapshot, current_snapshot)
            artifact_count = len(changed)
            max_files = max(1, int(getattr(bot_cfg, "RUNTIME_TRACE_ARTIFACT_SNAPSHOT_MAX_FILES", 120) or 120))
            for rel_path in changed[:max_files]:
                size, mtime = current_snapshot.get(rel_path, (0, 0))
                artifact_snapshot.append({"path": rel_path, "size": int(size), "mtime": int(mtime)})
            self._update_active_session(
                session_key,
                artifact_count=artifact_count,
                artifact_snapshot=artifact_snapshot,
                last_snapshot=current_snapshot,
            )
        if remote_pid:
            self._update_active_session(session_key, remote_pid=remote_pid)
        return {
            "session_key": session_key,
            "stage": stage,
            "prompt_file": prompt_state,
            "remote_pid": remote_pid,
            "process_tree": process_tree,
            "artifact_snapshot": artifact_snapshot,
            "artifact_count": artifact_count,
            "python_validation_processes": [row for row in process_tree if str(row.get("name") or "").lower() == "python.exe"],
        }

    def _cancel_runtime_session(
        self,
        *,
        client: paramiko.SSHClient,
        session_key: str,
    ) -> dict[str, Any]:
        session = self._get_active_session(session_key)
        if not session:
            return {"returncode": 1, "stdout": "", "stderr": f"No active session found for {session_key}"}
        prompt_path = str(session.get("prompt_path") or "")
        pid_path = str(session.get("pid_path") or "")
        prompt_state = self._prompt_file_state(client=client, prompt_path=prompt_path, pid_path=pid_path) if prompt_path else {}
        remote_pid = str(prompt_state.get("remote_pid") or session.get("remote_pid") or "").strip()
        outputs: list[str] = []
        rc = 0
        if remote_pid:
            kill = self._run_command(client, ["taskkill", "/PID", remote_pid, "/T", "/F"], cwd=None, timeout=30)
            rc = int(kill.get("returncode", 0) or 0)
            outputs.append(str(kill.get("stdout") or kill.get("stderr") or "").strip())
        elif prompt_path:
            ps = (
                f"$pp={_ps_quote(prompt_path)}; "
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -like ('*' + $pp + '*') } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId } | "
                "Out-String"
            )
            kill = self._run_command(
                client,
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                cwd=None,
                timeout=30,
            )
            rc = int(kill.get("returncode", 0) or 0)
            outputs.append(str(kill.get("stdout") or kill.get("stderr") or "").strip())
        probe = self._runtime_probe(
            client=client,
            session_key=session_key,
            working_dir=str(session.get("working_dir") or ""),
            stage=str(session.get("stage") or ""),
        )
        cleanup_status = "killed" if int(probe.get("artifact_count") or 0) >= 0 else "unknown"
        orphaned = bool(probe.get("prompt_file", {}).get("exists")) or bool(probe.get("prompt_file", {}).get("pid_exists"))
        if not orphaned:
            self._pop_active_session(session_key)
        else:
            self._update_active_session(session_key, status="orphaned_after_cancel")
        return {
            "returncode": int(rc),
            "stdout": "\n".join(line for line in outputs if line),
            "stderr": "" if rc == 0 else "Remote cancel reported a non-zero exit code.",
            "session_key": session_key,
            "cleanup_status": cleanup_status,
            "process_tree": probe.get("process_tree") or [],
            "prompt_file": probe.get("prompt_file") or {},
            "artifact_snapshot": probe.get("artifact_snapshot") or [],
            "artifact_count": int(probe.get("artifact_count") or 0),
            "remote_pid": str(probe.get("remote_pid") or ""),
            "orphaned": orphaned,
        }

    def _execute_sync(
        self,
        action: str,
        params: dict[str, Any],
        trace_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        prev_ctx = getattr(self._trace_local, "ctx", None)
        self._trace_local.ctx = dict(trace_ctx or {})
        try:
            slot_timeout = max(1, self.connect_timeout + 1)
            acquired = self._parallel_sem.acquire(timeout=slot_timeout)
            if not acquired:
                raise RuntimeError("SSH concurrency limit reached; try again shortly.")
            try:
                client = self._connect()
                try:
                    if action == "file_read":
                        return self._file_read(client, params)
                    if action == "file_write":
                        return self._file_write(client, params)
                    if action == "create_directory":
                        return self._create_directory(client, params)
                    if action == "list_directory":
                        return self._list_directory(client, params)
                    return self._run_command_action(client, action, params)
                finally:
                    client.close()
            finally:
                self._parallel_sem.release()
        finally:
            self._trace_local.ctx = prev_ctx

    def _build_command(
        self,
        args: list[str],
        cwd: str | None,
        env: dict[str, str] | None = None,
    ) -> str:

        if self.remote_os == "windows":
            return _build_windows_command(args, cwd=cwd, env=env)
        return _build_linux_command(args, cwd=cwd, env=env)

    def _require_str(self, params: dict[str, Any], key: str) -> str:

        value = params.get(key)
        if not value or not isinstance(value, str):
            raise ValueError(f"Missing required parameter: '{key}'")
        return value

    def _run_command(
        self,
        client: paramiko.SSHClient,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
        use_pty: bool = False,
    ) -> dict[str, Any]:

        command = self._build_command(args, cwd=cwd, env=env)
        preview = command_preview(command)
        cmd_hash = preview["command_hash"]
        trace_ctx = self._runtime_trace_context()
        trace_ctx["command_hash"] = cmd_hash
        trace_ctx["working_dir"] = str(cwd or "")
        emit_runtime_trace(
            "ssh.command.launch",
            status="start",
            details={
                "args_count": len(args or []),
                "timeout": int(timeout or self.command_timeout),
                "use_pty": bool(use_pty),
                "remote_os": self.remote_os,
                **preview,
            },
            **self._trace_fields(trace_ctx, phase="ssh_command", action_name=str(trace_ctx.get("action_name") or "exec_command")),
        )
        try:
            _, stdout, stderr = client.exec_command(
                command,
                timeout=timeout or self.command_timeout,
                get_pty=use_pty,
            )
            emit_runtime_trace(
                "ssh.command.launch",
                status="ok",
                details={"channel_ready": True, **preview},
                **self._trace_fields(trace_ctx, phase="ssh_command", command_hash=cmd_hash, working_dir=str(cwd or "")),
            )
            out, err, rc = self._read_channel_stream(
                channel=stdout.channel,
                trace_ctx=trace_ctx,
                cmd_hash=cmd_hash,
                cwd=cwd,
                timeout=int(timeout or self.command_timeout),
            )
            emit_runtime_trace(
                "ssh.command.exec",
                status="ok" if rc == 0 else "fail",
                error_code="SSH_COMMAND_NONZERO" if rc != 0 else "",
                error_message=err[:1200] if rc != 0 else "",
                details={"returncode": rc, "stdout_len": len(out), "stderr_len": len(err), **preview},
                **self._trace_fields(trace_ctx, phase="ssh_command", command_hash=cmd_hash, working_dir=str(cwd or "")),
            )
            return {"returncode": rc, "stdout": out, "stderr": err}
        except Exception as exc:
            emit_runtime_trace(
                "ssh.command.launch",
                status="fail",
                error_type=type(exc).__name__,
                error_code="SSH_COMMAND_EXEC_ERROR",
                error_message=str(exc)[:1200],
                details={"channel_ready": False, **preview},
                **self._trace_fields(trace_ctx, phase="ssh_command", command_hash=cmd_hash, working_dir=str(cwd or "")),
            )
            emit_runtime_trace(
                "ssh.command.exec",
                status="fail",
                error_type=type(exc).__name__,
                error_code="SSH_COMMAND_EXEC_ERROR",
                error_message=str(exc)[:1200],
                debug_bundle=build_debug_bundle(
                    failure_class="SSH_COMMAND_EXEC_ERROR",
                    error_message=str(exc),
                    causal_chain=["ssh.command.exec"],
                    mitigation_hint="Validate command syntax and SSH channel stability.",
                ),
                details={"args_count": len(args or []), "timeout": int(timeout or self.command_timeout), **preview},
                **self._trace_fields(trace_ctx, phase="ssh_command", command_hash=cmd_hash, working_dir=str(cwd or "")),
            )
            raise

    def _run_windows_command_with_prompt_file(
        self,
        *,
        client: paramiko.SSHClient,
        args_without_prompt: list[str],
        prompt: str,
        cwd: str | None,
        timeout: int,
        env: dict[str, str] | None = None,
        use_pty: bool = False,
        prompt_via_stdin: bool = False,
        session_key: str = "",
        before_snapshot: dict[str, tuple[int, int]] | None = None,
    ) -> dict[str, Any]:
        _sandbox_temp = bot_cfg.WORKER_PROJECTS_DIR or bot_cfg.get_str("SKYNET_DEFAULT_WORKING_DIR", "")
        temp_parent = cwd or _sandbox_temp or os.path.join(os.path.expanduser("~"), "skynet-temp")
        prompt_path = str(PureWindowsPath(temp_parent) / f".skynet_prompt_{uuid.uuid4().hex}.txt")
        pid_path = prompt_path + ".pid"
        trace_ctx = self._runtime_trace_context()
        active_session_key = str(session_key or trace_ctx.get("session_key") or uuid.uuid4().hex).strip()
        trace_ctx["session_key"] = active_session_key
        trace_ctx["working_dir"] = str(cwd or "")
        trace_ctx["stage"] = str(trace_ctx.get("stage") or "codex")
        self._register_active_session(
            active_session_key,
            prompt_path=prompt_path,
            pid_path=pid_path,
            working_dir=str(cwd or ""),
            stage=str(trace_ctx.get("stage") or ""),
            before_snapshot=dict(before_snapshot or {}),
            status="preparing",
        )
        if bool(getattr(bot_cfg, "RUNTIME_TRACE_ACTIVE_SESSION_REGISTRY", True)):
            emit_runtime_trace(
                "ssh.session.registered",
                status="ok",
                details={"prompt_file": prompt_path, "pid_file": pid_path},
                **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or "")),
            )

        sftp = client.open_sftp()
        try:
            if bool(getattr(bot_cfg, "RUNTIME_TRACE_PROMPT_FILE_EVENTS", True)):
                emit_runtime_trace(
                    "ssh.prompt_file.write",
                    status="start",
                    details={"prompt_file": prompt_path, "prompt_len": len(prompt or "")},
                    **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or "")),
                )
            self._sftp_makedirs(sftp, str(PureWindowsPath(prompt_path).parent))
            with sftp.open(prompt_path, "w") as fh:
                fh.write(prompt)
            if bool(getattr(bot_cfg, "RUNTIME_TRACE_PROMPT_FILE_EVENTS", True)):
                emit_runtime_trace(
                    "ssh.prompt_file.write",
                    status="ok",
                    details={"prompt_file": prompt_path, "prompt_len": len(prompt or "")},
                    **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or "")),
                )
        except Exception as exc:
            if bool(getattr(bot_cfg, "RUNTIME_TRACE_PROMPT_FILE_EVENTS", True)):
                emit_runtime_trace(
                    "ssh.prompt_file.write",
                    status="fail",
                    error_type=type(exc).__name__,
                    error_code="SSH_PROMPT_FILE_WRITE_ERROR",
                    error_message=str(exc)[:1200],
                    details={"prompt_file": prompt_path},
                    **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or "")),
                )
            try:
                sftp.close()
            except Exception:
                pass
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": f"Cannot write prompt file: {exc}",
            }
        finally:
            with contextlib.suppress(Exception):
                sftp.close()

        script_lines = [
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
        ]
        if cwd:
            script_lines.append(f"Set-Location -LiteralPath {_ps_quote(cwd)}")
        if env:
            for key, value in env.items():
                script_lines.append(f"$env:{key} = {_ps_quote(str(value))}")

        script_lines.append("$__args = @()")
        for arg in args_without_prompt:
            b64 = base64.b64encode(str(arg).encode("utf-8")).decode("ascii")
            script_lines.append(
                "$__args += [System.Text.Encoding]::UTF8.GetString("
                f"[System.Convert]::FromBase64String('{b64}'))"
            )
        script_lines.append(f"$__promptPath = {_ps_quote(prompt_path)}")
        script_lines.append(f"$__pidPath = {_ps_quote(pid_path)}")
        script_lines.append("$__cmd = $__args[0]")
        script_lines.append("$__rest = @()")
        script_lines.append("if ($__args.Length -gt 1) { $__rest = $__args[1..($__args.Length-1)] }")
        # Bypass .cmd/.bat wrappers: cmd.exe interprets |, &, <, > in
        # arguments and stdin as shell metacharacters, corrupting prompts
        # that contain them (e.g. "low|medium|high").  Resolving the npm
        # .cmd wrapper to the underlying node.exe + script.js lets us
        # call CreateProcess directly, avoiding cmd.exe entirely.
        script_lines.append(
            "if ($__cmd -match '\\.(cmd|bat)$' -and (Test-Path $__cmd)) {"
        )
        script_lines.append(
            "  $__body = Get-Content $__cmd -Raw -ErrorAction SilentlyContinue"
        )
        script_lines.append(
            "  if ($__body -match '\"?(%~dp0|%dp0%)[\\\\/ ]*([^\"\\r\\n]+\\.js)\"') {"
        )
        script_lines.append(
            "    $__dp0 = Split-Path -Parent $__cmd"
        )
        script_lines.append(
            "    $__script = Join-Path $__dp0 $Matches[2]"
        )
        script_lines.append(
            "    if (Test-Path $__script) {"
        )
        script_lines.append(
            "      $__rest = @($__script) + $__rest"
        )
        script_lines.append(
            "      $__cmd = 'node'"
        )
        script_lines.append("    }")
        script_lines.append("  }")
        script_lines.append("}")
        script_lines.append("Set-Content -LiteralPath $__pidPath -Value $PID -Encoding ascii")
        script_lines.append("$__prompt = Get-Content -LiteralPath $__promptPath -Raw -Encoding UTF8")
        if prompt_via_stdin:
            # Use System.Diagnostics.Process to pipe stdin properly.
            # Even after resolving .cmd wrappers, ProcessStartInfo gives
            # reliable stdin delivery vs PowerShell's pipe operator.
            script_lines.append("$__allArgs = ($__rest + @('-')) -join ' '")
            script_lines.append("$psi = [System.Diagnostics.ProcessStartInfo]::new()")
            script_lines.append("$psi.FileName = $__cmd")
            script_lines.append("$psi.Arguments = $__allArgs")
            script_lines.append("$psi.RedirectStandardInput = $true")
            script_lines.append("$psi.UseShellExecute = $false")
            script_lines.append("$psi.CreateNoWindow = $true")
            if cwd:
                script_lines.append(f"$psi.WorkingDirectory = {_ps_quote(cwd)}")
            if env:
                for key, value in env.items():
                    script_lines.append(
                        f"$psi.EnvironmentVariables[{_ps_quote(key)}] = {_ps_quote(str(value))}"
                    )
            script_lines.append("$proc = [System.Diagnostics.Process]::Start($psi)")
            script_lines.append("$proc.StandardInput.Write($__prompt)")
            script_lines.append("$proc.StandardInput.Close()")
            script_lines.append("$proc.WaitForExit()")
            script_lines.append("$code = $proc.ExitCode")
        else:
            script_lines.append("& $__cmd @__rest $__prompt")
            script_lines.append("$code = $LASTEXITCODE")
            script_lines.append("if ($null -eq $code) { $code = 0 }")
        script_lines.append("Remove-Item -LiteralPath $__promptPath -Force -ErrorAction SilentlyContinue")
        script_lines.append("Remove-Item -LiteralPath $__pidPath -Force -ErrorAction SilentlyContinue")
        script_lines.append("exit $code")

        script = "\n".join(script_lines)
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        command = (
            "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
            f"-EncodedCommand {encoded}"
        )
        preview = command_preview(command)
        try:
            emit_runtime_trace(
                "ssh.command.launch",
                status="start",
                details={"prompt_file": prompt_path, "pid_file": pid_path, **preview},
                **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or ""), command_hash=preview["command_hash"]),
            )
            _, stdout, _stderr = client.exec_command(
                command,
                timeout=timeout or self.command_timeout,
                get_pty=use_pty,
            )
            emit_runtime_trace(
                "ssh.command.launch",
                status="ok",
                details={"prompt_file": prompt_path, "pid_file": pid_path, **preview},
                **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or ""), command_hash=preview["command_hash"]),
            )
            self._update_active_session(active_session_key, status="running")
            out, err, rc = self._read_channel_stream(
                channel=stdout.channel,
                trace_ctx=trace_ctx,
                cmd_hash=preview["command_hash"],
                cwd=cwd,
                timeout=int(timeout or self.command_timeout),
            )
        except Exception as exc:
            self._update_active_session(active_session_key, status="orphaned", last_error=str(exc))
            emit_runtime_trace(
                "ssh.session.orphaned",
                status="fail",
                error_type=type(exc).__name__,
                error_code="SSH_SESSION_ORPHANED",
                error_message=str(exc)[:1200],
                details={"prompt_file": prompt_path, "pid_file": pid_path},
                **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or "")),
            )
            raise

        emit_runtime_trace(
            "ssh.prompt_file.cleanup",
            status="start",
            details={"prompt_file": prompt_path, "pid_file": pid_path},
            **self._trace_fields(trace_ctx, phase="ssh_command", session_key=active_session_key, working_dir=str(cwd or "")),
        )
        prompt_state = self._prompt_file_state(client=client, prompt_path=prompt_path, pid_path=pid_path)
        remote_pid = str(prompt_state.get("remote_pid") or "").strip()
        if remote_pid:
            trace_ctx["remote_pid"] = remote_pid
            self._update_active_session(active_session_key, remote_pid=remote_pid)
        cleanup_ok = not bool(prompt_state.get("exists")) and not bool(prompt_state.get("pid_exists"))
        emit_runtime_trace(
            "ssh.prompt_file.cleanup",
            status="ok" if cleanup_ok else "fail",
            error_code="" if cleanup_ok else "SSH_PROMPT_FILE_CLEANUP_PENDING",
            error_message="" if cleanup_ok else "Prompt wrapper cleanup did not complete.",
            details={"prompt_file_state": prompt_state},
            **self._trace_fields(
                trace_ctx,
                phase="ssh_command",
                session_key=active_session_key,
                working_dir=str(cwd or ""),
                remote_pid=remote_pid,
            ),
        )
        self._update_active_session(
            active_session_key,
            status="completed" if cleanup_ok else "orphaned",
            remote_pid=remote_pid,
            prompt_state=prompt_state,
            last_returncode=int(rc),
        )
        emit_runtime_trace(
            "ssh.session.completed" if cleanup_ok else "ssh.session.orphaned",
            status="ok" if cleanup_ok else "fail",
            error_code="" if cleanup_ok else "SSH_SESSION_ORPHANED",
            error_message="" if cleanup_ok else "Wrapper exited but prompt cleanup is still pending.",
            details={"prompt_file_state": prompt_state},
            **self._trace_fields(
                trace_ctx,
                phase="ssh_command",
                session_key=active_session_key,
                working_dir=str(cwd or ""),
                remote_pid=remote_pid,
            ),
        )
        if cleanup_ok:
            self._pop_active_session(active_session_key)
        return {"returncode": int(rc), "stdout": out, "stderr": err}

    def _persist_generated_files_from_blocks(
        self,
        *,
        client: paramiko.SSHClient,
        generated: str,
        working_dir: str | None,
    ) -> tuple[list[str], list[str]]:
        pattern = re.compile(r"```([^\n`]+)\r?\n(.*?)```", re.DOTALL)
        files_written: list[str] = []
        errors: list[str] = []

        # Language tag -> file extension for fallback naming.
        lang_ext = {
            "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
            "typescript": ".ts", "ts": ".ts", "java": ".java", "c": ".c",
            "cpp": ".cpp", "c++": ".cpp", "go": ".go", "rust": ".rs",
            "ruby": ".rb", "bash": ".sh", "sh": ".sh", "html": ".html",
            "css": ".css", "json": ".json", "yaml": ".yaml", "yml": ".yaml",
            "toml": ".toml", "sql": ".sql", "r": ".r", "swift": ".swift",
            "kotlin": ".kt", "dart": ".dart", "lua": ".lua", "perl": ".pl",
        }
        known_filenames = {
            "dockerfile",
            "makefile",
            "readme",
            "readme.md",
            "license",
            "license.txt",
            "requirements.txt",
            "pyproject.toml",
            "package.json",
            "skynet_run.json",
        }

        cwd_norm = _norm_remote_path(working_dir, self.remote_os) if working_dir else ""

        file_blocks: list[tuple[str, str]] = []
        lang_blocks: list[tuple[str, str]] = []
        for match in pattern.finditer(generated or ""):
            tag = match.group(1).strip()
            content = match.group(2)
            looks_like_file = (
                "." in tag
                or "/" in tag
                or "\\" in tag
                or tag.strip().lower() in known_filenames
            )
            if looks_like_file:
                file_blocks.append((tag, content))
            else:
                lang_blocks.append((tag, content))

        if not file_blocks and lang_blocks:
            for idx, (tag, content) in enumerate(lang_blocks):
                ext = lang_ext.get(tag.lower())
                if not ext:
                    continue
                fallback = f"main{ext}" if idx == 0 else f"file{idx}{ext}"
                file_blocks.append((fallback, content))

        # Avoid foo.py + foo/bar.py import shadowing.
        top_level_stems = set()
        for fn, _ in file_blocks:
            parts = fn.replace("\\", "/").split("/")
            if len(parts) == 1 and parts[0].endswith(".py"):
                top_level_stems.add(parts[0].rsplit(".", 1)[0])

        shadow_renames: dict[str, str] = {}
        for stem in top_level_stems:
            for fn, _ in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] == stem:
                    shadow_renames[stem] = "lib"
                    break

        if shadow_renames:
            fixed_blocks: list[tuple[str, str]] = []
            for fn, content in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] in shadow_renames:
                    parts[0] = shadow_renames[parts[0]]
                    fn = "/".join(parts)
                for old_name, new_name in shadow_renames.items():
                    content = content.replace(f"from {old_name}.", f"from {new_name}.")
                    content = content.replace(f"import {old_name}.", f"import {new_name}.")
                fixed_blocks.append((fn, content))
            file_blocks = fixed_blocks

        sftp = client.open_sftp()
        try:
            for filename, content in file_blocks:
                if cwd_norm and not os.path.isabs(filename):
                    if self.remote_os == "windows":
                        remote_path = cwd_norm.rstrip("\\") + "\\" + filename.replace("/", "\\")
                    else:
                        remote_path = cwd_norm.rstrip("/") + "/" + filename.lstrip("/")
                else:
                    remote_path = _norm_remote_path(filename, self.remote_os)

                try:
                    if self.remote_os == "windows":
                        parent = str(PureWindowsPath(remote_path).parent)
                    else:
                        parent = str(PurePosixPath(remote_path).parent)
                    self._sftp_makedirs(sftp, parent)
                    with sftp.open(remote_path, "w") as fh:
                        fh.write(content)
                    files_written.append(filename)
                except Exception as exc:
                    errors.append(f"{filename}: {exc}")
        finally:
            sftp.close()

        return files_written, errors

    def _snapshot_working_tree(
        self,
        *,
        client: paramiko.SSHClient,
        working_dir: str | None,
    ) -> dict[str, tuple[int, int]]:
        if not working_dir:
            return {}
        root = _norm_remote_path(working_dir, self.remote_os)
        snapshot: dict[str, tuple[int, int]] = {}
        max_entries = 5000
        max_depth = 10
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}

        sftp = client.open_sftp()
        try:
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack and len(snapshot) < max_entries:
                current, depth = stack.pop()
                try:
                    entries = sftp.listdir_attr(current)
                except OSError:
                    continue
                for entry in entries:
                    if len(snapshot) >= max_entries:
                        break
                    name = entry.filename
                    path = self._norm_join(current, name)
                    if stat.S_ISDIR(entry.st_mode):
                        if depth < max_depth and name not in skip_dirs:
                            stack.append((path, depth + 1))
                        continue
                    rel = self._relative_to_working_root(root, path)
                    if rel:
                        snapshot[rel] = (int(entry.st_size), int(getattr(entry, "st_mtime", 0) or 0))
        finally:
            sftp.close()
        return snapshot

    def _relative_to_working_root(self, root: str, path: str) -> str:
        root_norm = _norm_remote_path(root, self.remote_os).replace("\\", "/").rstrip("/")
        path_norm = _norm_remote_path(path, self.remote_os).replace("\\", "/")
        if path_norm == root_norm:
            return ""
        prefix = root_norm + "/"
        if path_norm.startswith(prefix):
            return path_norm[len(prefix):]
        return path_norm.rsplit("/", 1)[-1]

    @staticmethod
    def _diff_snapshots(
        before: dict[str, tuple[int, int]],
        after: dict[str, tuple[int, int]],
    ) -> list[str]:
        changed: list[str] = []
        for rel_path, meta in after.items():
            if before.get(rel_path) != meta:
                changed.append(rel_path)
        return sorted(changed)

    # ------------------------------------------------------------------
    # Ollama coding agent - EC2 calls Ollama on laptop via SSH, all
    # intelligence (prompt building, code parsing, file writing) stays on EC2.
    # ------------------------------------------------------------------

    @staticmethod
    def _ollama_system_prompt() -> str:
        return load_prompt("gateway/coding/system.md")

    def _run_ollama_coding_agent(
        self,
        client: paramiko.SSHClient,
        prompt: str,
        working_dir: str | None,
        model: str,
        ollama_url: str,
        timeout: int,
    ) -> dict[str, Any]:
        """
        1. Write a one-shot Python script to the laptop via SFTP.
        2. Run it via SSH exec - it calls the local Ollama API and prints the response.
        3. Parse the response on EC2 for fenced code blocks with file paths.
        4. Write each file to the laptop via SFTP.
        5. Return a summary.

        Laptop is a dumb executor: it runs Python standard library + Ollama.
        All logic lives here on EC2.
        """
        full_prompt = f"{self._ollama_system_prompt()}\nTask: {prompt}"
        b64_prompt  = base64.b64encode(full_prompt.encode("utf-8")).decode("ascii")
        b64_url     = base64.b64encode(ollama_url.encode("utf-8")).decode("ascii")
        b64_model   = base64.b64encode(model.encode("utf-8")).decode("ascii")

        # One-shot Python script - uses only stdlib (no pip installs needed).
        script_body = (
            "import base64, json, urllib.request, sys\n"
            f"prompt = base64.b64decode('{b64_prompt}').decode('utf-8')\n"
            f"url    = base64.b64decode('{b64_url}').decode('utf-8') + '/api/generate'\n"
            f"model  = base64.b64decode('{b64_model}').decode('utf-8')\n"
            f"options = {{'num_ctx': {int(bot_cfg.get_str('OLLAMA_NUM_CTX', '8192'))}, 'temperature': 0.2}}\n"
            "payload = json.dumps({'model': model, 'prompt': prompt, 'stream': False, 'options': options}).encode()\n"
            "req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})\n"
            "try:\n"
            "    resp = urllib.request.urlopen(req, timeout=600).read().decode()\n"
            "    print(json.loads(resp).get('response', ''))\n"
            "except Exception as e:\n"
            "    print(f'OLLAMA_ERROR: {e}', file=__import__('sys').stderr)\n"
            "    sys.exit(1)\n"
        )

        # Write the script to a temp file inside the sandbox temp folder.
        if self.remote_os == "windows":
            _win_temp = bot_cfg.WORKER_PROJECTS_DIR or bot_cfg.get_str("SKYNET_DEFAULT_WORKING_DIR", "")
            tmp_script = str(PureWindowsPath(_win_temp) / "_skynet_ollama_coder.py")
        else:
            tmp_script = "/tmp/_skynet_ollama_coder.py"

        sftp = client.open_sftp()
        try:
            # Ensure the parent directory exists (e.g. E:\SKYNET-SANDBOX\temp\)
            if self.remote_os == "windows":
                tmp_parent = str(PureWindowsPath(tmp_script).parent)
            else:
                tmp_parent = str(PurePosixPath(tmp_script).parent)
            self._sftp_makedirs(sftp, tmp_parent)
            with sftp.open(tmp_script, "w") as fh:
                fh.write(script_body)
        except Exception as exc:
            sftp.close()
            return {"returncode": 1, "stdout": "", "stderr": f"Cannot write temp script: {exc}"}

        # Run it - stdout is the Ollama-generated text.
        # Close SFTP first - long-running exec can invalidate the session.
        sftp.close()
        result = self._run_command(client, ["python", tmp_script], cwd=None, timeout=timeout)

        # Re-open SFTP for file operations after exec.
        sftp = client.open_sftp()

        # Clean up temp script (best effort).
        try:
            sftp.remove(tmp_script)
        except Exception:
            pass

        if result["returncode"] != 0:
            sftp.close()
            return result

        generated = result["stdout"]

        # Parse fenced code blocks: ```path/to/file.ext\n<content>\n```
        pattern = re.compile(r"```([^\n`]+)\n(.*?)```", re.DOTALL)
        files_written: list[str] = []
        errors: list[str] = []

        # Language tag -> file extension mapping for fallback naming.
        _LANG_EXT = {
            "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
            "typescript": ".ts", "ts": ".ts", "java": ".java", "c": ".c",
            "cpp": ".cpp", "c++": ".cpp", "go": ".go", "rust": ".rs",
            "ruby": ".rb", "bash": ".sh", "sh": ".sh", "html": ".html",
            "css": ".css", "json": ".json", "yaml": ".yaml", "yml": ".yaml",
            "toml": ".toml", "sql": ".sql", "r": ".r", "swift": ".swift",
            "kotlin": ".kt", "dart": ".dart", "lua": ".lua", "perl": ".pl",
        }

        cwd_norm = _norm_remote_path(working_dir, self.remote_os) if working_dir else ""

        # Collect all matches, separating file-path blocks from language-tag blocks.
        file_blocks: list[tuple[str, str]] = []
        lang_blocks: list[tuple[str, str]] = []
        for match in pattern.finditer(generated):
            tag = match.group(1).strip()
            content = match.group(2)
            if "." in tag or "/" in tag or "\\" in tag:
                file_blocks.append((tag, content))
            else:
                lang_blocks.append((tag, content))

        # If no file-path blocks found, convert language-tag blocks using fallback names.
        if not file_blocks and lang_blocks:
            for idx, (tag, content) in enumerate(lang_blocks):
                ext = _LANG_EXT.get(tag.lower(), f".{tag.lower()}")
                fallback = f"main{ext}" if idx == 0 else f"file{idx}{ext}"
                file_blocks.append((fallback, content))

        # -- Fix shadowing: if foo.py and foo/bar.py both exist, rename the
        # subdirectory to lib/ so Python doesn't treat foo.py as the package.
        top_level_stems = set()
        for fn, _ in file_blocks:
            parts = fn.replace("\\", "/").split("/")
            if len(parts) == 1 and parts[0].endswith(".py"):
                top_level_stems.add(parts[0].rsplit(".", 1)[0])

        # Map of conflicting stems that need renaming (e.g. "blakely" -> "lib").
        shadow_renames: dict[str, str] = {}
        for stem in top_level_stems:
            for fn, _ in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] == stem:
                    shadow_renames[stem] = "lib"
                    break

        if shadow_renames:
            fixed_blocks: list[tuple[str, str]] = []
            for fn, content in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] in shadow_renames:
                    parts[0] = shadow_renames[parts[0]]
                    fn = "/".join(parts)
                # Rewrite imports: from <old_pkg>.x import y -> from lib.x import y
                for old_name, new_name in shadow_renames.items():
                    content = content.replace(f"from {old_name}.", f"from {new_name}.")
                    content = content.replace(f"import {old_name}.", f"import {new_name}.")
                fixed_blocks.append((fn, content))
            file_blocks = fixed_blocks

        for filename, content in file_blocks:

            # Resolve to absolute path inside working_dir.
            if cwd_norm and not os.path.isabs(filename):
                if self.remote_os == "windows":
                    remote_path = cwd_norm.rstrip("\\") + "\\" + filename.replace("/", "\\")
                else:
                    remote_path = cwd_norm.rstrip("/") + "/" + filename.lstrip("/")
            else:
                remote_path = _norm_remote_path(filename, self.remote_os)

            # Ensure parent directory exists.
            try:
                if self.remote_os == "windows":
                    parent = str(PureWindowsPath(remote_path).parent)
                else:
                    parent = str(PurePosixPath(remote_path).parent)
                self._sftp_makedirs(sftp, parent)
                with sftp.open(remote_path, "w") as fh:
                    fh.write(content)
                files_written.append(filename)
            except Exception as exc:
                errors.append(f"{filename}: {exc}")

        sftp.close()

        if not files_written and not errors:
            return {
                "returncode": 1,
                "stdout": generated[:1000],
                "stderr": "Ollama responded but no code blocks with file paths were found.",
            }

        summary_parts = []
        if files_written:
            summary_parts.append(f"Wrote {len(files_written)} file(s): {', '.join(files_written)}")
        if errors:
            summary_parts.append(f"Errors: {'; '.join(errors)}")

        return {
            "returncode": 0 if files_written else 1,
            "stdout": "\n".join(summary_parts),
            "stderr": "",
            "files_written": files_written,
        }

    @staticmethod
    def _is_missing_command_output(text: str) -> bool:
        lowered = (text or "").lower()
        markers = (
            "is not recognized",
            "command not found",
            "no such file or directory",
            "cannot find the file specified",
            "not found",
        )
        return any(marker in lowered for marker in markers)

    def _run_python_snippet(
        self,
        client: paramiko.SSHClient,
        script: str,
        args: list[str],
        *,
        timeout: int = 60,
    ) -> dict[str, Any]:
        interpreters: list[list[str]] = [["python"]]
        if self.remote_os == "windows":
            interpreters.append(["py", "-3"])
        else:
            interpreters.append(["python3"])

        last_result: dict[str, Any] | None = None
        for prefix in interpreters:
            result = self._run_command(
                client,
                [*prefix, "-c", script, *args],
                cwd=None,
                timeout=timeout,
            )
            last_result = result
            if result.get("returncode", 1) == 0:
                return result
            combined = f"{result.get('stderr', '')}\n{result.get('stdout', '')}"
            if self._is_missing_command_output(combined):
                continue
            return result
        return last_result or {"returncode": 1, "stdout": "", "stderr": "Python runtime not found on remote host."}

    @staticmethod
    def _bool_param(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    @contextlib.contextmanager
    def _managed_remote_qwen_context_file(
        self,
        *,
        client: paramiko.SSHClient,
        cwd: str | None,
        context_text: str,
        enabled: bool,
    ) -> Any:
        text = str(context_text or "").strip()
        working_dir = str(cwd or "").strip()
        if not enabled or not text or not working_dir:
            yield None
            return

        context_path = _norm_remote_path(
            str(
                (PureWindowsPath(working_dir) / "QWEN.md")
                if self.remote_os == "windows"
                else (PurePosixPath(working_dir) / "QWEN.md")
            ),
            self.remote_os,
        )
        parent_dir = str(PureWindowsPath(context_path).parent) if self.remote_os == "windows" else str(PurePosixPath(context_path).parent)
        previous_text = ""
        had_existing = False
        sftp = client.open_sftp()
        try:
            self._sftp_makedirs(sftp, parent_dir)
            try:
                with sftp.open(context_path, "r") as handle:
                    existing = handle.read()
                if isinstance(existing, bytes):
                    previous_text = existing.decode("utf-8", errors="replace")
                else:
                    previous_text = str(existing)
                had_existing = True
            except OSError:
                had_existing = False
                previous_text = ""

            with sftp.open(context_path, "w") as handle:
                handle.write(text)

            yield context_path
        finally:
            try:
                if had_existing:
                    with sftp.open(context_path, "w") as handle:
                        handle.write(previous_text)
                else:
                    with contextlib.suppress(OSError):
                        sftp.remove(context_path)
            finally:
                with contextlib.suppress(Exception):
                    sftp.close()

    def _resolve_backend(self, *, agent: str, backend: str) -> tuple[str | None, str | None]:
        backend = (backend or "auto").strip().lower()
        if backend not in {"auto", "ollama", "native"}:
            return None, "backend must be one of: auto, ollama, native."
        if agent == "claude":
            if backend == "auto":
                return "ollama", None
            return backend, None
        if backend == "ollama":
            return None, f"backend=ollama is only supported for agent='claude'."
        if backend == "auto":
            return "native", None
        return backend, None

    def _ollama_check_model(
        self,
        client: paramiko.SSHClient,
        *,
        base_url: str,
        model: str,
    ) -> dict[str, Any]:
        script = (
            "import json, sys, urllib.request\n"
            "base=(sys.argv[1] if len(sys.argv)>1 else '').rstrip('/')\n"
            "model=sys.argv[2] if len(sys.argv)>2 else ''\n"
            "try:\n"
            "    resp=urllib.request.urlopen(base + '/api/tags', timeout=15)\n"
            "    data=json.loads(resp.read().decode('utf-8', errors='replace'))\n"
            "except Exception as exc:\n"
            "    print(f'CONNECT_ERROR:{exc}')\n"
            "    raise SystemExit(2)\n"
            "names=set()\n"
            "for item in (data.get('models') or []):\n"
            "    if isinstance(item, dict):\n"
            "        name=str(item.get('name') or '').strip()\n"
            "        if name:\n"
            "            names.add(name)\n"
            "if model in names:\n"
            "    print('MODEL_OK')\n"
            "    raise SystemExit(0)\n"
            "print('MODEL_MISSING')\n"
            "raise SystemExit(3)\n"
        )
        result = self._run_python_snippet(
            client,
            script,
            [base_url, model],
            timeout=90,
        )
        rc = int(result.get("returncode", 1))
        out = str(result.get("stdout") or "").strip()
        err = str(result.get("stderr") or "").strip()
        if rc == 0:
            return {"reachable": True, "present": True, "summary": "model present"}
        if rc == 3:
            return {"reachable": True, "present": False, "summary": "model missing"}
        if rc == 2:
            return {"reachable": False, "present": False, "summary": out or err or "cannot reach Ollama API"}
        return {"reachable": False, "present": False, "summary": err or out or "ollama preflight failed"}

    def _ollama_pull_model(
        self,
        client: paramiko.SSHClient,
        *,
        model: str,
        timeout: int,
    ) -> dict[str, Any]:
        pull_timeout = max(120, min(timeout, 1800))
        return self._run_command(
            client,
            ["ollama", "pull", model],
            cwd=None,
            timeout=pull_timeout,
        )

    def _ollama_context_warning(
        self,
        client: paramiko.SSHClient,
        *,
        model: str,
    ) -> str:
        show = self._run_command(client, ["ollama", "show", model], cwd=None, timeout=45)
        if int(show.get("returncode", 1)) != 0:
            return ""
        text = f"{show.get('stdout', '')}\n{show.get('stderr', '')}"
        patterns = (
            r"context length[^0-9]*([0-9][0-9,]*)",
            r"num_ctx[^0-9]*([0-9][0-9,]*)",
        )
        context_value: int | None = None
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            raw = match.group(1).replace(",", "")
            try:
                context_value = int(raw)
            except ValueError:
                context_value = None
            if context_value is not None:
                break
        if context_value is None:
            return ""
        if context_value >= max(1, int(bot_cfg.CLAUDE_OLLAMA_MIN_CONTEXT)):
            return ""
        return (
            f"Warning: detected model context {context_value} tokens, below "
            f"target {int(bot_cfg.CLAUDE_OLLAMA_MIN_CONTEXT)}."
        )

    def _build_codex_command_base_args(self, *, binary: str) -> list[str]:
        args = [binary, *self._coding_prefix["codex"], "--skip-git-repo-check"]
        if self._codex_write_mode == "danger_full_access":
            args.append("--dangerously-bypass-approvals-and-sandbox")
        elif self._codex_write_mode == "workspace_write":
            args.extend(["--sandbox", "workspace-write"])
        elif self._codex_write_mode == "read_only":
            args.extend(["--sandbox", "read-only"])
        return args

    def _build_codex_command_args(self, *, binary: str, prompt: str) -> list[str]:
        args = self._build_codex_command_base_args(binary=binary)
        args.append(prompt)
        return args

    def _codex_write_blocked(self, run: dict[str, Any]) -> bool:
        text = f"{run.get('stdout', '')}\n{run.get('stderr', '')}".lower()
        patterns = (
            "sandbox: read-only",
            "read-only sandbox",
            "cannot write",
            "can't write",
            "unable to write",
            "approval policy is never",
            "must ask for approval",
        )
        return any(pattern in text for pattern in patterns)

    def _run_coding_agent_native(
        self,
        *,
        client: paramiko.SSHClient,
        agent: str,
        prompt: str,
        cwd: str | None,
        timeout: int,
        model: str,
        task_mode: str = "",
        qwen_context_text: str = "",
        reply_contract: str = "",
        planner_state: dict[str, Any] | None = None,
        requirement_summary_md: str = "",
    ) -> dict[str, Any]:
        binary = self._coding_bins[agent]
        if self.remote_os == "windows":
            binary, available = self._resolve_windows_binary(client, binary)
            if not available:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"'{agent}' CLI is not installed or not on PATH. "
                        f"Expected binary: {self._coding_bins[agent]}"
                    ),
                }

        before_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        run: dict[str, Any]

        if agent == "claude":
            if self.remote_os == "windows":
                args = self._build_claude_command_args(
                    binary=binary,
                    prompt=None,
                    model=model,
                )
                run = self._run_windows_command_with_prompt_file(
                    client=client,
                    args_without_prompt=args,
                    prompt=prompt,
                    cwd=cwd,
                    timeout=timeout,
                    use_pty=True,
                )
            else:
                args = self._build_claude_command_args(
                    binary=binary,
                    prompt=prompt,
                    model=model,
                )
                # Claude CLI can block indefinitely on SSH non-PTY channels.
                run = self._run_command(client, args, cwd=cwd, timeout=timeout, use_pty=True)
        else:
            if agent == "codex":
                args = self._build_codex_command_base_args(binary=binary)
                trace_ctx = self._runtime_trace_context()
                session_key = str(trace_ctx.get("session_key") or uuid.uuid4().hex).strip()
                trace_ctx["session_key"] = session_key
                emit_runtime_trace(
                    "coding.prompt.transport",
                    status="start",
                    **self._trace_fields(
                        trace_ctx,
                        phase="coding_agent",
                        stage="codex",
                        action_name=str(trace_ctx.get("action_name") or "run_coding_agent"),
                        command_hash=command_hash(prompt),
                        working_dir=str(cwd or ""),
                        session_key=session_key,
                    ),
                    details={
                        "remote_os": str(self.remote_os or ""),
                        "prompt_len": len(prompt or ""),
                        "prompt_newlines": int((prompt or "").count("\n")),
                        "delivery": "stdin" if self.remote_os == "windows" else "argv",
                    },
                )
                if self.remote_os == "windows":
                    initial = self._run_windows_command_with_prompt_file(
                        client=client,
                        args_without_prompt=args,
                        prompt=prompt,
                        cwd=cwd,
                        timeout=timeout,
                        prompt_via_stdin=True,
                        session_key=session_key,
                        before_snapshot=before_snapshot,
                    )
                else:
                    args.append(prompt)
                    initial = self._run_command(client, args, cwd=cwd, timeout=timeout)
            elif agent == "qwen":
                try:
                    qwen_task_mode = normalize_qwen_task_mode(task_mode)
                except ValueError as exc:
                    return {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": str(exc),
                    }
                args, stdin_prompt = build_qwen_command_args(
                    binary=binary,
                    prompt=prompt,
                    session_id=uuid.uuid4().hex,
                    task_mode=qwen_task_mode,
                    policy=self._qwen_policy,
                )
                profile = qwen_profile_for_task_mode(self._qwen_policy, qwen_task_mode)
                with self._managed_remote_qwen_context_file(
                    client=client,
                    cwd=cwd,
                    context_text=qwen_context_text,
                    enabled=bool(profile.get("use_context_file", False)) or bool(qwen_context_text),
                ):
                    if self.remote_os == "windows":
                        # Always pipe the prompt via stdin on Windows for
                        # qwen to avoid the 8191-char CLI limit that
                        # PowerShell hits when expanding $__prompt into
                        # a process command line.
                        effective_prompt = stdin_prompt or args[-1]
                        args_no_prompt = args if stdin_prompt else args[:-1]
                        initial = self._run_windows_command_with_prompt_file(
                            client=client,
                            args_without_prompt=args_no_prompt,
                            prompt=effective_prompt,
                            cwd=cwd,
                            timeout=timeout,
                            prompt_via_stdin=True,
                        )
                    else:
                        initial = self._run_command(client, args, cwd=cwd, timeout=timeout)
            else:
                # Cline agent â€” use file-based prompt on Windows to avoid
                # exceeding the 8191-char command-line limit.
                if self.remote_os == "windows":
                    args_no_prompt = [binary, *self._coding_prefix[agent]]
                    initial = self._run_windows_command_with_prompt_file(
                        client=client,
                        args_without_prompt=args_no_prompt,
                        prompt=prompt,
                        cwd=cwd,
                        timeout=timeout,
                    )
                else:
                    args = [binary, *self._coding_prefix[agent], prompt]
                    initial = self._run_command(client, args, cwd=cwd, timeout=timeout)
            if agent == "qwen":
                run = normalize_qwen_result(
                    initial,
                    task_mode=qwen_task_mode,
                    policy=self._qwen_policy,
                    working_dir=cwd,
                    reply_contract=reply_contract,
                    planner_state=planner_state,
                    requirement_summary_md=requirement_summary_md,
                )
            elif agent != "cline":
                run = initial
            elif initial["returncode"] == 0:
                run = initial
            elif not self._cline_auto_switch:
                run = initial
            elif not self._is_retryable_cline_failure(initial):
                run = initial
            else:
                run = self._run_cline_with_auto_switch(
                    client=client,
                    binary=binary,
                    prompt=prompt,
                    cwd=cwd if isinstance(cwd, str) else None,
                    timeout=timeout,
                    initial_result=initial,
                )

        if int(run.get("returncode", 1)) != 0:
            return run

        parsed_written: list[str] = []
        parse_errors: list[str] = []
        if agent in {"claude", "codex"}:
            generated = str(run.get("stdout") or "")
            parsed_written, parse_errors = self._persist_generated_files_from_blocks(
                client=client,
                generated=generated,
                working_dir=cwd,
            )
        after_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        changed_written = self._diff_snapshots(before_snapshot, after_snapshot)

        combined: list[str] = []
        for path in [*parsed_written, *changed_written]:
            if path not in combined:
                combined.append(path)
        if combined:
            run["files_written"] = combined
            if agent == "codex":
                session_key = str(self._runtime_trace_context().get("session_key") or "").strip()
                if session_key:
                    self._update_active_session(
                        session_key,
                        artifact_count=len(combined),
                        artifact_snapshot=[{"path": path} for path in combined[:20]],
                    )
        if agent == "codex" and not combined and self._codex_write_blocked(run):
            detail = str(run.get("stderr") or run.get("stdout") or "").strip()
            run["returncode"] = 1
            run["stderr"] = f"CODEX_WRITE_BLOCKED: {detail[:700]}".strip()
        if parse_errors:
            warning = "; ".join(parse_errors)[:1200]
            current_err = str(run.get("stderr") or "").strip()
            run["stderr"] = f"{current_err}\nFILE_WRITE_WARNINGS: {warning}".strip()
        return run

    def _run_coding_agent_claude_ollama(
        self,
        *,
        client: paramiko.SSHClient,
        prompt: str,
        cwd: str | None,
        timeout: int,
        model: str,
        base_url: str,
        auto_pull_model: bool,
    ) -> dict[str, Any]:
        binary = self._coding_bins["claude"]
        if self.remote_os == "windows":
            binary, available = self._resolve_windows_binary(client, binary)
            if not available:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        "'claude' CLI is not installed or not on PATH. "
                        f"Expected binary: {self._coding_bins['claude']}"
                    ),
                }

        before_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        check = self._ollama_check_model(client, base_url=base_url, model=model)
        if not check["reachable"]:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": f"OLLAMA_SETUP_ERROR: {check['summary']}",
            }

        if not check["present"]:
            if not auto_pull_model:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"OLLAMA_MODEL_MISSING: '{model}' not found. "
                        "Set auto_pull_model=true or pre-pull the model."
                    ),
                }
            pull = self._ollama_pull_model(client, model=model, timeout=timeout)
            if int(pull.get("returncode", 1)) != 0:
                reason = str(pull.get("stderr") or pull.get("stdout") or "").strip()
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"OLLAMA_MODEL_SETUP_ERROR: pull failed for '{model}'. {reason}",
                }
            check_after = self._ollama_check_model(client, base_url=base_url, model=model)
            if not check_after["reachable"]:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"OLLAMA_SETUP_ERROR: {check_after['summary']}",
                }
            if not check_after["present"]:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"OLLAMA_MODEL_SETUP_ERROR: '{model}' still missing after pull."
                    ),
                }

        enforced_prompt = f"{self._ollama_system_prompt()}\nTask:\n{prompt}"
        args = self._build_claude_command_args(
            binary=binary,
            prompt=enforced_prompt,
            model=model,
        )
        env = {
            "ANTHROPIC_AUTH_TOKEN": bot_cfg.CLAUDE_OLLAMA_AUTH_TOKEN or "ollama",
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_BASE_URL": base_url,
        }
        # Claude-over-Ollama requires PTY on this SSH path to avoid hangs.
        run = self._run_command(
            client,
            args,
            cwd=cwd,
            timeout=timeout,
            env=env,
            use_pty=True,
        )
        warning = self._ollama_context_warning(client, model=model)
        if warning:
            current_out = str(run.get("stdout") or "").strip()
            run["stdout"] = f"{warning}\n{current_out}".strip()
        if int(run.get("returncode", 1)) != 0:
            return run

        generated = str(run.get("stdout") or "")
        parsed_written, parse_errors = self._persist_generated_files_from_blocks(
            client=client,
            generated=generated,
            working_dir=cwd,
        )
        after_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        changed_written = self._diff_snapshots(before_snapshot, after_snapshot)
        combined: list[str] = []
        for path in [*parsed_written, *changed_written]:
            if path not in combined:
                combined.append(path)
        if combined:
            run["files_written"] = combined
        if parse_errors:
            warning = "; ".join(parse_errors)[:1200]
            current_err = str(run.get("stderr") or "").strip()
            run["stderr"] = f"{current_err}\nFILE_WRITE_WARNINGS: {warning}".strip()
        return run

    def _build_claude_command_args(
        self,
        *,
        binary: str,
        prompt: str | None,
        model: str = "",
    ) -> list[str]:
        args = [binary]
        if model:
            args.extend(["--model", model])
        if self._claude_permission_mode:
            args.extend(["--permission-mode", self._claude_permission_mode])
        if self._claude_disable_slash_commands:
            args.append("--disable-slash-commands")
        if self._claude_dangerously_skip_permissions:
            args.append("--dangerously-skip-permissions")
        if prompt is not None:
            args.extend(["-p", prompt])
        return args

    def _run_command_action(self, client: paramiko.SSHClient, action: str, params: dict[str, Any]) -> dict[str, Any]:

        if action == "git_status":
            cwd = self._require_str(params, "working_dir")
            self._ensure_git_safe_directory(client, cwd)
            return self._run_command(client, ["git", "status", "--porcelain"], cwd=cwd)

        if action == "run_tests":
            cwd = self._require_str(params, "working_dir")
            runner = params.get("runner", "pytest")
            if runner == "pytest":
                result = self._run_command(client, ["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
                if int(result.get("returncode", 1)) != 0 and _python_module_missing(result, "pytest"):
                    install = self._run_command(
                        client,
                        ["python", "-m", "pip", "install", "pytest"],
                        cwd=cwd,
                        timeout=300,
                    )
                    if int(install.get("returncode", 1)) != 0:
                        detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                        base_err = str(result.get("stderr") or "").strip()
                        result["stderr"] = f"{base_err}\nPYTEST_SETUP_ERROR: {detail}".strip()
                        return result
                    retry = self._run_command(client, ["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
                    retry["stdout"] = f"Auto-installed pytest.\n{retry.get('stdout', '')}".strip()
                    return retry
                return result
            if runner == "npm":
                return self._run_command(client, ["npm", "test"], cwd=cwd)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown runner: {runner}"}

        if action == "lint_project":
            cwd = self._require_str(params, "working_dir")
            linter = params.get("linter", "ruff")
            if linter == "ruff":
                result = self._run_command(client, ["python", "-m", "ruff", "check", "."], cwd=cwd)
                if int(result.get("returncode", 1)) != 0 and _python_module_missing(result, "ruff"):
                    install = self._run_command(
                        client,
                        ["python", "-m", "pip", "install", "ruff"],
                        cwd=cwd,
                        timeout=300,
                    )
                    if int(install.get("returncode", 1)) != 0:
                        detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                        base_err = str(result.get("stderr") or "").strip()
                        result["stderr"] = f"{base_err}\nRUFF_SETUP_ERROR: {detail}".strip()
                        return result
                    retry = self._run_command(client, ["python", "-m", "ruff", "check", "."], cwd=cwd)
                    retry["stdout"] = f"Auto-installed ruff.\n{retry.get('stdout', '')}".strip()
                    return retry
                return result
            if linter == "eslint":
                return self._run_command(client, ["npx", "eslint", "."], cwd=cwd)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown linter: {linter}"}

        if action == "build_project":
            cwd = self._require_str(params, "working_dir")
            tool = params.get("build_tool", "npm")
            if tool == "npm":
                return self._run_command(client, ["npm", "run", "build"], cwd=cwd)
            if tool == "python":
                return self._run_command(client, ["python", "-m", "build"], cwd=cwd)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown build tool: {tool}"}

        if action == "install_dependencies":
            cwd = self._require_str(params, "working_dir")
            manager = params.get("manager", "pip")
            if manager == "pip":
                req_file = self._norm_join(cwd, "requirements.txt")
                return self._run_command(
                    client, ["python", "-m", "pip", "install", "-r", req_file], cwd=cwd, timeout=300,
                )
            if manager == "npm":
                return self._run_command(client, ["npm", "install"], cwd=cwd, timeout=300)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown manager: {manager}"}

        if action == "git_init":
            cwd = self._require_str(params, "working_dir")
            self._ensure_git_safe_directory(client, cwd)
            result = self._run_command(client, ["git", "init"], cwd=cwd)
            if result["returncode"] == 0:
                _ = self._run_command(client, ["git", "checkout", "-b", "main"], cwd=cwd)
            return result

        if action == "git_add_all":
            cwd = self._require_str(params, "working_dir")
            self._ensure_git_safe_directory(client, cwd)
            return self._run_command(client, ["git", "add", "-A"], cwd=cwd)

        if action == "git_commit":
            cwd = self._require_str(params, "working_dir")
            message = self._require_str(params, "message")
            self._ensure_git_safe_directory(client, cwd)
            stage = self._run_command(client, ["git", "add", "-u"], cwd=cwd)
            if stage["returncode"] != 0:
                return stage
            return self._run_command(client, ["git", "commit", "-m", message], cwd=cwd)

        if action == "git_push":
            cwd = self._require_str(params, "working_dir")
            remote = str(params.get("remote", "origin"))
            branch = str(params.get("branch", "main"))
            self._ensure_git_safe_directory(client, cwd)
            return self._run_command(client, ["git", "push", "-u", remote, branch], cwd=cwd)

        if action == "gh_create_repo":
            cwd = self._require_str(params, "working_dir")
            repo_name = self._require_str(params, "repo_name")
            description = str(params.get("description") or "")
            private = params.get("private", False) is True
            if not re.match(r"^[a-zA-Z0-9._-]+$", repo_name):
                return {"returncode": 1, "stdout": "", "stderr": "Invalid repo name characters."}
            if self.remote_os == "windows":
                exists = self._run_command(client, ["where", "gh"], cwd=None)
                if exists.get("returncode", 1) != 0:
                    return {
                        "returncode": 127,
                        "stdout": "",
                        "stderr": "GitHub CLI (gh) is not installed on the worker laptop.",
                    }
            visibility = "--private" if private else "--public"
            args = ["gh", "repo", "create", repo_name, visibility, "--source=.", "--push"]
            if description:
                args.extend(["--description", description])
            return self._run_command(client, args, cwd=cwd, timeout=120)

        if action == "open_in_vscode":
            path = self._require_str(params, "path")
            return self._run_command(client, ["code", path], cwd=None)

        if action == "check_coding_agents":
            if self.remote_os == "windows":
                lines = []
                for name, binary in self._coding_bins.items():
                    resolved_bin, available = self._resolve_windows_binary(client, binary)
                    if available:
                        lines.append(f"{name}: available ({resolved_bin})")
                    else:
                        lines.append(f"{name}: unavailable (expected binary: {binary})")
                out = "\n".join(lines)
                err = ""
                rc = 0
                return {"returncode": int(rc), "stdout": out, "stderr": err}
            # Linux fallback
            lines = []
            for name, binary in self._coding_bins.items():
                r = self._run_command(client, ["bash", "-lc", f"command -v {binary} || true"], cwd=None)
                if r["stdout"].strip():
                    lines.append(f"{name}: available ({r['stdout'].strip()})")
                else:
                    lines.append(f"{name}: unavailable (expected binary: {binary})")
            return {"returncode": 0, "stdout": "\n".join(lines), "stderr": ""}

        if action == "configure_coding_agent":
            agent = self._require_str(params, "agent").strip().lower()
            provider = self._require_str(params, "provider").strip().lower()
            model = str(params.get("model") or "").strip()
            base_url = str(params.get("base_url") or "").strip()

            if agent != "cline":
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "Only 'cline' is supported for provider switching right now.",
                }

            api_key = str(params.get("api_key") or "").strip()
            if not api_key:
                api_key = self._default_api_key_for_provider(provider)
            if not api_key:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"No API key available for provider '{provider}'. "
                        "Pass api_key explicitly or configure matching environment key."
                    ),
                }

            if not model:
                model = self._default_model_for_provider(provider)

            binary = self._coding_bins.get("cline", "cline")
            if self.remote_os == "windows":
                binary, available = self._resolve_windows_binary(client, binary)
                if not available:
                    return {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "Cline CLI is not installed or not on PATH.",
                    }

            configured = self._configure_cline_provider(
                client=client,
                binary=binary,
                provider=provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
            )
            if configured["returncode"] != 0:
                return configured

            verify = self._run_command(client, [binary, "--version"], cwd=None, timeout=30)
            if verify["returncode"] == 0:
                configured["stdout"] = (
                    f"Cline configured to provider '{provider}'"
                    + (f" (model: {model})" if model else "")
                )
            else:
                configured["stdout"] = (
                    f"Cline auth command succeeded for provider '{provider}'"
                    + (f" (model: {model})" if model else "")
                )
            configured["stderr"] = ""
            return configured

        if action == "run_coding_agent":
            agent = self._require_str(params, "agent").strip().lower()
            prompt = self._require_str(params, "prompt")
            cwd = params.get("working_dir")
            worker_id = str(params.get("worker_id") or "").strip()
            timeout = params.get("timeout_seconds", 1800)
            backend_raw = str(params.get("backend") or "auto").strip().lower()
            model = str(params.get("model") or "").strip()
            task_mode = str(params.get("task_mode") or "").strip().lower()
            qwen_context_text = str(params.get("qwen_context_text") or "").strip()
            reply_contract = str(params.get("reply_contract") or "").strip().lower()
            planner_state = params.get("planner_state_json") if isinstance(params.get("planner_state_json"), dict) else {}
            requirement_summary_md = str(params.get("requirement_summary_md") or "").strip()
            base_url = str(
                params.get("base_url")
                or bot_cfg.CLAUDE_OLLAMA_BASE_URL
            ).strip().rstrip("/")
            auto_pull_model = self._bool_param(
                params.get("auto_pull_model"),
                bool(bot_cfg.CLAUDE_OLLAMA_AUTO_PULL),
            )

            if agent not in self._coding_bins:
                allowed = ", ".join(sorted(self._coding_bins.keys()))
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"Unknown coding agent '{agent}'. Allowed: {allowed}",
                }
            if cwd is not None and not isinstance(cwd, str):
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "working_dir must be a string path.",
                }
            if not isinstance(timeout, int) or timeout < 30 or timeout > 3600:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "timeout_seconds must be an integer between 30 and 3600.",
                }
            if agent == "qwen":
                try:
                    task_mode = normalize_qwen_task_mode(task_mode)
                except ValueError as exc:
                    return {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": str(exc),
                    }

            resolved_backend, backend_error = self._resolve_backend(
                agent=agent,
                backend=backend_raw,
            )
            if backend_error:
                return {"returncode": 1, "stdout": "", "stderr": backend_error}

            if agent == "claude" and resolved_backend == "ollama":
                run = self._run_coding_agent_claude_ollama(
                    client=client,
                    prompt=prompt,
                    cwd=cwd if isinstance(cwd, str) else None,
                    timeout=timeout,
                    model=model or bot_cfg.CLAUDE_OLLAMA_DEFAULT_MODEL,
                    base_url=base_url or bot_cfg.CLAUDE_OLLAMA_BASE_URL,
                    auto_pull_model=auto_pull_model,
                )
                if worker_id:
                    run["worker_id"] = worker_id
                return run

            run = self._run_coding_agent_native(
                client=client,
                agent=agent,
                prompt=prompt,
                cwd=cwd if isinstance(cwd, str) else None,
                timeout=timeout,
                model=model,
                task_mode=task_mode,
                qwen_context_text=qwen_context_text,
                reply_contract=reply_contract,
                planner_state=planner_state,
                requirement_summary_md=requirement_summary_md,
            )
            if worker_id:
                run["worker_id"] = worker_id
            return run

        if action == "docker_build":
            cwd = self._require_str(params, "working_dir")
            tag = str(params.get("tag", "chathan-build:latest"))
            if not re.match(r"^[a-zA-Z0-9._/:@-]+$", tag):
                return {"returncode": 1, "stdout": "", "stderr": "Invalid Docker tag characters."}
            return self._run_command(client, ["docker", "build", "-t", tag, "."], cwd=cwd, timeout=600)

        if action == "docker_compose_up":
            cwd = self._require_str(params, "working_dir")
            return self._run_command(client, ["docker", "compose", "up", "-d"], cwd=cwd, timeout=300)

        if action == "close_app":
            app_name = self._require_str(params, "app").lower()
            exe = self._closeable_apps.get(app_name)
            if not exe:
                allowed = ", ".join(sorted(self._closeable_apps.keys()))
                return {"returncode": 1, "stdout": "", "stderr": f"'{app_name}' is not in the allowed list. Allowed: {allowed}"}
            if self.remote_os == "windows":
                return self._run_command(client, ["taskkill", "/F", "/IM", exe], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": "close_app currently supports Windows remote hosts only."}

        if action == "exec_command":
            cwd     = self._require_str(params, "working_dir")
            command = self._require_str(params, "command")
            parts   = command.strip().split()
            if not parts:
                return {"returncode": 1, "stdout": "", "stderr": "Empty command."}
            interpreter = parts[0].lower().rstrip(".exe")
            if interpreter not in ("python", "python3", "node"):
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"Interpreter {parts[0]!r} is not allowed. "
                        "Use python, python3, or node."
                    ),
                }
            remote_cwd = _norm_remote_path(cwd, self.remote_os)
            return self._run_command(client, parts, cwd=remote_cwd)

        if action == "run_tool_command":
            cwd = self._require_str(params, "working_dir")
            command = self._require_str(params, "command")
            worker_id = str(params.get("worker_id") or "").strip()
            parts = self._parse_tool_command(command)
            if not parts:
                return {"returncode": 1, "stdout": "", "stderr": "Empty tool command."}
            ok, reason = self._allow_tool_command(parts)
            if not ok:
                return {"returncode": 1, "stdout": "", "stderr": f"Command not allowed: {reason}"}
            timeout = int(params.get("timeout_seconds") or 600)
            remote_cwd = _norm_remote_path(cwd, self.remote_os)
            run = self._run_command(client, parts, cwd=remote_cwd, timeout=max(30, min(timeout, 3600)))
            if worker_id:
                run["worker_id"] = worker_id
            return run

        if action == "trace_runtime_probe":
            session_key = self._require_str(params, "session_key")
            working_dir = str(params.get("working_dir") or "").strip() or None
            stage = str(params.get("stage") or "").strip()
            probe = self._runtime_probe(
                client=client,
                session_key=session_key,
                working_dir=working_dir,
                stage=stage,
                started_at=str(params.get("started_at") or ""),
            )
            return {
                "returncode": 0,
                "stdout": json.dumps(probe, ensure_ascii=True),
                "stderr": "",
                **probe,
            }

        if action == "cancel_runtime_session":
            session_key = self._require_str(params, "session_key")
            return self._cancel_runtime_session(client=client, session_key=session_key)

        return {"returncode": 1, "stdout": "", "stderr": f"Action '{action}' is not supported in SSH tunnel mode."}

    @staticmethod
    def _parse_tool_command(command: str) -> list[str]:
        raw = str(command or "").strip()
        if not raw:
            return []
        if any(token in raw for token in ("&&", "||", ";", "|", ">", "<")):
            return []
        try:
            return shlex.split(raw, posix=False)
        except Exception:
            return raw.split()

    @staticmethod
    def _allow_tool_command(parts: list[str]) -> tuple[bool, str]:
        if not parts:
            return False, "empty"
        p0 = parts[0].lower()
        p1 = parts[1].lower() if len(parts) > 1 else ""
        p2 = parts[2].lower() if len(parts) > 2 else ""

        if p0 in {"pytest"}:
            return True, ""
        if p0 in {"python", "python3"} and p1 == "-m" and p2 == "pytest":
            return True, ""
        if p0 == "npm" and p1 == "test":
            return True, ""
        if p0 == "npm" and p1 == "run" and p2 == "build":
            return True, ""
        if p0 in {"pip", "pip3"} and p1 == "install" and len(parts) >= 4 and parts[2] == "-r":
            return True, ""
        if p0 == "python" and p1 == "-m" and p2 == "pip":
            if len(parts) >= 6 and parts[3] == "install" and parts[4] == "-r":
                return True, ""
            return False, "python -m pip only allows install -r"
        if p0 == "docker":
            if p1 == "build":
                return True, ""
            if p1 == "compose":
                return True, ""
            return False, "docker allows only build/compose"
        if p0 == "terraform" and p1 in {"validate", "plan"}:
            return True, ""
        return False, "not in allowlist"

    def _ensure_git_safe_directory(self, client: paramiko.SSHClient, cwd: str) -> None:
        """
        Avoid Git's "dubious ownership" protection on Windows SSH-created folders.
        """
        safe_path = _norm_remote_path(cwd, self.remote_os)
        check = self._run_command(
            client,
            ["git", "config", "--global", "--get-all", "safe.directory"],
            cwd=None,
            timeout=30,
        )
        if check["returncode"] == 0:
            lines = [
                line.strip().lower()
                for line in (check.get("stdout") or "").splitlines()
                if line.strip()
            ]
            if safe_path.lower() in lines:
                return

        _ = self._run_command(
            client,
            ["git", "config", "--global", "--add", "safe.directory", safe_path],
            cwd=None,
            timeout=30,
        )

    @staticmethod
    def _default_api_key_for_provider(provider: str) -> str:

        keys = {
            "gemini": bot_cfg.GOOGLE_AI_API_KEY,
            "deepseek": bot_cfg.DEEPSEEK_API_KEY,
            "groq": bot_cfg.GROQ_API_KEY,
            "openrouter": bot_cfg.OPENROUTER_API_KEY,
            "openai": bot_cfg.OPENAI_API_KEY,
            "anthropic": bot_cfg.ANTHROPIC_API_KEY,
        }
        return str(keys.get(provider, "") or "").strip()

    @staticmethod
    def _default_model_for_provider(provider: str) -> str:

        defaults = {
            "gemini": bot_cfg.get_str("OPENCLAW_CLINE_GEMINI_MODEL", bot_cfg.GEMINI_MODEL or "gemini-2.0-flash"),
            "deepseek": bot_cfg.get_str("OPENCLAW_CLINE_DEEPSEEK_MODEL", "deepseek-chat"),
            "groq": bot_cfg.get_str("OPENCLAW_CLINE_GROQ_MODEL", "llama-3.3-70b-versatile"),
            "openrouter": bot_cfg.get_str(
                "OPENCLAW_CLINE_OPENROUTER_MODEL",
                bot_cfg.OPENROUTER_MODEL or "qwen/qwen3-next-80b-a3b-instruct:free",
            ),
            "openai": bot_cfg.get_str("OPENCLAW_CLINE_OPENAI_MODEL", "gpt-4o-mini"),
            "anthropic": bot_cfg.get_str("OPENCLAW_CLINE_ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        }
        return str(defaults.get(provider, "") or "").strip()

    def _configure_cline_provider(
        self,
        *,
        client: paramiko.SSHClient,
        binary: str,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
    ) -> dict[str, Any]:

        args = [binary, "auth", "-p", provider, "-k", api_key]
        if model:
            args.extend(["-m", model])
        if base_url:
            args.extend(["-b", base_url])
        return self._run_command(client, args, cwd=None, timeout=120)

    @staticmethod
    def _is_retryable_cline_failure(result: dict[str, Any]) -> bool:

        text = (
            f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
        ).lower()
        markers = (
            "not authenticated",
            "api request failed",
            "provider returned error",
            "rate limit",
            "rate_limit_exceeded",
            "resource_exhausted",
            "quota",
            "quota exceeded",
            "insufficient_quota",
            "status\":402",
            "status\":429",
            "status':402",
            "status':429",
            "spend limit",
            "billing",
            "too many requests",
        )
        return any(marker in text for marker in markers)

    def _run_cline_with_auto_switch(
        self,
        *,
        client: paramiko.SSHClient,
        binary: str,
        prompt: str,
        cwd: str | None,
        timeout: int,
        initial_result: dict[str, Any],
    ) -> dict[str, Any]:

        attempted: list[str] = []
        last = dict(initial_result)
        use_prompt_file = self.remote_os == "windows"
        run_args = [binary, *self._coding_prefix["cline"]]
        if not use_prompt_file:
            run_args.append(prompt)

        for provider in self._cline_provider_priority:
            api_key = self._default_api_key_for_provider(provider)
            if not api_key:
                continue
            model = self._default_model_for_provider(provider)
            base_url = self._cline_provider_base_urls.get(provider, "")
            attempted.append(provider)

            configured = self._configure_cline_provider(
                client=client,
                binary=binary,
                provider=provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
            )
            if configured.get("returncode", 1) != 0:
                last = configured
                continue

            if use_prompt_file:
                run = self._run_windows_command_with_prompt_file(
                    client=client,
                    args_without_prompt=run_args,
                    prompt=prompt,
                    cwd=cwd,
                    timeout=timeout,
                )
            else:
                run = self._run_command(client, run_args, cwd=cwd, timeout=timeout)
            notice = (
                f"Notice: auto-switched Cline provider to {provider}"
                + (f" (model: {model})." if model else ".")
            )
            run["stdout"] = (f"{notice}\n{run.get('stdout', '').strip()}").strip()
            if run.get("returncode", 1) == 0:
                run["stderr"] = (run.get("stderr") or "").strip()
                return run
            last = run
            if not self._is_retryable_cline_failure(run):
                return run

        if attempted:
            suffix = f"Auto-switch attempted providers: {', '.join(attempted)}."
            err = (last.get("stderr") or "").strip()
            out = (last.get("stdout") or "").strip()
            if err:
                last["stderr"] = f"{err}\n{suffix}"
            elif out:
                last["stderr"] = suffix
            else:
                last["stderr"] = f"Cline failed and {suffix}"
        return last

    def _resolve_windows_binary(self, client: paramiko.SSHClient, binary: str) -> tuple[str, bool]:

        b = (binary or "").strip()
        if not b:
            return binary, False

        # Explicit path already provided.
        if any(ch in b for ch in ("\\", "/", ":")):
            return b, self._remote_path_exists(client, b)

        # PATH lookup first.
        where_result = self._run_command(client, ["where", b], cwd=None)
        if where_result.get("returncode", 1) == 0:
            lines = [ln.strip() for ln in (where_result.get("stdout") or "").splitlines() if ln.strip()]
            if lines:
                return lines[0], True

        # Fallback: npm global bin for current user.
        npm_bin = rf"C:\Users\{self.username}\AppData\Roaming\npm"
        candidates = [
            rf"{npm_bin}\{b}.cmd",
            rf"{npm_bin}\{b}.exe",
            rf"{npm_bin}\{b}",
        ]
        for cand in candidates:
            if self._remote_path_exists(client, cand):
                return cand, True

        return b, False

    @staticmethod
    def _remote_path_exists(client: paramiko.SSHClient, path: str) -> bool:

        sftp = client.open_sftp()
        try:
            sftp.stat(path)
            return True
        except OSError:
            return False
        finally:
            sftp.close()

    def _norm_join(self, parent: str | None, child: str) -> str:

        if not parent:
            return child
        if self.remote_os == "windows":
            return str(PureWindowsPath(parent) / child)
        return str(PurePosixPath(parent) / child)

    def _file_read(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:

        filepath = self._require_str(params, "file")
        sftp = client.open_sftp()
        try:
            with sftp.open(filepath, "r") as fh:
                content = fh.read().decode("utf-8", errors="replace")
            if len(content) > 65536:
                content = content[:65536] + "\n... (truncated at 64 KB)"
            return {"returncode": 0, "stdout": content, "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                ps = (
                    f"$p={_ps_quote(filepath)}; "
                    "if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { Write-Error \"File not found: $p\"; exit 1 }; "
                    "$c=[System.IO.File]::ReadAllText($p,[System.Text.Encoding]::UTF8); "
                    "if ($c.Length -gt 65536) { $c.Substring(0,65536) + [Environment]::NewLine + '... (truncated at 64 KB)' } else { $c }"
                )
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _file_write(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:

        filepath = self._require_str(params, "file")
        content = params.get("content", "")
        if not isinstance(content, str):
            return {"returncode": 1, "stdout": "", "stderr": "content must be a string."}
        if len(content.encode("utf-8")) > 1_048_576:
            return {"returncode": 1, "stdout": "", "stderr": "Content exceeds 1 MB limit."}

        sftp = client.open_sftp()
        try:
            parent = str(PureWindowsPath(filepath).parent) if self.remote_os == "windows" else str(PurePosixPath(filepath).parent)
            self._sftp_makedirs(sftp, parent)
            with sftp.open(filepath, "w") as fh:
                fh.write(content)
            return {"returncode": 0, "stdout": f"Wrote {len(content)} bytes to {filepath}.", "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
                ps = (
                    f"$p={_ps_quote(filepath)}; "
                    "$d=Split-Path -Parent $p; if ($d) { New-Item -ItemType Directory -Path $d -Force | Out-Null }; "
                    f"$bytes=[System.Convert]::FromBase64String('{encoded}'); "
                    "[System.IO.File]::WriteAllBytes($p,$bytes);"
                )
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _create_directory(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:

        directory = self._require_str(params, "directory")
        sftp = client.open_sftp()
        try:
            self._sftp_makedirs(sftp, directory)
            return {"returncode": 0, "stdout": f"Created {directory}", "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                ps = f"$d={_ps_quote(directory)}; New-Item -ItemType Directory -Path $d -Force | Out-Null; Write-Output \"Created $d\""
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _sftp_makedirs(self, sftp: paramiko.SFTPClient, path: str) -> None:

        if not path:
            return
        if self.remote_os == "windows":
            parts = list(PureWindowsPath(path).parts)
            if len(parts) == 1 and parts[0].endswith("\\"):
                return
            current = parts[0]
            for p in parts[1:]:
                current = str(PureWindowsPath(current) / p)
                try:
                    sftp.stat(current)
                except OSError:
                    sftp.mkdir(current)
            return

        parts = list(PurePosixPath(path).parts)
        current = ""
        for p in parts:
            current = str(PurePosixPath(current) / p)
            try:
                sftp.stat(current)
            except OSError:
                sftp.mkdir(current)

    def _list_directory(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:

        directory = self._require_str(params, "directory")
        recursive = params.get("recursive", False) is True
        sftp = client.open_sftp()
        try:
            lines: list[str] = []
            self._walk_sftp(sftp, directory, recursive, 0, lines, {"count": 0})
            return {"returncode": 0, "stdout": "\n".join(lines), "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                if recursive:
                    ps = (
                        f"$d={_ps_quote(directory)}; "
                        "Get-ChildItem -LiteralPath $d -Recurse -Force | "
                        "Select-Object FullName,Length,PSIsContainer | "
                        "ForEach-Object { if ($_.PSIsContainer) { \"[DIR] $($_.FullName)\" } else { \"$($_.FullName)  ($($_.Length) bytes)\" } }"
                    )
                else:
                    ps = (
                        f"$d={_ps_quote(directory)}; "
                        "Get-ChildItem -LiteralPath $d -Force | "
                        "ForEach-Object { if ($_.PSIsContainer) { \"[DIR] $($_.Name)/\" } else { \"$($_.Name)  ($($_.Length) bytes)\" } }"
                    )
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _walk_sftp(
        self,
        sftp: paramiko.SFTPClient,
        directory: str,
        recursive: bool,
        depth: int,
        out: list[str],
        state: dict[str, int],
    ) -> None:

        max_depth = 3
        max_entries = 500

        entries = sorted(sftp.listdir_attr(directory), key=lambda e: e.filename.lower())
        for e in entries:
            if state["count"] >= max_entries:
                out.append("... (truncated)")
                return
            name = e.filename
            path = self._norm_join(directory, name)
            prefix = "  " * depth
            if stat.S_ISDIR(e.st_mode):
                out.append(f"{prefix}[DIR] {name}/")
                if recursive and depth < max_depth:
                    self._walk_sftp(sftp, path, True, depth + 1, out, state)
            else:
                out.append(f"{prefix}{name}  ({int(e.st_size)} bytes)")
            state["count"] += 1


_SSH_EXECUTOR: SSHTunnelExecutor | None = None


def get_ssh_executor() -> SSHTunnelExecutor:
    global _SSH_EXECUTOR
    if _SSH_EXECUTOR is None:
        _SSH_EXECUTOR = SSHTunnelExecutor()
    return _SSH_EXECUTOR
