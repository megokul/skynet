from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import aiohttp
from live_qwen_probe import qwen_probe_requests, validate_qwen_probe_result

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = REPO_ROOT / "openclaw-gateway"
for candidate in (str(REPO_ROOT), str(GATEWAY_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

_CONTAINER_LOG_ENV_PREFIX = "SKYNET_E2E_CONTAINER_LOG_"
_AUTH_BEARER_PATTERN = re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+\S+")
_BARE_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|session|api[_-]?key|private[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"(?i)bot\d+:[A-Za-z0-9_-]{20,}")
_LOCAL_HTTP_RETRY_ATTEMPTS = 3
_LOCAL_HTTP_RETRY_DELAY_SECONDS = 0.75


def _get_config_module() -> Any:
    import importlib.util as _ilu

    cached = sys.modules.get("config")
    if cached is not None and hasattr(cached, "get_live_e2e_policy"):
        return cached
    spec = _ilu.spec_from_file_location("config", str(GATEWAY_ROOT / "config.py"))
    if spec is None or spec.loader is None:
        return importlib.import_module("config")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["config"] = mod
    return mod


def _status_to_action_url(status_url: str) -> str:
    text = str(status_url or "").strip()
    if text.endswith("/status"):
        return text.rsplit("/", 1)[0] + "/action"
    return text.rstrip("/") + "/action"


class LiveTrace:
    def __init__(self, label: str) -> None:
        env_path = os.environ.get("SKYNET_LIVE_TRACE_FILE", "").strip()
        if env_path:
            path = Path(env_path)
        else:
            log_dir = REPO_ROOT / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / f"{label}-{int(time.time())}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._started = time.monotonic()
        self.log("trace.start", trace_file=str(self.path))
        print(f"[LIVE TRACE] {self.path}", flush=True)

    def log(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_s": round(time.monotonic() - self._started, 1),
            "event": event,
        }
        payload.update(fields)
        line = json.dumps(payload, ensure_ascii=True, default=str)
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def make_live_trace_logger(test_name: str) -> tuple[Path, Callable[..., None]]:
    trace = LiveTrace(test_name)
    return trace.path, trace.log


def sanitize_container_log_line(line: str, *, max_chars: int) -> str:
    text = str(line or "").replace("\x00", "").strip()
    if not text:
        return ""
    text = _AUTH_BEARER_PATTERN.sub("authorization: Bearer [REDACTED]", text)
    text = _BARE_BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _KEY_VALUE_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = _TELEGRAM_BOT_TOKEN_PATTERN.sub("bot[REDACTED]", text)
    if len(text) > max_chars:
        text = f"{text[:max_chars]}...[truncated:{len(text) - max_chars}]"
    return text


def _extract_docker_log_timestamp(line: str) -> tuple[str, str]:
    text = str(line or "")
    if not text:
        return "", ""
    first, sep, rest = text.partition(" ")
    if sep and "T" in first and (first.endswith("Z") or "+" in first or "-" in first[10:]):
        return first, rest
    return "", text


def _path_is_platform_compatible(candidate: str) -> bool:
    value = str(candidate or "").strip()
    if not value:
        return False
    if sys.platform == "win32":
        if value.startswith("/") or value.startswith("\\"):
            return False
    else:
        if len(value) > 2 and value[1] == ":":
            return False
    return True


def _resolve_ssh_profile(profile: str | None) -> str:
    resolved = str(profile or "").strip().lower()
    if resolved in {"tunnel", "transport", "auto"}:
        return resolved
    if Path("/.dockerenv").exists():
        return "transport"
    return "tunnel"


def resolve_container_log_stream_ssh(profile: str | None = None) -> dict[str, Any] | None:
    resolved_profile = _resolve_ssh_profile(profile)
    override_host = (os.environ.get(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_HOST") or "").strip()
    override_user = (os.environ.get(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_USER") or "").strip()
    override_key = (os.environ.get(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_KEY") or "").strip()

    source_profiles: list[tuple[str, str, str, str]]
    if resolved_profile == "transport":
        source_profiles = [
            (
                "ssh_fallback",
                (os.environ.get("OPENCLAW_SSH_HOST") or "").strip(),
                (os.environ.get("OPENCLAW_SSH_USER") or "").strip(),
                (os.environ.get("OPENCLAW_SSH_KEY_PATH") or "").strip(),
            ),
        ]
    elif resolved_profile == "tunnel":
        source_profiles = [
            (
                "tunnel",
                (os.environ.get("OPENCLAW_TUNNEL_EC2_HOST") or "").strip(),
                (os.environ.get("OPENCLAW_TUNNEL_EC2_USER") or "").strip(),
                (os.environ.get("OPENCLAW_TUNNEL_SSH_KEY") or "").strip(),
            ),
        ]
    else:
        source_profiles = [
            (
                "tunnel",
                (os.environ.get("OPENCLAW_TUNNEL_EC2_HOST") or "").strip(),
                (os.environ.get("OPENCLAW_TUNNEL_EC2_USER") or "").strip(),
                (os.environ.get("OPENCLAW_TUNNEL_SSH_KEY") or "").strip(),
            ),
            (
                "ssh_fallback",
                (os.environ.get("OPENCLAW_SSH_HOST") or "").strip(),
                (os.environ.get("OPENCLAW_SSH_USER") or "").strip(),
                (os.environ.get("OPENCLAW_SSH_KEY_PATH") or "").strip(),
            ),
        ]

    host = override_host
    user = override_user
    key_candidates = [("e2e_override", override_key)] if override_key else []
    for source, source_host, source_user, source_key in source_profiles:
        if not host and source_host:
            host = source_host
        if not user and source_user:
            user = source_user
        if source_key:
            key_candidates.append((source, source_key))

    key_options = [(source, value) for source, value in key_candidates if value]
    key = ""
    key_source = ""
    for source, candidate in key_options:
        if not _path_is_platform_compatible(candidate):
            continue
        if Path(candidate).exists():
            key = candidate
            key_source = source
            break
    if not key and key_options:
        for source, candidate in key_options:
            if not _path_is_platform_compatible(candidate):
                continue
            key_source, key = source, candidate
            break
    raw_port = (os.environ.get(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_PORT") or "").strip()
    try:
        port = int(raw_port or 22)
    except ValueError:
        port = 22
    if not host or not user or not key:
        return None
    return {
        "host": host,
        "user": user,
        "key": key,
        "key_source": key_source or "unknown",
        "port": max(1, port),
    }


def container_log_error_summary(source: Any | None) -> str:
    if source is None or not hasattr(source, "error_tail"):
        return ""
    try:
        tail = list(source.error_tail())
    except Exception:
        return ""
    if not tail:
        return ""
    return " | ".join(tail[-5:])


def _build_ssh_command(ssh: dict[str, Any], remote_cmd: str) -> list[str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=6",
    ]
    port = int(ssh.get("port") or 22)
    key = str(ssh.get("key") or "").strip()
    if port != 22:
        cmd.extend(["-p", str(port)])
    if key:
        cmd.extend(["-i", key])
    cmd.extend([f"{ssh.get('user')}@{ssh.get('host')}", remote_cmd])
    return cmd


def _build_stream_remote_cmd(container: str, since_utc_iso: str) -> str:
    return f"docker logs -f --since {since_utc_iso} --timestamps {container}"


def _build_snapshot_remote_cmd(container: str, tail_lines: int) -> str:
    return f"docker logs --tail {max(1, int(tail_lines))} --timestamps {container}"


async def _run_capture_command(cmd: list[str], *, timeout_s: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    return (
        int(proc.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _normalize_container_log_config(config_override: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _get_config_module()
    base = dict(cfg.get_live_e2e_container_log_config())
    override = dict(config_override or {})
    if "sources" in override:
        override["sources"] = [
            str(item).strip() for item in list(override.get("sources") or []) if str(item).strip()
        ]
    if "tail_overrides" in override:
        normalized_overrides: dict[str, int] = {}
        for key, value in dict(override.get("tail_overrides") or {}).items():
            name = str(key or "").strip()
            if not name:
                continue
            try:
                tail_lines = int(value)
            except (TypeError, ValueError):
                continue
            if tail_lines < 1:
                continue
            normalized_overrides[name] = tail_lines
        override["tail_overrides"] = normalized_overrides
    base.update(override)
    base["sources"] = [str(item).strip() for item in list(base.get("sources") or []) if str(item).strip()]
    base["max_line_chars"] = max(200, int(base.get("max_line_chars") or 1200))
    base["ring_lines"] = max(20, int(base.get("ring_lines") or 300))
    base["tail_default"] = max(1, int(base.get("tail_default") or 100))
    base["tail_overrides"] = dict(base.get("tail_overrides") or {})
    base["stream_enabled"] = bool(base.get("stream_enabled", True))
    base["require_stream"] = bool(base.get("require_stream", True))
    return base


def _normalize_cleanup_config(config_override: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _get_config_module()
    base = dict(cfg.get_live_e2e_cleanup_config())
    override = dict(config_override or {})
    if "targets" in override:
        override["targets"] = [
            str(item).strip().lower()
            for item in list(override.get("targets") or [])
            if str(item).strip()
        ]
    base.update(override)
    base["enabled"] = bool(base.get("enabled", True))
    base["targets"] = [
        str(item).strip().lower()
        for item in list(base.get("targets") or [])
        if str(item).strip()
    ]
    base["grace_seconds"] = max(1, int(base.get("grace_seconds") or 5))
    return base


def _normalize_process_command_line(command_line: str) -> str:
    return str(command_line or "").replace("\\", "/").lower()


def _list_local_processes() -> list[dict[str, Any]]:
    if sys.platform == "win32":
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
            "ConvertTo-Json -Compress",
        ]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "powershell process query failed")
        raw = completed.stdout.strip()
        if not raw:
            return []
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload = [payload]
        records: list[dict[str, Any]] = []
        for item in list(payload or []):
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("ProcessId") or 0)
            except (TypeError, ValueError):
                pid = 0
            try:
                ppid = int(item.get("ParentProcessId") or 0)
            except (TypeError, ValueError):
                ppid = 0
            if pid <= 0:
                continue
            records.append(
                {
                    "pid": pid,
                    "ppid": ppid,
                    "name": str(item.get("Name") or ""),
                    "command_line": str(item.get("CommandLine") or ""),
                }
            )
        return records

    completed = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,comm=,args="],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "ps process query failed")
    records: list[dict[str, Any]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        records.append(
            {
                "pid": pid,
                "ppid": ppid,
                "name": parts[2],
                "command_line": parts[3],
            }
        )
    return records


def _match_cleanup_target(
    *,
    process: dict[str, Any],
    target: str,
    repo_root: Path,
) -> bool:
    command_line = _normalize_process_command_line(process.get("command_line") or "")
    if not command_line:
        return False
    repo_marker = _normalize_process_command_line(str(repo_root))
    if repo_marker not in command_line:
        return False
    if target == "worker_launcher":
        return "scripts/run_worker_agent.ps1" in command_line
    if target == "worker_agent":
        return "openclaw-agent/main.py" in command_line
    if target == "live_runner":
        return "openclaw-gateway/tests/e2e_live.py" in command_line
    return False


def _terminate_process_tree(pid: int, *, grace_seconds: int) -> dict[str, Any]:
    if pid <= 0:
        return {"status": "invalid_pid", "detail": "pid<=0"}
    if sys.platform == "win32":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(grace_seconds) + 5),
        )
        detail = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode == 0:
            return {"status": "terminated", "detail": detail}
        lowered = detail.lower()
        if "not found" in lowered or "no running instance" in lowered or "not valid" in lowered:
            return {"status": "missing", "detail": detail}
        return {"status": "error", "detail": detail or f"taskkill returncode={completed.returncode}"}

    with contextlib.suppress(ProcessLookupError):
        os.kill(int(pid), 15)
    deadline = time.monotonic() + max(1, int(grace_seconds))
    while time.monotonic() < deadline:
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid), 0)
            time.sleep(0.1)
            continue
        return {"status": "terminated", "detail": "terminated"}
    with contextlib.suppress(ProcessLookupError):
        os.kill(int(pid), 9)
        return {"status": "killed", "detail": "killed"}
    return {"status": "missing", "detail": "already_exited"}


class LiveRunCleanupManager:
    def __init__(
        self,
        *,
        trace_fn: Callable[..., None],
        config_override: dict[str, Any] | None = None,
    ) -> None:
        self._trace = trace_fn
        self._config = _normalize_cleanup_config(config_override)
        self._repo_root = REPO_ROOT
        self._registered: dict[int, str] = {}
        self._cleaned = False

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._config)

    def register_subprocess(self, process: subprocess.Popen[str], *, label: str) -> None:
        pid = int(getattr(process, "pid", 0) or 0)
        if pid <= 0:
            return
        self._registered[pid] = str(label or "subprocess")
        self._trace("test.cleanup.register", status="ok", pid=pid, label=self._registered[pid])

    def unregister_subprocess(self, process: subprocess.Popen[str] | None) -> None:
        pid = int(getattr(process, "pid", 0) or 0)
        if pid > 0:
            self._registered.pop(pid, None)

    def cleanup(self, *, reason: str) -> list[dict[str, Any]]:
        if self._cleaned:
            return []
        self._cleaned = True
        if not bool(self._config.get("enabled", True)):
            self._trace("test.cleanup.disabled", status="skip", reason=reason)
            return []

        results: list[dict[str, Any]] = []
        tracked = sorted(self._registered.items())
        self._trace(
            "test.cleanup.start",
            status="start",
            reason=reason,
            targets=list(self._config.get("targets") or []),
            tracked_pids=[pid for pid, _label in tracked],
        )

        handled: set[int] = set()
        for pid, label in tracked:
            result = _terminate_process_tree(pid, grace_seconds=int(self._config["grace_seconds"]))
            handled.add(pid)
            record = {
                "pid": pid,
                "label": label,
                "target": "registered_subprocess",
                **result,
            }
            results.append(record)
            self._trace("test.cleanup.item", **record)

        try:
            processes = _list_local_processes()
        except Exception as exc:
            self._trace(
                "test.cleanup.error",
                status="fail",
                reason=reason,
                error=f"{type(exc).__name__}: {exc}",
            )
            return results

        current_pid = os.getpid()
        matched: list[tuple[int, str, str]] = []
        for process in processes:
            pid = int(process.get("pid") or 0)
            if pid <= 0 or pid == current_pid or pid in handled:
                continue
            for target in list(self._config.get("targets") or []):
                if _match_cleanup_target(process=process, target=target, repo_root=self._repo_root):
                    matched.append((pid, target, str(process.get("name") or "")))
                    break

        matched.sort(key=lambda item: item[0])
        for pid, target, name in matched:
            result = _terminate_process_tree(pid, grace_seconds=int(self._config["grace_seconds"]))
            record = {
                "pid": pid,
                "label": name or target,
                "target": target,
                **result,
            }
            results.append(record)
            self._trace("test.cleanup.item", **record)

        self._trace(
            "test.cleanup.end",
            status="ok",
            reason=reason,
            cleaned=len(results),
        )
        return results


async def fetch_local_gateway_status(*, url: str, timeout_seconds: int = 10) -> dict[str, Any]:
    return await _request_local_http_json(
        method="GET",
        url=url,
        timeout_seconds=max(1, int(timeout_seconds)),
        error_prefix="PREFLIGHT_STATUS_UNAVAILABLE",
    )


async def fetch_remote_gateway_status(
    *,
    container_name: str,
    status_url: str,
    diagnostics_profile: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    ssh = resolve_container_log_stream_ssh(diagnostics_profile)
    if not ssh:
        raise AssertionError("PREFLIGHT_DIAGNOSTICS_CONFIG_ERROR: missing SSH credentials for remote status probe")
    key_path = Path(str(ssh.get("key") or "").strip())
    if not key_path.exists():
        raise AssertionError(
            "PREFLIGHT_DIAGNOSTICS_CONFIG_ERROR: SSH key path does not exist "
            f"({key_path})"
        )
    script = (
        "import sys,urllib.request;"
        f"sys.stdout.write(urllib.request.urlopen({status_url!r}, timeout=10).read().decode('utf-8'))"
    )
    remote_cmd = f"docker exec {shlex.quote(container_name)} python -c {shlex.quote(script)}"
    cmd = _build_ssh_command(ssh, remote_cmd)
    rc, stdout, stderr = await _run_capture_command(cmd, timeout_s=max(5.0, float(timeout_seconds)))
    if rc != 0:
        detail = stderr.strip() or stdout.strip() or f"returncode:{rc}"
        raise AssertionError(f"PREFLIGHT_STATUS_UNAVAILABLE: {detail[:240]}")
    payload = json.loads(stdout)
    return dict(payload) if isinstance(payload, dict) else {}


async def _post_remote_gateway_action(
    *,
    container_name: str,
    action_url: str,
    action: str,
    params: dict[str, Any],
    diagnostics_profile: str,
    timeout_seconds: int = 25,
) -> dict[str, Any]:
    ssh = resolve_container_log_stream_ssh(diagnostics_profile)
    if not ssh:
        raise AssertionError("PREFLIGHT_QWEN_ACTION_FAILED: missing SSH credentials for remote action probe")
    key_path = Path(str(ssh.get("key") or "").strip())
    if not key_path.exists():
        raise AssertionError(
            "PREFLIGHT_QWEN_ACTION_FAILED: SSH key path does not exist "
            f"({key_path})"
        )
    body = {
        "action": action,
        "params": params,
        "confirmed": True,
    }
    payload_b64 = base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii")
    script = (
        "import base64,json,sys,urllib.request;"
        f"data=base64.b64decode({payload_b64!r});"
        f"req=urllib.request.Request({action_url!r}, data=data, headers={{'Content-Type':'application/json'}}, method='POST');"
        "sys.stdout.write(urllib.request.urlopen(req, timeout=20).read().decode('utf-8'))"
    )
    remote_cmd = f"docker exec {shlex.quote(container_name)} python -c {shlex.quote(script)}"
    cmd = _build_ssh_command(ssh, remote_cmd)
    rc, stdout, stderr = await _run_capture_command(cmd, timeout_s=max(5.0, float(timeout_seconds)))
    if rc != 0:
        detail = stderr.strip() or stdout.strip() or f"returncode:{rc}"
        raise AssertionError(f"PREFLIGHT_QWEN_ACTION_FAILED: action={action} detail={detail[:240]}")
    payload = json.loads(stdout)
    return dict(payload) if isinstance(payload, dict) else {}


async def _post_local_gateway_action(
    *,
    action_url: str,
    action: str,
    params: dict[str, Any],
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    return await _request_local_http_json(
        method="POST",
        url=action_url,
        json_body={"action": action, "params": params, "confirmed": True},
        timeout_seconds=max(5, int(timeout_seconds)),
        error_prefix=f"PREFLIGHT_QWEN_ACTION_FAILED: action={action}",
    )


async def _post_tunnel_gateway_action(
    *,
    tunnel_http_port: int,
    action: str,
    params: dict[str, Any],
    timeout_seconds: int = 25,
) -> dict[str, Any]:
    """POST to gateway /action via the local SSH tunnel (localhost:tunnel_http_port).

    This avoids all shell-quoting issues with docker exec by going through the
    HTTP API tunnel that run_worker_agent.ps1 establishes alongside the WS tunnel.
    """
    url = f"http://127.0.0.1:{tunnel_http_port}/action"
    return await _request_local_http_json(
        method="POST",
        url=url,
        json_body={"action": action, "params": params, "confirmed": True},
        timeout_seconds=max(5, int(timeout_seconds)),
        error_prefix=f"PREFLIGHT_QWEN_ACTION_FAILED: action={action}",
    )


def _is_retryable_local_http_error(exc: BaseException) -> bool:
    if isinstance(exc, AssertionError):
        return False
    if isinstance(exc, aiohttp.ClientResponseError):
        return False
    if isinstance(exc, aiohttp.ClientError):
        return True
    if isinstance(exc, (asyncio.TimeoutError, ConnectionResetError)):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, "winerror", None) in {64, 10054}
    return False


async def _request_local_http_json(
    *,
    method: str,
    url: str,
    timeout_seconds: int,
    error_prefix: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last_error: BaseException | None = None
    timeout = aiohttp.ClientTimeout(total=max(1, int(timeout_seconds)))
    for attempt in range(1, _LOCAL_HTTP_RETRY_ATTEMPTS + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, url, json=json_body) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        detail = text.strip() or f"status={resp.status}"
                        raise AssertionError(f"{error_prefix}: status={resp.status} detail={detail[:240]}")
                    payload = json.loads(text)
                    return dict(payload) if isinstance(payload, dict) else {}
        except Exception as exc:
            last_error = exc
            if attempt >= _LOCAL_HTTP_RETRY_ATTEMPTS or not _is_retryable_local_http_error(exc):
                raise
            await asyncio.sleep(_LOCAL_HTTP_RETRY_DELAY_SECONDS * attempt)
    if last_error is not None:
        raise last_error
    raise AssertionError(f"{error_prefix}: unknown local HTTP failure")


async def run_qwen_preflight_smoke_probe(
    *,
    trace_fn: Callable[..., None],
    flow: str,
    policy: dict[str, Any],
    local_status_url: str | None = None,
) -> None:
    qwen_smoke_cfg = dict(policy.get("qwen_smoke") or {})
    required_agents = {str(item).strip().lower() for item in list(policy.get("required_worker_agents") or []) if str(item).strip()}
    if not bool(qwen_smoke_cfg.get("enabled", True)) or "qwen" not in required_agents:
        trace_fn("preflight.qwen_smoke.skip", flow=flow, enabled=bool(qwen_smoke_cfg.get("enabled", True)))
        return

    cfg = _get_config_module()
    timeout_seconds = max(15, int(qwen_smoke_cfg.get("timeout_seconds", 45) or 45))
    diagnostics_profile = str(policy.get("diagnostics_profile") or "tunnel")
    worker_id = str(getattr(cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "") or "worker-primary")
    session_key = uuid.uuid4().hex
    action_url = str(local_status_url or f"http://127.0.0.1:{int(getattr(cfg, 'HTTP_PORT', 8766) or 8766)}/status")
    remote_action_url = _status_to_action_url(str(policy.get("remote_status_url") or "http://localhost:8766/status"))
    trace_fn(
        "preflight.qwen_smoke.start",
        flow=flow,
        timeout_seconds=timeout_seconds,
        status_probe_mode=str(policy.get("status_probe_mode") or ""),
    )

    tunnel_http_port = int(policy.get("tunnel_http_port") or 0)

    async def _dispatch(action: str, params: dict[str, Any]) -> dict[str, Any]:
        if str(policy.get("status_probe_mode") or "") == "remote_container_http":
            if tunnel_http_port > 0:
                try:
                    return await _post_tunnel_gateway_action(
                        tunnel_http_port=tunnel_http_port,
                        action=action,
                        params=params,
                        timeout_seconds=timeout_seconds,
                    )
                except Exception as exc:
                    if not _is_retryable_local_http_error(exc):
                        raise
                    trace_fn(
                        "preflight.qwen_action.tunnel_fallback",
                        action=action,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            return await _post_remote_gateway_action(
                container_name=str(policy.get("remote_gateway_container") or "openclaw-gateway"),
                action_url=remote_action_url,
                action=action,
                params=params,
                diagnostics_profile=diagnostics_profile,
                timeout_seconds=timeout_seconds,
            )
        return await _post_local_gateway_action(
            action_url=_status_to_action_url(action_url),
            action=action,
            params=params,
            timeout_seconds=timeout_seconds,
        )

    try:
        for probe in qwen_probe_requests():
            probe_kind = str(probe.get("probe_kind") or "unknown")
            trace_fn(
                "preflight.qwen_probe.start",
                flow=flow,
                probe_kind=probe_kind,
                timeout_seconds=timeout_seconds,
            )
            probe_session_key = uuid.uuid4().hex
            planner_state_json = dict(probe.get("planner_state_json") or {})
            requirement_summary_md = str(probe.get("requirement_summary_md") or "")
            result = await _dispatch(
                "run_coding_agent",
                {
                    "agent": "qwen",
                    "backend": "auto",
                    "task_mode": str(probe.get("task_mode") or "").strip(),
                    "reply_contract": str(probe.get("reply_contract") or "").strip(),
                    "prompt": str(probe.get("prompt") or ""),
                    "qwen_context_text": str(probe.get("qwen_context_text") or ""),
                    "planner_state_json": planner_state_json,
                    "requirement_summary_md": requirement_summary_md,
                    "timeout_seconds": timeout_seconds,
                    "project_id": "preflight-qwen",
                    "task_id": f"preflight-qwen-{probe_kind}",
                    "worker_id": worker_id,
                    "session_key": probe_session_key,
                },
            )
            if result.get("status") == "error":
                raise AssertionError(
                    f"PREFLIGHT_QWEN_PROVIDER_CAPABILITY_FAILED: probe={probe_kind} detail={str(result.get('error') or 'run_coding_agent failed')[:220]}"
                )
            probe_result = validate_qwen_probe_result(probe=probe, result=result)
            trace_fn(
                "preflight.qwen_probe.result",
                flow=flow,
                **probe_result,
            )
    finally:
        pass


def _preflight_missing_agents(status_payload: dict[str, Any], required_agents: list[str]) -> list[str]:
    coding_agents = status_payload.get("coding_agents") or {}
    available = {str(name).strip().lower() for name in dict(coding_agents).keys()}
    missing: list[str] = []
    for raw in required_agents:
        agent = str(raw or "").strip().lower()
        if agent and agent not in available:
            missing.append(agent)
    return missing


def _status_missing_fields(status_payload: dict[str, Any], required_fields: list[str]) -> list[str]:
    return [field for field in required_fields if field not in status_payload]


def _build_revision_matches(expected: str, actual: str) -> bool:
    want = str(expected or "").strip().lower()
    have = str(actual or "").strip().lower()
    if not want:
        return True
    if not have:
        return False
    return have == want or have.startswith(want) or want.startswith(have)


def _status_needs_local_warmup(
    status_payload: dict[str, Any],
    *,
    required_transport: str,
    required_agents: list[str],
) -> bool:
    if required_transport != "websocket_primary":
        return False
    if not bool(status_payload.get("agent_connected", False)):
        return False
    if str(status_payload.get("primary_transport_mode") or "").strip().lower() != "websocket_primary":
        return True
    if not bool(status_payload.get("websocket_health_ok", False)):
        return True
    return bool(_preflight_missing_agents(status_payload, required_agents))


async def run_live_e2e_preflight(
    *,
    trace_fn: Callable[..., None],
    flow: str,
    policy: dict[str, Any],
    local_status_url: str | None = None,
) -> dict[str, Any]:
    cfg = _get_config_module()
    trace_fn(
        "preflight.start",
        flow=flow,
        required_transport=policy.get("required_transport"),
        allow_fallback=bool(policy.get("allow_fallback", False)),
        required_worker_agents=list(policy.get("required_worker_agents") or []),
        diagnostics_profile=str(policy.get("diagnostics_profile") or ""),
        require_telegram_poller=bool(policy.get("require_telegram_poller", False)),
        qwen_smoke_enabled=bool(dict(policy.get("qwen_smoke") or {}).get("enabled", True)),
    )

    container_log_cfg = dict(policy.get("container_log") or {})
    diagnostics_profile = str(policy.get("diagnostics_profile") or container_log_cfg.get("ssh_profile") or "")
    needs_ssh = str(policy.get("status_probe_mode") or "") == "remote_container_http"
    required_transport = str(policy.get("required_transport") or "").strip().lower()
    required_worker_agents = list(policy.get("required_worker_agents") or [])
    has_tunnel = int(policy.get("tunnel_http_port") or 0) > 0
    ssh = resolve_container_log_stream_ssh(diagnostics_profile)
    if not ssh and needs_ssh and not has_tunnel:
        raise AssertionError(
            "PREFLIGHT_DIAGNOSTICS_CONFIG_ERROR: missing SSH credentials for diagnostics profile "
            f"'{diagnostics_profile or 'auto'}'"
        )
    if ssh:
        key_path = Path(str(ssh.get("key") or "").strip())
        if not key_path.exists() and needs_ssh:
            raise AssertionError(
                "PREFLIGHT_DIAGNOSTICS_CONFIG_ERROR: SSH key path does not exist "
                f"({key_path})"
            )
    if not ssh:
        trace_fn(
            "preflight.ssh_unavailable",
            status="degraded",
            diagnostics_profile=diagnostics_profile or "auto",
        )

    tunnel_http_port = int(policy.get("tunnel_http_port") or 0)
    if needs_ssh and tunnel_http_port > 0:
        tunnel_status_url = f"http://127.0.0.1:{tunnel_http_port}/status"
        try:
            status_payload = await fetch_local_gateway_status(url=tunnel_status_url)
        except Exception as exc:
            if not _is_retryable_local_http_error(exc):
                raise
            trace_fn(
                "preflight.status.tunnel_fallback",
                url=tunnel_status_url,
                error=f"{type(exc).__name__}: {exc}",
            )
            status_payload = await fetch_remote_gateway_status(
                container_name=str(policy.get("remote_gateway_container") or "openclaw-gateway"),
                status_url=str(policy.get("remote_status_url") or "http://localhost:8766/status"),
                diagnostics_profile=diagnostics_profile,
            )
    elif needs_ssh:
        status_payload = await fetch_remote_gateway_status(
            container_name=str(policy.get("remote_gateway_container") or "openclaw-gateway"),
            status_url=str(policy.get("remote_status_url") or "http://localhost:8766/status"),
            diagnostics_profile=diagnostics_profile,
        )
    else:
        status_payload = await fetch_local_gateway_status(
            url=str(local_status_url or f"http://127.0.0.1:{int(getattr(cfg, 'HTTP_PORT', 8766) or 8766)}/status"),
        )

    if not needs_ssh and _status_needs_local_warmup(
        status_payload,
        required_transport=required_transport,
        required_agents=required_worker_agents,
    ):
        warmup_url = str(local_status_url or f"http://127.0.0.1:{int(getattr(cfg, 'HTTP_PORT', 8766) or 8766)}/status")
        warmup_deadline = time.monotonic() + 10.0
        trace_fn(
            "preflight.status.warmup.start",
            flow=flow,
            url=warmup_url,
            required_transport=required_transport,
            required_worker_agents=required_worker_agents,
        )
        while time.monotonic() < warmup_deadline:
            await asyncio.sleep(0.5)
            status_payload = await fetch_local_gateway_status(url=warmup_url)
            if not _status_needs_local_warmup(
                status_payload,
                required_transport=required_transport,
                required_agents=required_worker_agents,
            ):
                trace_fn(
                    "preflight.status.warmup.ready",
                    flow=flow,
                    primary_transport_mode=str(status_payload.get("primary_transport_mode") or ""),
                    websocket_health_ok=bool(status_payload.get("websocket_health_ok", False)),
                    coding_agents=sorted(dict(status_payload.get("coding_agents") or {}).keys()),
                )
                break

    trace_fn(
        "preflight.status",
        flow=flow,
        build_revision=str(status_payload.get("build_revision") or ""),
        primary_transport_mode=str(status_payload.get("primary_transport_mode") or ""),
        agent_connected=bool(status_payload.get("agent_connected", False)),
        websocket_health_ok=bool(status_payload.get("websocket_health_ok", False)),
        telegram_poller_state=str(status_payload.get("telegram_poller_state") or ""),
        telegram_poller_lock_healthy=bool(status_payload.get("telegram_poller_lock_healthy", False)),
        live_e2e_active=bool(status_payload.get("live_e2e_active", False)),
        live_e2e_flow=str(status_payload.get("live_e2e_flow") or ""),
        live_e2e_effective_coding_stage_chain=list(
            status_payload.get("live_e2e_effective_coding_stage_chain") or []
        ),
        live_e2e_active_present="live_e2e_active" in status_payload,
        telegram_poller_state_present="telegram_poller_state" in status_payload,
        coding_agents=sorted(dict(status_payload.get("coding_agents") or {}).keys()),
    )

    missing_contract_fields = _status_missing_fields(
        status_payload,
        [
            "live_e2e_active",
            "live_e2e_flow",
            "live_e2e_effective_coding_stage_chain",
        ],
    )
    if missing_contract_fields:
        trace_fn(
            "preflight.status.legacy_contract",
            flow=flow,
            missing_fields=missing_contract_fields,
        )
    elif not bool(status_payload.get("live_e2e_active", False)):
        raise AssertionError("PREFLIGHT_LIVE_POLICY_INACTIVE: gateway is not enforcing live E2E policy")

    expected_build_revision = str(policy.get("expected_remote_build_revision") or "").strip()
    actual_build_revision = str(status_payload.get("build_revision") or "").strip()
    if expected_build_revision and not needs_ssh:
        trace_fn(
            "preflight.build_revision.skip_local",
            flow=flow,
            expected=expected_build_revision,
            actual=actual_build_revision or "missing",
        )
    elif expected_build_revision and not _build_revision_matches(
        expected_build_revision,
        actual_build_revision,
    ):
        raise AssertionError(
            "PREFLIGHT_BUILD_REVISION_MISMATCH: "
            f"expected={expected_build_revision} actual={actual_build_revision or 'missing'}"
        )

    actual_transport = str(status_payload.get("primary_transport_mode") or "").strip().lower()
    if required_transport and actual_transport != required_transport:
        raise AssertionError(
            f"PREFLIGHT_TRANSPORT_MISMATCH: expected={required_transport} actual={actual_transport or 'missing'}"
        )

    if not bool(policy.get("allow_fallback", False)) and actual_transport == "ssh_fallback":
        raise AssertionError("PREFLIGHT_TRANSPORT_FALLBACK: gateway is using ssh_fallback")

    if required_transport == "websocket_primary":
        if not bool(status_payload.get("agent_connected", False)):
            raise AssertionError("PREFLIGHT_WORKER_UNAVAILABLE: no websocket worker connected")
        if not bool(status_payload.get("websocket_health_ok", False)):
            raise AssertionError("PREFLIGHT_WORKER_STALE: websocket worker heartbeat is stale")

    missing_agents = _preflight_missing_agents(status_payload, required_worker_agents)
    if missing_agents:
        raise AssertionError(
            "PREFLIGHT_REQUIRED_AGENTS_MISSING: " + ",".join(sorted(missing_agents))
        )

    await run_qwen_preflight_smoke_probe(
        trace_fn=trace_fn,
        flow=flow,
        policy=policy,
        local_status_url=local_status_url,
    )

    if bool(policy.get("require_telegram_poller", False)):
        missing_poller_fields = _status_missing_fields(
            status_payload,
            ["telegram_poller_state", "telegram_poller_lock_healthy"],
        )
        if missing_poller_fields:
            trace_fn(
                "preflight.telegram_poller.legacy_contract",
                flow=flow,
                missing_fields=missing_poller_fields,
            )
        else:
            poller_state = str(status_payload.get("telegram_poller_state") or "").strip().lower()
            if poller_state != "running":
                raise AssertionError(
                    f"PREFLIGHT_TELEGRAM_POLLER_STATE: expected=running actual={poller_state or 'missing'}"
                )
            if not bool(status_payload.get("telegram_poller_lock_healthy", False)):
                raise AssertionError("PREFLIGHT_TELEGRAM_POLLER_LOCK: poller lease is not healthy")

    trace_fn("preflight.ok", flow=flow)
    return status_payload


class ContainerLogStreamer:
    def __init__(
        self,
        *,
        trace_fn: Callable[..., None],
        since_utc_iso: str,
        containers: list[str],
        max_line_chars: int,
        ring_lines: int,
        ssh_profile: str,
    ) -> None:
        self._trace = trace_fn
        self._since = since_utc_iso
        self._containers = [c.strip() for c in containers if c.strip()]
        self._max_line_chars = max(200, int(max_line_chars))
        self._ring_lines = max(20, int(ring_lines))
        self._ssh_profile = str(ssh_profile or "").strip().lower()
        self._ring: dict[str, deque[str]] = {
            name: deque(maxlen=self._ring_lines) for name in self._containers
        }
        self._tasks: list[asyncio.Task[Any]] = []
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._stop = False
        self._seq = 0
        self._line_count = 0
        self._last_activity_monotonic = time.monotonic()
        self._ssh: dict[str, Any] | None = None
        self._errors: deque[str] = deque(maxlen=20)

    def _record_error(self, *, container: str, message: str) -> None:
        entry = f"{container}:{message}".strip()
        if entry:
            self._errors.append(entry)

    async def start(self) -> None:
        self._ssh = resolve_container_log_stream_ssh(self._ssh_profile)
        if not self._ssh:
            self._record_error(
                container="__all__",
                message="missing SSH credentials "
                "(set SKYNET_E2E_CONTAINER_LOG_SSH_* or OPENCLAW_TUNNEL_EC2_HOST/USER/SSH_KEY)",
            )
            self._trace(
                "container.log.stream.ssh_unavailable",
                status="degraded",
                reason="no_ssh_credentials",
            )
            return
        key_path = Path(str(self._ssh.get("key") or "").strip())
        if not key_path.exists():
            self._record_error(
                container="__all__",
                message=f"SSH key path does not exist ({key_path})",
            )
            self._trace(
                "container.log.stream.ssh_unavailable",
                status="degraded",
                reason="key_not_found",
                key_path=str(key_path),
            )
            return
        self._trace(
            "container.log.stream.start",
            status="start",
            containers=self._containers,
            host=self._ssh.get("host"),
            user=self._ssh.get("user"),
            key_source=self._ssh.get("key_source"),
            port=int(self._ssh.get("port") or 22),
            since=self._since,
        )
        for container in self._containers:
            self._tasks.append(asyncio.create_task(self._stream_container(container)))

    async def stop(self) -> None:
        self._stop = True
        for proc in list(self._procs.values()):
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
        if self._tasks:
            done, pending = await asyncio.wait(self._tasks, timeout=8)
            for task in pending:
                task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(*pending, return_exceptions=True)
            with contextlib.suppress(Exception):
                await asyncio.gather(*done, return_exceptions=True)
        self._trace(
            "container.log.stream.stop",
            status="ok",
            containers=self._containers,
            line_count=self._line_count,
            last_activity_s=round(max(0.0, time.monotonic() - self._last_activity_monotonic), 1),
        )

    async def _stream_container(self, container: str) -> None:
        assert self._ssh is not None
        remote_cmd = _build_stream_remote_cmd(container, self._since)
        cmd = _build_ssh_command(self._ssh, remote_cmd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            self._record_error(container=container, message=f"spawn:{type(exc).__name__}")
            self._trace(
                "container.log.stream.error",
                status="fail",
                container=container,
                error=f"{type(exc).__name__}: {exc}",
                cmd_preview=" ".join(cmd[:6]) + " ...",
            )
            return

        max_retries = 3
        retry_delay = 5
        for attempt in range(max_retries + 1):
            if attempt > 0:
                self._trace(
                    "container.log.stream.reconnect",
                    status="ok",
                    container=container,
                    attempt=attempt,
                )
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except Exception as exc:
                    self._record_error(container=container, message=f"reconnect:{type(exc).__name__}")
                    self._trace(
                        "container.log.stream.error",
                        status="fail",
                        container=container,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    return

            self._procs[container] = proc
            self._trace(
                "container.log.stream.ok",
                status="ok",
                container=container,
                pid=int(proc.pid or 0),
            )
            stderr_task = asyncio.create_task(self._consume_stream(container, proc.stderr, stream_name="stderr"))
            await self._consume_stream(container, proc.stdout, stream_name="stdout")
            rc = await proc.wait()
            with contextlib.suppress(Exception):
                await stderr_task

            if self._stop or rc == 0:
                break

            if attempt < max_retries:
                self._trace(
                    "container.log.stream.error",
                    status="fail",
                    container=container,
                    returncode=int(rc),
                    retrying=True,
                )
                await asyncio.sleep(retry_delay)
            else:
                self._record_error(container=container, message=f"returncode:{int(rc)}")
                self._trace(
                    "container.log.stream.error",
                    status="fail",
                    container=container,
                    returncode=int(rc),
                )

    async def _consume_stream(self, container: str, stream, *, stream_name: str) -> None:
        if stream is None:
            return
        while not self._stop:
            line = await stream.readline()
            if not line:
                break
            raw = line.decode("utf-8", errors="replace").rstrip("\r\n")
            source_ts, payload = _extract_docker_log_timestamp(raw)
            sanitized = sanitize_container_log_line(payload, max_chars=self._max_line_chars)
            if not sanitized:
                continue
            self._seq += 1
            self._line_count += 1
            self._last_activity_monotonic = time.monotonic()
            if container in self._ring:
                self._ring[container].append(sanitized)
            self._trace(
                "container.log.line",
                status="ok",
                container=container,
                stream=stream_name,
                stream_seq=self._seq,
                source_ts=source_ts,
                line_preview=sanitized,
            )

    def has_recent_activity(self, *, within_seconds: float) -> bool:
        return (time.monotonic() - self._last_activity_monotonic) <= max(0.5, float(within_seconds))

    def bundle(self) -> dict[str, list[str]]:
        return {container: list(lines) for container, lines in self._ring.items()}

    def has_errors(self) -> bool:
        return bool(self._errors)

    def error_tail(self) -> list[str]:
        return list(self._errors)


class LiveContainerDiagnostics:
    def __init__(
        self,
        *,
        trace_fn: Callable[..., None],
        config_override: dict[str, Any] | None = None,
        since_utc_iso: str | None = None,
    ) -> None:
        self._trace = trace_fn
        self._config = _normalize_container_log_config(config_override)
        self._since = since_utc_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._streamer: ContainerLogStreamer | None = None

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._config)

    def get_tail_lines(self, container: str) -> int:
        overrides = dict(self._config.get("tail_overrides") or {})
        if container in overrides:
            return max(1, int(overrides[container]))
        return max(1, int(self._config.get("tail_default") or 100))

    async def start(self) -> None:
        if not self._config["sources"]:
            self._trace("container.log.stream.disabled", status="skip", reason="no_sources")
            return
        if not self._config["stream_enabled"]:
            self._trace(
                "container.log.stream.disabled",
                status="skip",
                reason="config_disabled",
                containers=self._config["sources"],
            )
            return
        self._streamer = ContainerLogStreamer(
            trace_fn=self._trace,
            since_utc_iso=self._since,
            containers=list(self._config["sources"]),
            max_line_chars=int(self._config["max_line_chars"]),
            ring_lines=int(self._config["ring_lines"]),
            ssh_profile=str(self._config.get("ssh_profile") or ""),
        )
        try:
            await self._streamer.start()
            await asyncio.sleep(1.0)
            if self._config["require_stream"] and self._streamer.has_errors():
                summary = container_log_error_summary(self._streamer)
                raise AssertionError(f"CONTAINER_LOG_STREAM_UNAVAILABLE: {summary}")
        except Exception as exc:
            self._trace(
                "container.log.stream.error",
                status="fail",
                error=f"{type(exc).__name__}: {exc}",
            )
            if self._config["require_stream"]:
                raise

    async def stop(self) -> None:
        if self._streamer is not None:
            await self._streamer.stop()

    def has_errors(self) -> bool:
        return bool(self._streamer and self._streamer.has_errors())

    def has_recent_activity(self, *, within_seconds: float) -> bool:
        return bool(self._streamer and self._streamer.has_recent_activity(within_seconds=within_seconds))

    def bundle(self) -> dict[str, list[str]]:
        if self._streamer is None:
            return {}
        return self._streamer.bundle()

    def error_tail(self) -> list[str]:
        if self._streamer is None:
            return []
        return self._streamer.error_tail()

    async def emit_bundle(self, *, status: str, reason: str, **fields: Any) -> dict[str, list[str]]:
        tails, capture_errors = await self._capture_snapshot_bundle()
        payload: dict[str, Any] = {
            "status": status,
            "reason": reason,
            "tails": tails,
            "capture_status": "ok" if not capture_errors else "fail",
        }
        payload.update(fields)
        stream_tails = self.bundle()
        if stream_tails:
            payload["stream_tails"] = stream_tails
        stream_errors = container_log_error_summary(self)
        if stream_errors:
            payload["stream_errors"] = stream_errors
        if capture_errors:
            payload["capture_errors"] = capture_errors
        self._trace("container.log.bundle", **payload)
        return tails

    async def _capture_snapshot_bundle(self) -> tuple[dict[str, list[str]], list[str]]:
        if not self._config["sources"]:
            return {}, []
        ssh = resolve_container_log_stream_ssh(str(self._config.get("ssh_profile") or ""))
        if not ssh:
            return {}, ["missing_ssh_credentials"]
        key_path = Path(str(ssh.get("key") or "").strip())
        if not key_path.exists():
            return {}, [f"missing_key:{key_path}"]

        tails: dict[str, list[str]] = {}
        errors: list[str] = []
        for container in list(self._config["sources"]):
            tail_lines = self.get_tail_lines(container)
            remote_cmd = _build_snapshot_remote_cmd(container, tail_lines)
            cmd = _build_ssh_command(ssh, remote_cmd)
            try:
                rc, stdout, stderr = await _run_capture_command(cmd, timeout_s=30.0)
            except asyncio.TimeoutError:
                errors.append(f"{container}:timeout")
                continue
            except Exception as exc:
                errors.append(f"{container}:{type(exc).__name__}")
                continue
            if rc != 0:
                detail = stderr.strip() or stdout.strip() or f"returncode:{rc}"
                errors.append(f"{container}:{detail[:120]}")
                continue
            lines: list[str] = []
            for raw in stdout.splitlines():
                source_ts, payload = _extract_docker_log_timestamp(raw)
                sanitized = sanitize_container_log_line(payload, max_chars=int(self._config["max_line_chars"]))
                if not sanitized:
                    continue
                lines.append(f"{source_ts} {sanitized}".strip())
            tails[container] = lines[-tail_lines:]
        return tails, errors
