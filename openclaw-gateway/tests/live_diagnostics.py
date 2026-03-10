from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import re
import shlex
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import aiohttp

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


def _get_config_module() -> Any:
    return importlib.import_module("config")


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


async def fetch_local_gateway_status(*, url: str, timeout_seconds: int = 10) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=max(1, int(timeout_seconds)))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            payload = await resp.json()
            return dict(payload) if isinstance(payload, dict) else {}


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


def _preflight_missing_agents(status_payload: dict[str, Any], required_agents: list[str]) -> list[str]:
    coding_agents = status_payload.get("coding_agents") or {}
    available = {str(name).strip().lower() for name in dict(coding_agents).keys()}
    missing: list[str] = []
    for raw in required_agents:
        agent = str(raw or "").strip().lower()
        if agent and agent not in available:
            missing.append(agent)
    return missing


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
    )

    container_log_cfg = dict(policy.get("container_log") or {})
    diagnostics_profile = str(policy.get("diagnostics_profile") or container_log_cfg.get("ssh_profile") or "")
    ssh = resolve_container_log_stream_ssh(diagnostics_profile)
    if not ssh:
        raise AssertionError(
            "PREFLIGHT_DIAGNOSTICS_CONFIG_ERROR: missing SSH credentials for diagnostics profile "
            f"'{diagnostics_profile or 'auto'}'"
        )
    key_path = Path(str(ssh.get("key") or "").strip())
    if not key_path.exists():
        raise AssertionError(
            "PREFLIGHT_DIAGNOSTICS_CONFIG_ERROR: SSH key path does not exist "
            f"({key_path})"
        )

    if str(policy.get("status_probe_mode") or "") == "remote_container_http":
        status_payload = await fetch_remote_gateway_status(
            container_name=str(policy.get("remote_gateway_container") or "openclaw-gateway"),
            status_url=str(policy.get("remote_status_url") or "http://localhost:8766/status"),
            diagnostics_profile=diagnostics_profile,
        )
    else:
        status_payload = await fetch_local_gateway_status(
            url=str(local_status_url or f"http://127.0.0.1:{int(getattr(cfg, 'HTTP_PORT', 8766) or 8766)}/status"),
        )

    trace_fn(
        "preflight.status",
        flow=flow,
        primary_transport_mode=str(status_payload.get("primary_transport_mode") or ""),
        agent_connected=bool(status_payload.get("agent_connected", False)),
        websocket_health_ok=bool(status_payload.get("websocket_health_ok", False)),
        telegram_poller_state=str(status_payload.get("telegram_poller_state") or ""),
        telegram_poller_lock_healthy=bool(status_payload.get("telegram_poller_lock_healthy", False)),
        coding_agents=sorted(dict(status_payload.get("coding_agents") or {}).keys()),
    )

    required_transport = str(policy.get("required_transport") or "").strip().lower()
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

    missing_agents = _preflight_missing_agents(
        status_payload,
        list(policy.get("required_worker_agents") or []),
    )
    if missing_agents:
        raise AssertionError(
            "PREFLIGHT_REQUIRED_AGENTS_MISSING: " + ",".join(sorted(missing_agents))
        )

    if bool(policy.get("require_telegram_poller", False)):
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
            raise AssertionError(
                "CONTAINER_LOG_STREAM_UNAVAILABLE: missing SSH credentials "
                "(set SKYNET_E2E_CONTAINER_LOG_SSH_* or OPENCLAW_TUNNEL_EC2_HOST/USER/SSH_KEY)"
            )
        key_path = Path(str(self._ssh.get("key") or "").strip())
        if not key_path.exists():
            raise AssertionError(
                "CONTAINER_LOG_STREAM_UNAVAILABLE: SSH key path does not exist "
                f"({key_path})"
            )
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
