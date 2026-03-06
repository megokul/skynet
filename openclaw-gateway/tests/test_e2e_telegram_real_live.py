"""Fully real Telegram-network E2E (user account -> bot chat -> inline buttons)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable

import pytest

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency at runtime
    load_dotenv = None  # type: ignore[assignment]

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except Exception:  # pragma: no cover - optional dependency at runtime
    TelegramClient = None  # type: ignore[assignment]
    StringSession = None  # type: ignore[assignment]

_CONVERSATIONAL_REQUIREMENT = (
    "I'm building a small Windows Python script that runs from the terminal. "
    "When executed, it should show a popup saying \"hi\" and play a short beep sound. "
    "Use only Python standard library, include tests, and add a valid skynet_run.json."
)
_CONVERSATIONAL_RESTATEMENT = (
    "It is a Windows terminal Python script that pops up \"hi\" and plays a short beep on run, "
    "using only stdlib."
)
_CONTAINER_LOG_ENV_PREFIX = "SKYNET_E2E_CONTAINER_LOG_"


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_csv_env(name: str, default: str) -> list[str]:
    raw = (os.environ.get(name) or default).strip()
    out: list[str] = []
    for token in raw.split(","):
        item = token.strip()
        if item:
            out.append(item)
    return out


_AUTH_BEARER_PATTERN = re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+\S+")
_BARE_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|session|api[_-]?key|private[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"(?i)bot\d+:[A-Za-z0-9_-]{20,}")


def _sanitize_container_log_line(line: str, *, max_chars: int) -> str:
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


def _resolve_container_log_stream_ssh() -> dict[str, Any] | None:
    host = (
        (os.environ.get(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_HOST") or "").strip()
        or (os.environ.get("OPENCLAW_TUNNEL_EC2_HOST") or "").strip()
    )
    user = (
        (os.environ.get(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_USER") or "").strip()
        or (os.environ.get("OPENCLAW_TUNNEL_EC2_USER") or "").strip()
    )
    key_candidates = [
        ("e2e_override", (os.environ.get(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_KEY") or "").strip()),
        ("tunnel", (os.environ.get("OPENCLAW_TUNNEL_SSH_KEY") or "").strip()),
        ("ssh_fallback", (os.environ.get("OPENCLAW_SSH_KEY_PATH") or "").strip()),
    ]
    key_options = [(source, value) for source, value in key_candidates if value]
    key = ""
    key_source = ""
    for source, candidate in key_options:
        if Path(candidate).exists():
            key = candidate
            key_source = source
            break
    if not key and key_options:
        key_source, key = key_options[0]
    port = _env_int(f"{_CONTAINER_LOG_ENV_PREFIX}SSH_PORT", 22)
    if not host or not user or not key:
        return None
    return {
        "host": host,
        "user": user,
        "key": key,
        "key_source": key_source or "unknown",
        "port": max(1, port),
    }


class _RuntimeTraceProgress:
    def __init__(self) -> None:
        self.last_mtime_iso = ""
        self.last_line_count = 0
        self.last_progress_monotonic = time.monotonic()

    def observe(self, snapshot: dict[str, Any] | None) -> bool:
        if not isinstance(snapshot, dict):
            return False
        if str(snapshot.get("status") or "").strip().lower() != "ok":
            return False
        mtime_iso = str(snapshot.get("mtime_iso") or "").strip()
        try:
            line_count = int(snapshot.get("line_count") or 0)
        except Exception:
            line_count = 0
        progressed = (
            (mtime_iso and mtime_iso != self.last_mtime_iso)
            or (line_count > self.last_line_count)
        )
        if progressed:
            self.last_mtime_iso = mtime_iso
            self.last_line_count = line_count
            self.last_progress_monotonic = time.monotonic()
        return progressed

    def stale_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_progress_monotonic)


class _ContainerLogStreamer:
    def __init__(
        self,
        *,
        trace_fn: Callable[..., None],
        since_utc_iso: str,
        containers: list[str],
        max_line_chars: int,
        ring_lines: int,
    ) -> None:
        self._trace = trace_fn
        self._since = since_utc_iso
        self._containers = [c.strip() for c in containers if c.strip()]
        self._max_line_chars = max(200, int(max_line_chars))
        self._ring_lines = max(20, int(ring_lines))
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
        self._ssh = _resolve_container_log_stream_ssh()
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
        host = str(self._ssh.get("host") or "").strip()
        user = str(self._ssh.get("user") or "").strip()
        key = str(self._ssh.get("key") or "").strip()
        port = int(self._ssh.get("port") or 22)
        remote_cmd = f"docker logs -f --since {self._since} --timestamps {container}"
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
        ]
        if port != 22:
            cmd.extend(["-p", str(port)])
        if key:
            cmd.extend(["-i", key])
        cmd.extend([f"{user}@{host}", remote_cmd])
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
        if not self._stop and rc != 0:
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
            sanitized = _sanitize_container_log_line(payload, max_chars=self._max_line_chars)
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


def _load_live_env_from_dotenv() -> None:
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parents[2]
    candidates = []
    explicit = os.environ.get("SKYNET_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([repo_root / ".env", repo_root / "openclaw-gateway" / ".env"])
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
        except Exception:
            key = str(candidate).lower()
        if key in seen or not candidate.exists():
            continue
        seen.add(key)
        load_dotenv(candidate, override=False)


_load_live_env_from_dotenv()


def _make_live_trace_logger(test_name: str):
    env_path = os.environ.get("SKYNET_LIVE_TRACE_FILE", "").strip()
    if env_path:
        path = Path(env_path)
    else:
        repo_root = Path(__file__).resolve().parents[2]
        log_dir = repo_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{test_name}-{int(time.time())}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    def trace(event: str, **fields) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_s": round(time.monotonic() - started, 1),
            "event": event,
        }
        payload.update(fields)
        line = json.dumps(payload, ensure_ascii=True, default=str)
        print(line, flush=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    trace("trace.start", test_name=test_name, trace_file=str(path))
    return path, trace


def _resolve_runtime_trace_file() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    explicit = (os.environ.get("SKYNET_E2E_RUNTIME_TRACE_FILE") or "").strip()
    runtime_live_file = (os.environ.get("SKYNET_RUNTIME_TRACE_LIVE_FILE") or "").strip()
    mirror_dir = (os.environ.get("SKYNET_TRACE_MIRROR_LOG_DIR") or "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if runtime_live_file:
        candidates.append(Path(runtime_live_file))
    if mirror_dir:
        candidates.append(Path(mirror_dir) / "skynet.trace.log")
    candidates.extend(
        [
            repo_root / "logs" / "skynet.trace.log",
            repo_root / "openclaw-gateway" / "logs" / "skynet.trace.log",
        ]
    )
    if explicit:
        explicit_path = Path(explicit)
        if explicit_path.exists():
            return explicit_path
        return explicit_path
    existing: list[Path] = []
    for candidate in candidates:
        if candidate.exists():
            existing.append(candidate)
    if existing:
        # Prefer the freshest trace source to avoid pinning to stale mirror files.
        existing.sort(
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        return existing[0]
    return None


def _emit_runtime_trace_snapshot(
    trace_fn: Callable[..., None],
    *,
    checkpoint: str,
    tail_lines: int = 120,
) -> dict[str, Any]:
    path = _resolve_runtime_trace_file()
    if path is None:
        payload = {
            "checkpoint": checkpoint,
            "status": "missing",
            "reason": "trace file not found",
        }
        trace_fn(
            "runtime.trace.snapshot",
            **payload,
        )
        return payload
    if not path.exists():
        payload = {
            "checkpoint": checkpoint,
            "status": "missing",
            "trace_file": str(path),
            "reason": "path does not exist",
        }
        trace_fn(
            "runtime.trace.snapshot",
            **payload,
        )
        return payload
    try:
        tail = deque(maxlen=max(1, tail_lines))
        line_count = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line_count += 1
                tail.append(line.rstrip("\n"))
        joined = "\n".join(tail)
        digest = sha1(joined.encode("utf-8", errors="replace")).hexdigest()
        preview = joined[-2500:]
        stat = path.stat()
        mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        mtime_iso = mtime_dt.isoformat(timespec="seconds")
        age_s = round(max(0.0, time.time() - stat.st_mtime), 1)
        payload = {
            "checkpoint": checkpoint,
            "status": "ok",
            "trace_file": str(path),
            "lines": len(tail),
            "line_count": line_count,
            "digest": digest,
            "mtime_iso": mtime_iso,
            "age_s": age_s,
            "preview": preview,
        }
        trace_fn(
            "runtime.trace.snapshot",
            **payload,
        )
        return payload
    except Exception as exc:
        payload = {
            "checkpoint": checkpoint,
            "status": "error",
            "trace_file": str(path),
            "error": f"{type(exc).__name__}: {exc}",
        }
        trace_fn(
            "runtime.trace.snapshot",
            **payload,
        )
        return payload


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise AssertionError(f"Missing required env: {name}")
    return value


def _button_texts(message) -> list[str]:
    rows = getattr(message, "buttons", None) or []
    out: list[str] = []
    for row in rows:
        for btn in row:
            text = str(getattr(btn, "text", "")).strip()
            if text:
                out.append(text)
    return out


async def _wait_for_bot_message(
    client,
    bot_entity,
    after_id: int,
    *,
    timeout_s: int,
    trace_fn: Callable[..., None],
    step: str,
    predicate: Callable[[str, list[str]], bool],
) -> object:
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    poll_count = 0
    last_seen_id = after_id
    while time.monotonic() < deadline:
        msgs = await client.get_messages(bot_entity, limit=20)
        poll_count += 1
        for msg in reversed(msgs):
            if int(getattr(msg, "id", 0)) <= after_id:
                continue
            if bool(getattr(msg, "out", False)):
                continue
            last_seen_id = max(last_seen_id, int(getattr(msg, "id", 0)))
            text = str(getattr(msg, "message", "") or "")
            btns = _button_texts(msg)
            if predicate(text, btns):
                trace_fn(
                    "telegram.wait.match",
                    step=step,
                    message_id=int(getattr(msg, "id", 0)),
                    text_preview=text[:220],
                    buttons=btns,
                    polls=poll_count,
                    waited_s=round(time.monotonic() - started, 1),
                )
                return msg
        if poll_count % 10 == 0:
            trace_fn(
                "telegram.waiting",
                step=step,
                polls=poll_count,
                waited_s=round(time.monotonic() - started, 1),
                after_id=after_id,
                last_seen_id=last_seen_id,
            )
        await asyncio.sleep(1.0)
    trace_fn(
        "telegram.wait.timeout",
        step=step,
        timeout_s=timeout_s,
        after_id=after_id,
        last_seen_id=last_seen_id,
        polls=poll_count,
    )
    raise AssertionError("Timed out waiting for expected bot message.")


async def _click_button_contains(message, needle: str, *, trace_fn: Callable[..., None], step: str) -> str:
    rows = getattr(message, "buttons", None) or []
    for i, row in enumerate(rows):
        for j, btn in enumerate(row):
            text = str(getattr(btn, "text", "")).strip()
            if needle.lower() in text.lower():
                await message.click(i, j)
                trace_fn(
                    "telegram.button.clicked",
                    step=step,
                    text=text,
                    row=i,
                    col=j,
                    message_id=int(getattr(message, "id", 0)),
                )
                return text
    trace_fn(
        "telegram.button.missing",
        step=step,
        needle=needle,
        available=_button_texts(message),
        message_id=int(getattr(message, "id", 0)),
    )
    raise AssertionError(f"Could not find button containing '{needle}'.")


async def _request_trace_deep_snapshot(
    *,
    client,
    bot,
    after_id: int,
    trace_fn: Callable[..., None],
) -> int:
    try:
        await client.send_message(bot, "/trace deep")
        trace_fn("telegram.message.sent", step="trace_deep.requested", text="/trace deep")
        msg = await _wait_for_bot_message(
            client,
            bot,
            after_id,
            timeout_s=60,
            trace_fn=trace_fn,
            step="await_trace_deep_response",
            predicate=lambda text, _btns: bool(text.strip()),
        )
        response_id = int(getattr(msg, "id", 0))
        response_text = str(getattr(msg, "message", "") or "")
        trace_fn(
            "trace.deep.response",
            message_id=response_id,
            text_preview=response_text[:320],
        )
        return max(after_id, response_id)
    except Exception as exc:
        trace_fn(
            "trace.deep.error",
            error=f"{type(exc).__name__}: {exc}",
        )
        return after_id


async def _poll_tracker_message_edit(
    *,
    client,
    bot,
    tracker_message_id: int | None,
    tracker_last_text: str,
    tracker_edit_count: int,
    trace_fn: Callable[..., None],
) -> tuple[str, int, bool]:
    if tracker_message_id is None:
        return tracker_last_text, tracker_edit_count, False
    try:
        tracker_msg = await client.get_messages(bot, ids=tracker_message_id)
        current_tracker_text = str(getattr(tracker_msg, "message", "") or "")
    except Exception:
        return tracker_last_text, tracker_edit_count, False
    if not current_tracker_text or current_tracker_text == tracker_last_text:
        return tracker_last_text, tracker_edit_count, False
    tracker_edit_count += 1
    tracker_last_text = current_tracker_text
    trace_fn(
        "tracker.message.edited",
        message_id=tracker_message_id,
        edits=tracker_edit_count,
        text_preview=current_tracker_text[:220],
    )
    return tracker_last_text, tracker_edit_count, True


def _container_stream_error_summary(streamer: _ContainerLogStreamer | None) -> str:
    if streamer is None:
        return ""
    tail = streamer.error_tail()
    if not tail:
        return ""
    return " | ".join(tail[-5:])


def _resolve_worker_projects_dir() -> Path:
    candidates = [
        os.environ.get("OPENCLAW_PROJECT_BASE_DIR", "").strip(),
        os.environ.get("WORKER_PROJECTS_DIR", "").strip(),
        os.environ.get("SKYNET_PROJECT_BASE_DIR", "").strip(),
        "C:/Projects",
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if path.exists():
            return path
    for raw in candidates:
        if raw:
            return Path(raw)
    return Path("C:/Projects")


def _is_safe_relative_path(path: str) -> bool:
    norm = path.replace("\\", "/").strip()
    if not norm:
        return False
    if norm.startswith("/") or ":" in norm:
        return False
    return ".." not in norm.split("/")


def _validate_generated_project_artifacts(*, project_slug: str, trace_fn: Callable[..., None]) -> None:
    base_dir = _resolve_worker_projects_dir()
    project_dir = base_dir / project_slug
    if not project_dir.exists():
        raise AssertionError(
            f"Generated project folder not found: {project_dir} "
            "(set OPENCLAW_PROJECT_BASE_DIR/WORKER_PROJECTS_DIR for this test host)"
        )

    py_files = sorted(project_dir.rglob("*.py"))
    if not py_files:
        raise AssertionError(f"No Python files found in generated project: {project_dir}")

    popup_markers = ("messageboxw", "tkinter", "messagebox")
    beep_markers = ("winsound.beep", "winsound.messagebeep", "winsound.playsound")
    popup_detected = False
    beep_detected = False

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8", errors="ignore").lower()
        if any(marker in content for marker in popup_markers):
            popup_detected = True
        if any(marker in content for marker in beep_markers):
            beep_detected = True

    run_contract_path = project_dir / "skynet_run.json"
    run_contract_valid = False
    run_contract_summary = "missing"
    if run_contract_path.exists():
        try:
            contract = json.loads(run_contract_path.read_text(encoding="utf-8"))
            if isinstance(contract, dict):
                interpreter = str(contract.get("interpreter") or "").strip().lower()
                entrypoint = str(contract.get("entrypoint") or "").strip()
                if interpreter in {"python", "python3"} and _is_safe_relative_path(entrypoint):
                    entrypoint_norm = Path(*entrypoint.replace("\\", "/").split("/"))
                    if (project_dir / entrypoint_norm).exists():
                        run_contract_valid = True
                        run_contract_summary = f"{interpreter}:{entrypoint}"
                    else:
                        run_contract_summary = f"entrypoint_missing:{entrypoint}"
                else:
                    run_contract_summary = "invalid_contract_fields"
            else:
                run_contract_summary = "contract_not_object"
        except Exception as exc:
            run_contract_summary = f"json_error:{type(exc).__name__}"

    trace_fn(
        "artifact.validation",
        project_dir=str(project_dir),
        py_files_count=len(py_files),
        popup_detected=popup_detected,
        beep_detected=beep_detected,
        run_contract_valid=run_contract_valid,
        run_contract_summary=run_contract_summary,
    )

    if not popup_detected:
        raise AssertionError("Missing popup implementation evidence")
    if not beep_detected:
        raise AssertionError("Missing beep implementation evidence")
    if not run_contract_valid:
        raise AssertionError("Missing/invalid skynet_run.json")


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_telegram_chat_flow_no_github_repo_creation() -> None:
    trace_path, trace = _make_live_trace_logger("telegram-real-live-e2e")
    if os.environ.get("SKYNET_E2E_LIVE") != "1":
        trace("test.skip", reason="SKYNET_E2E_LIVE is not 1")
        pytest.skip("Set SKYNET_E2E_LIVE=1 to run live Telegram E2E.")
    if TelegramClient is None or StringSession is None:
        trace("test.skip", reason="Telethon dependency missing")
        pytest.skip("Telethon is not installed. Install with: pip install telethon")

    required_env = (
        "SKYNET_E2E_TELEGRAM_API_ID",
        "SKYNET_E2E_TELEGRAM_API_HASH",
        "SKYNET_E2E_TELEGRAM_SESSION",
        "SKYNET_E2E_TELEGRAM_BOT_USERNAME",
    )
    missing_env = [name for name in required_env if not (os.environ.get(name) or "").strip()]
    if missing_env:
        trace("telegram_real.env.missing", missing=missing_env)
        raise AssertionError(f"Missing required env: {', '.join(missing_env)}")

    api_id = int(_require_env("SKYNET_E2E_TELEGRAM_API_ID"))
    api_hash = _require_env("SKYNET_E2E_TELEGRAM_API_HASH")
    session = _require_env("SKYNET_E2E_TELEGRAM_SESSION")
    bot_username = _require_env("SKYNET_E2E_TELEGRAM_BOT_USERNAME")
    stream_enabled = _env_bool(f"{_CONTAINER_LOG_ENV_PREFIX}STREAM_ENABLED", True)
    stream_required = _env_bool(f"{_CONTAINER_LOG_ENV_PREFIX}REQUIRE_STREAM", True)
    stream_sources = _parse_csv_env(
        f"{_CONTAINER_LOG_ENV_PREFIX}SOURCES",
        "openclaw-gateway,skynet-api",
    )
    stream_max_line_chars = _env_int(f"{_CONTAINER_LOG_ENV_PREFIX}MAX_LINE_CHARS", 1200)
    stream_ring_lines = _env_int(f"{_CONTAINER_LOG_ENV_PREFIX}RING_LINES", 300)
    runtime_stale_seconds = max(30, _env_int("SKYNET_E2E_RUNTIME_TRACE_STALE_SECONDS", 90))
    runtime_progress = _RuntimeTraceProgress()
    trace(
        "test.start",
        bot_username=bot_username,
        flow="hi_to_project_completion",
        container_stream_enabled=stream_enabled,
        container_stream_required=stream_required,
        container_stream_sources=stream_sources,
        runtime_trace_stale_seconds=runtime_stale_seconds,
    )
    runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="test.start", tail_lines=80))

    project_slug = f"livee2e{int(time.time())}"
    requirement = _CONVERSATIONAL_REQUIREMENT
    trace("test.input", project_slug=project_slug, requirement_preview=requirement[:220])
    trace("test.requirement.payload", payload=requirement)

    async with TelegramClient(StringSession(session), api_id, api_hash) as client:
        container_streamer: _ContainerLogStreamer | None = None
        trace("telegram.client.connected")
        bot = await client.get_entity(bot_username)
        history = await client.get_messages(bot, limit=1)
        last_id = int(history[0].id) if history else 0
        trace("telegram.history", last_message_id=last_id)
        if stream_enabled:
            container_streamer = _ContainerLogStreamer(
                trace_fn=trace,
                since_utc_iso=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                containers=stream_sources,
                max_line_chars=stream_max_line_chars,
                ring_lines=stream_ring_lines,
            )
            try:
                await container_streamer.start()
                await asyncio.sleep(1.0)
                if stream_required and container_streamer.has_errors():
                    summary = _container_stream_error_summary(container_streamer)
                    trace(
                        "container.log.stream.error",
                        status="fail",
                        error=f"CONTAINER_LOG_STREAM_UNAVAILABLE: {summary}",
                    )
                    raise AssertionError(
                        f"CONTAINER_LOG_STREAM_UNAVAILABLE: {summary}"
                    )
            except Exception as exc:
                trace(
                    "container.log.stream.error",
                    status="fail",
                    error=f"{type(exc).__name__}: {exc}",
                )
                if stream_required:
                    raise
        else:
            trace("container.log.stream.disabled", status="skip")
        trace("e2e.step.start", step=1, name="send_hi")
        await client.send_message(bot, "hi")
        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            trace_fn=trace,
            step="menu_after_hi",
            predicate=lambda text, btns: any("start a project" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Start a Project", trace_fn=trace, step="click_start_project")
        runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="after.start_project", tail_lines=60))
        trace("e2e.step.end", step=1, name="send_hi", status="ok")

        trace("e2e.step.start", step=2, name="project_name")
        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            trace_fn=trace,
            step="await_project_name_prompt",
            predicate=lambda text, _btns: "what should we call this project" in text.lower(),
        )
        last_id = int(msg.id)
        await client.send_message(bot, project_slug)
        trace("telegram.message.sent", step="project_name_sent", text=project_slug)
        trace("e2e.step.end", step=2, name="project_name", status="ok")

        trace("e2e.step.start", step=3, name="project_type")
        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            trace_fn=trace,
            step="await_project_type_prompt",
            predicate=lambda _text, btns: any("python app" in b.lower() for b in btns) or any("other" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        if any("python app" in b.lower() for b in _button_texts(msg)):
            await _click_button_contains(msg, "Python App", trace_fn=trace, step="click_python_app")
        else:
            await _click_button_contains(msg, "Other", trace_fn=trace, step="click_other_type")
        trace("e2e.step.end", step=3, name="project_type", status="ok")

        trace("e2e.step.start", step=4, name="requirements")
        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            trace_fn=trace,
            step="await_requirements_prompt",
            predicate=lambda text, btns: (
                "what are you building" in text.lower()
                or "what does this app do" in text.lower()
                or any("generate plan" in b.lower() for b in btns)
            ),
        )
        last_id = int(msg.id)
        await client.send_message(bot, requirement)
        trace("telegram.message.sent", step="requirements_sent", text_preview=requirement[:220])
        runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="after.requirements_sent", tail_lines=80))
        trace("e2e.step.end", step=4, name="requirements", status="ok")

        trace("e2e.step.start", step=5, name="generate_plan")
        plan_msg = None
        max_rounds = 3
        for round_idx in range(1, max_rounds + 1):
            msg = await _wait_for_bot_message(
                client,
                bot,
                last_id,
                timeout_s=240,
                trace_fn=trace,
                step=f"await_plan_flow_round_{round_idx}",
                predicate=lambda text, btns: bool(text.strip()) or bool(btns),
            )
            last_id = int(msg.id)
            text = str(getattr(msg, "message", "") or "")
            lowered = text.lower()
            btns = _button_texts(msg)

            if any("approve" in b.lower() for b in btns):
                plan_msg = msg
                trace(
                    "planner.approve.ready",
                    round=round_idx,
                    message_id=last_id,
                    text_preview=text[:220],
                )
                break

            if any("generate plan" in b.lower() for b in btns):
                await _click_button_contains(
                    msg,
                    "Generate Plan",
                    trace_fn=trace,
                    step=f"click_generate_plan_round_{round_idx}",
                )
                continue

            needs_clarification = any(
                marker in lowered
                for marker in (
                    "what are you building",
                    "describe",
                    "clarify",
                    "confirm",
                    "2-3 sentences",
                )
            )
            if needs_clarification:
                trace(
                    "planner.clarification.detected",
                    round=round_idx,
                    message_id=last_id,
                    text_preview=text[:220],
                )
                await client.send_message(bot, _CONVERSATIONAL_RESTATEMENT)
                trace(
                    "planner.requirement.resubmitted",
                    round=round_idx,
                    text_preview=_CONVERSATIONAL_RESTATEMENT[:220],
                )
                continue

        if plan_msg is None:
            trace(
                "e2e.step.fail",
                step=5,
                name="generate_plan",
                status="fail",
                error_message="Planner clarification loop exhausted before plan approval",
            )
            raise AssertionError("Planner clarification loop exhausted before plan approval")
        trace("e2e.step.end", step=5, name="generate_plan", status="ok")

        trace("e2e.step.start", step=6, name="approve_plan")
        msg = plan_msg
        await _click_button_contains(msg, "Approve", trace_fn=trace, step="click_plan_approve")
        runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="after.plan_approved", tail_lines=100))
        trace("e2e.step.end", step=6, name="approve_plan", status="ok")

        trace("e2e.step.start", step=7, name="start_coding")
        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=180,
            trace_fn=trace,
            step="await_start_coding_button",
            predicate=lambda _text, btns: any("start coding" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Start Coding", trace_fn=trace, step="click_start_coding")
        runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="after.start_coding_clicked", tail_lines=120))
        trace("e2e.step.end", step=7, name="start_coding", status="ok")

        trace("e2e.step.start", step=8, name="skip_github_repo_creation")
        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=180,
            trace_fn=trace,
            step="await_skip_github_button",
            predicate=lambda _text, btns: any("skip" in b.lower() and "start coding" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Skip", trace_fn=trace, step="click_skip_github")
        runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="after.skip_github", tail_lines=120))
        trace("e2e.step.end", step=8, name="skip_github_repo_creation", status="ok")

        trace("e2e.step.start", step=9, name="coding_and_run")
        saw_run_button = False
        saw_finish_summary = False
        saw_no_github_push = True
        saw_run_success = False
        saw_director_phase = False
        saw_architect_phase = False
        saw_worker_assignment_marker = False
        complete_count: int | None = None
        failed_count: int | None = None
        tracker_message_id: int | None = None
        tracker_last_text = ""
        tracker_edit_count = 0
        preflight_fail_markers = (
            "coding preflight failed",
            "no control-plane coding agents available",
            "no coding agents available for chain",
            "codex_write_blocked",
            "generation_failed: codex",
        )

        for idx in range(80):
            try:
                msg = await _wait_for_bot_message(
                    client,
                    bot,
                    last_id,
                    timeout_s=90,
                    trace_fn=trace,
                    step=f"coding_poll_{idx + 1}",
                    predicate=lambda text, btns: bool(text.strip()) or bool(btns),
                )
            except AssertionError:
                tracker_last_text, tracker_edit_count, tracker_progress = await _poll_tracker_message_edit(
                    client=client,
                    bot=bot,
                    tracker_message_id=tracker_message_id,
                    tracker_last_text=tracker_last_text,
                    tracker_edit_count=tracker_edit_count,
                    trace_fn=trace,
                )
                if stream_required and container_streamer is not None and container_streamer.has_errors():
                    summary = _container_stream_error_summary(container_streamer)
                    trace(
                        "container.log.bundle",
                        status="fail",
                        reason="container_stream_unhealthy",
                        stream_errors=summary,
                        tails=container_streamer.bundle(),
                    )
                    raise AssertionError(f"CONTAINER_LOG_STREAM_UNAVAILABLE: {summary}")
                if tracker_progress:
                    lowered_tracker = tracker_last_text.lower()
                    if "phase: director" in lowered_tracker:
                        saw_director_phase = True
                    if "phase: architect" in lowered_tracker:
                        saw_architect_phase = True
                    if "worker=" in lowered_tracker or "worker:" in lowered_tracker:
                        saw_worker_assignment_marker = True
                timeout_snapshot = _emit_runtime_trace_snapshot(
                    trace,
                    checkpoint=f"coding_poll_timeout_{idx + 1}",
                    tail_lines=140,
                )
                trace_progress = runtime_progress.observe(timeout_snapshot)
                container_progress = bool(
                    container_streamer
                    and container_streamer.has_recent_activity(within_seconds=max(20, runtime_stale_seconds / 2.0))
                )
                stale_s = runtime_progress.stale_seconds()
                if tracker_progress or trace_progress or container_progress:
                    trace(
                        "coding.poll.recovered",
                        iteration=idx + 1,
                        tracker_progress=tracker_progress,
                        trace_progress=trace_progress,
                        container_progress=container_progress,
                        runtime_trace_stale_s=round(stale_s, 1),
                    )
                    continue
                last_id = await _request_trace_deep_snapshot(
                    client=client,
                    bot=bot,
                    after_id=last_id,
                    trace_fn=trace,
                )
                if stale_s >= runtime_stale_seconds:
                    if container_streamer is not None:
                        trace(
                            "container.log.bundle",
                            status="fail",
                            reason="trace_stale_timeout",
                            tails=container_streamer.bundle(),
                        )
                    trace(
                        "e2e.step.fail",
                        step=9,
                        name="coding_and_run",
                        status="fail",
                        error_message=(
                            f"TRACE_STALE: runtime trace idle {round(stale_s, 1)}s "
                            f"(threshold={runtime_stale_seconds}s)"
                        ),
                    )
                    raise AssertionError(
                        f"TRACE_STALE: runtime trace idle {round(stale_s, 1)}s "
                        f"(threshold={runtime_stale_seconds}s)"
                    )
                if container_streamer is not None:
                    trace(
                        "container.log.bundle",
                        status="fail",
                        reason="coding_poll_timeout",
                        tails=container_streamer.bundle(),
                    )
                raise AssertionError("Coding poll timed out without progress signals")
            last_id = int(msg.id)
            text = str(getattr(msg, "message", "") or "")
            btns = _button_texts(msg)
            trace(
                "telegram.message.received",
                step="coding_loop",
                iteration=idx + 1,
                message_id=last_id,
                text_preview=text[:220],
                buttons=btns,
            )
            if stream_required and container_streamer is not None and container_streamer.has_errors():
                summary = _container_stream_error_summary(container_streamer)
                trace(
                    "container.log.bundle",
                    status="fail",
                    reason="container_stream_unhealthy",
                    stream_errors=summary,
                    tails=container_streamer.bundle(),
                )
                raise AssertionError(f"CONTAINER_LOG_STREAM_UNAVAILABLE: {summary}")
            if idx == 0 or (idx + 1) % 5 == 0:
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(
                        trace,
                        checkpoint=f"coding_poll_{idx + 1}",
                        tail_lines=80,
                    )
                )
            stale_s = runtime_progress.stale_seconds()
            if stale_s >= runtime_stale_seconds:
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(
                        trace,
                        checkpoint=f"coding.trace_stale.{idx + 1}",
                        tail_lines=180,
                    )
                )
                if container_streamer is not None:
                    trace(
                        "container.log.bundle",
                        status="fail",
                        reason="runtime_trace_stale_during_coding",
                        tails=container_streamer.bundle(),
                    )
                trace(
                    "e2e.step.fail",
                    step=9,
                    name="coding_and_run",
                    status="fail",
                    error_message=(
                        f"TRACE_STALE: runtime trace idle {round(stale_s, 1)}s "
                        f"(threshold={runtime_stale_seconds}s)"
                    ),
                )
                raise AssertionError(
                    f"TRACE_STALE: runtime trace idle {round(stale_s, 1)}s "
                    f"(threshold={runtime_stale_seconds}s)"
                )
            lowered = text.lower()
            if "phase: director" in lowered:
                saw_director_phase = True
            if "phase: architect" in lowered:
                saw_architect_phase = True
            if "worker=" in lowered or "worker:" in lowered:
                saw_worker_assignment_marker = True

            if any(marker in lowered for marker in preflight_fail_markers):
                trace(
                    "coding.preflight.failure",
                    message_id=last_id,
                    text_preview=text[:320],
                )
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(trace, checkpoint="coding.preflight.failure", tail_lines=200)
                )
                trace(
                    "e2e.step.fail",
                    step=9,
                    name="coding_and_run",
                    status="fail",
                    error_message=f"Terminal preflight failure: {text[:260]}",
                )
                raise AssertionError(
                    f"Live Telegram E2E encountered terminal preflight failure: {text[:260]}"
                )

            if "session failed" in lowered and "complete=" in lowered:
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(trace, checkpoint="coding.session.failed", tail_lines=220)
                )
                trace(
                    "e2e.step.fail",
                    step=9,
                    name="coding_and_run",
                    status="fail",
                    error_message=f"Session summary indicates failure: {text[:260]}",
                )
                raise AssertionError(
                    f"Live Telegram E2E reached failed session summary: {text[:260]}"
                )

            if "coding progress [" in lowered:
                if tracker_message_id is None:
                    tracker_message_id = int(getattr(msg, "id", 0))
                    tracker_last_text = text
                    trace(
                        "tracker.message.detected",
                        message_id=tracker_message_id,
                        text_preview=text[:220],
                    )
                elif tracker_message_id == int(getattr(msg, "id", 0)) and text != tracker_last_text:
                    tracker_edit_count += 1
                    tracker_last_text = text
                    trace(
                        "tracker.message.edited",
                        message_id=tracker_message_id,
                        edits=tracker_edit_count,
                        text_preview=text[:220],
                    )

            tracker_last_text, tracker_edit_count, tracker_progress = await _poll_tracker_message_edit(
                client=client,
                bot=bot,
                tracker_message_id=tracker_message_id,
                tracker_last_text=tracker_last_text,
                tracker_edit_count=tracker_edit_count,
                trace_fn=trace,
            )
            if tracker_progress:
                lowered_tracker = tracker_last_text.lower()
                if "phase: director" in lowered_tracker:
                    saw_director_phase = True
                if "phase: architect" in lowered_tracker:
                    saw_architect_phase = True
                if "worker=" in lowered_tracker or "worker:" in lowered_tracker:
                    saw_worker_assignment_marker = True

            if "github repo created and pushed" in lowered:
                saw_no_github_push = False
                break

            if any("run it" in b.lower() for b in btns):
                await _click_button_contains(msg, "Run It", trace_fn=trace, step="click_milestone_run_it")
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(
                        trace,
                        checkpoint=f"milestone.run_it.clicked.{idx + 1}",
                        tail_lines=120,
                    )
                )
                continue

            if "session finished" in lowered or "complete=" in lowered:
                saw_finish_summary = True
                if "complete=" in lowered:
                    try:
                        complete_count = int(lowered.split("complete=", 1)[1].split(",", 1)[0].strip())
                    except Exception:
                        complete_count = complete_count
                if "failed=" in lowered:
                    try:
                        failed_count = int(lowered.split("failed=", 1)[1].split(",", 1)[0].strip())
                    except Exception:
                        failed_count = failed_count
                if complete_count is not None and complete_count < 1:
                    trace(
                        "coding.session.no_completion",
                        complete_count=complete_count,
                        failed_count=failed_count,
                        text_preview=text[:220],
                    )
                    break

            if any("run project" in b.lower() for b in btns):
                saw_run_button = True
                await _click_button_contains(msg, "Run Project", trace_fn=trace, step="click_run_project")
                run_msg = await _wait_for_bot_message(
                    client,
                    bot,
                    last_id,
                    timeout_s=240,
                    trace_fn=trace,
                    step="await_run_project_output",
                    predicate=lambda t, _b: "exit" in t.lower() or "finished" in t.lower(),
                )
                last_id = int(run_msg.id)
                run_text = str(getattr(run_msg, "message", "") or "")
                trace(
                    "run_project.output",
                    text_preview=run_text[:320],
                )
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(trace, checkpoint="run_project.output", tail_lines=220)
                )
                if "exit 0" in run_text.lower() or "finished (exit 0)" in run_text.lower():
                    saw_run_success = True
                break

        if saw_run_success:
            _validate_generated_project_artifacts(project_slug=project_slug, trace_fn=trace)
            runtime_progress.observe(
                _emit_runtime_trace_snapshot(trace, checkpoint="artifact.validation.ok", tail_lines=160)
            )

        try:
            assert saw_no_github_push, "Live Telegram E2E unexpectedly created/pushed a GitHub repo."
            assert tracker_message_id is not None, "Live Telegram E2E did not observe tracker message."
            assert tracker_edit_count >= 1, "Live Telegram E2E did not observe tracker edits."
            assert saw_director_phase or "arch=" in tracker_last_text.lower(), (
                "Live Telegram E2E did not observe director/architecture tracker phase."
            )
            assert saw_architect_phase or "arch=" in tracker_last_text.lower(), (
                "Live Telegram E2E did not observe architect/architecture tracker phase."
            )
            assert saw_worker_assignment_marker or "worker=" in tracker_last_text.lower(), (
                "Live Telegram E2E did not observe worker assignment marker in tracker."
            )
            assert saw_finish_summary, "Live Telegram E2E did not reach session summary."
            assert complete_count is None or complete_count >= 1, (
                f"Live Telegram E2E did not complete any milestones (complete={complete_count}, failed={failed_count})."
            )
            assert saw_run_button and saw_run_success, "Live Telegram E2E did not reach a successful Run Project output."
        except Exception:
            if container_streamer is not None:
                trace(
                    "container.log.bundle",
                    status="fail",
                    reason="final_assertion_failure",
                    tails=container_streamer.bundle(),
                )
            raise
        trace("e2e.step.end", step=9, name="coding_and_run", status="ok")
        trace(
            "test.success",
            saw_run_button=saw_run_button,
            saw_run_success=saw_run_success,
            complete_count=complete_count,
            failed_count=failed_count,
            tracker_message_id=tracker_message_id,
            tracker_edit_count=tracker_edit_count,
        )
        runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="test.success", tail_lines=180))
        if container_streamer is not None:
            await container_streamer.stop()
        print(f"[LIVE TRACE] {trace_path}")
