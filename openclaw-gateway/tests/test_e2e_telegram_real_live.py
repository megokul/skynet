"""Fully real Telegram-network E2E (user account -> bot chat -> inline buttons)."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from ctypes import wintypes
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = REPO_ROOT / "openclaw-gateway"
import sys

for candidate in (str(REPO_ROOT), str(GATEWAY_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from live_settings import bootstrap_gateway_runtime

bootstrap_gateway_runtime()

from live_diagnostics import (
    LiveContainerDiagnostics,
    container_log_error_summary,
    fetch_remote_gateway_status,
    make_live_trace_logger,
    run_live_e2e_preflight,
)
import config as cfg

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
_WINDOWS_DEFAULT_POPUP_TITLES = ("Message", "Notification")
_WINDOWS_POPUP_CLASSES = ("#32770", "TkTopLevel")
_WM_CLOSE = 0x0010


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


def _env_flag(name: str, default: bool) -> bool:
    return _env_bool(name, default)


def _terminal_run_project_failure_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    if "run failed:" in lowered:
        return str(text).strip()
    if "exited with code " in lowered:
        return str(text).strip()
    return ""


def _terminal_coding_failure_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    if (
        "coding progress [" in lowered
        and "phase: finalization" in lowered
        and ("status: failed" in lowered or "status: stopped" in lowered)
    ):
        return str(text).strip()
    for marker in (
        "worker unavailable before coding started",
        "worker not connected - cannot create project folder",
        "session stopped before milestones were extracted",
        "session stopped while executing milestone",
        "timed out while waiting for the coding agent",
        "coding session stopped because it appeared stuck",
        "unexpected error occurred in the coding loop",
    ):
        if marker in lowered:
            return str(text).strip()
    return ""


def _strict_stage_policy_violation_text(text: str, *, allowed_stages: set[str]) -> str:
    raw = str(text or "").strip()
    if not raw or not allowed_stages:
        return ""
    lowered = raw.lower()
    fallback_match = re.search(
        r"stage\s+([a-z0-9_]+)\s+failed\b.*?\btrying\s+([a-z0-9_]+)\b",
        lowered,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fallback_match:
        next_stage = str(fallback_match.group(2) or "").strip().lower()
        if (
            next_stage
            and next_stage not in allowed_stages
            and _looks_like_agent_stage(next_stage, allowed_stages=allowed_stages)
        ):
            return raw
    for pattern in (
        r"\brunning stage ([a-z0-9_]+)\b",
        r"\bstage=([a-z0-9_]+)\b",
    ):
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        stage = str(match.group(1) or "").strip().lower()
        if (
            stage
            and stage not in allowed_stages
            and _looks_like_agent_stage(stage, allowed_stages=allowed_stages)
        ):
            return raw
    return ""


def _terminal_bot_failure_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    for marker in (
        "ai is unavailable right now",
        "planner is unavailable right now",
        "i couldn't generate a plan right now",
        "i could not generate a plan right now",
        "requirements ai call failed",
    ):
        if marker in lowered:
            return str(text).strip()
    return ""


def _window_text(hwnd: int) -> str:
    if sys.platform != "win32":
        return ""
    length = int(ctypes.windll.user32.GetWindowTextLengthW(hwnd) or 0)
    buffer = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return str(buffer.value or "")


def _window_class_name(hwnd: int) -> str:
    if sys.platform != "win32":
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buffer, len(buffer))
    return str(buffer.value or "")


def _close_windows_popup_dialogs_once(*, titles: set[str] | None = None) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    title_whitelist = {title.lower() for title in (titles or set(_WINDOWS_DEFAULT_POPUP_TITLES))}
    class_whitelist = {name.lower() for name in _WINDOWS_POPUP_CLASSES}
    closed_windows: list[dict[str, Any]] = []

    @enum_proc
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(hwnd).strip()
        if not title or title.lower() not in title_whitelist:
            return True
        class_name = _window_class_name(hwnd).strip()
        class_lower = class_name.lower()
        if class_lower not in class_whitelist and "tk" not in class_lower:
            return True
        user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        closed_windows.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "class_name": class_name,
            }
        )
        return True

    user32.EnumWindows(_callback, 0)
    return closed_windows


async def _popup_auto_close_worker(
    *,
    stop_event: asyncio.Event,
    trace_fn: Callable[..., None],
    step: str,
    titles: set[str] | None = None,
) -> None:
    if sys.platform != "win32":
        trace_fn("popup.auto_close.skip", step=step, reason="non_windows")
        return
    if not _env_flag("SKYNET_E2E_AUTO_CLOSE_POPUPS", True):
        trace_fn("popup.auto_close.skip", step=step, reason="disabled")
        return
    poll_interval_s = max(
        0.2,
        float((os.environ.get("SKYNET_E2E_POPUP_CLOSE_POLL_S") or "0.5").strip() or "0.5"),
    )
    trace_fn(
        "popup.auto_close.start",
        step=step,
        poll_interval_s=poll_interval_s,
        titles=sorted(titles or set(_WINDOWS_DEFAULT_POPUP_TITLES)),
    )
    closed_count = 0
    while not stop_event.is_set():
        try:
            closed_windows = await asyncio.to_thread(
                _close_windows_popup_dialogs_once,
                titles=titles,
            )
        except Exception as exc:
            trace_fn(
                "popup.auto_close.error",
                step=step,
                error=f"{type(exc).__name__}: {exc}",
            )
            closed_windows = []
        for window in closed_windows:
            closed_count += 1
            trace_fn(
                "popup.auto_close.closed",
                step=step,
                close_count=closed_count,
                **window,
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            pass
    trace_fn("popup.auto_close.stop", step=step, close_count=closed_count)


class _TerminalBotFailure(AssertionError):
    pass


_KNOWN_AGENT_STAGE_NAMES = {
    "claude",
    "cline",
    "codex",
    "ollama",
    "qwen",
    "router",
}


def _looks_like_agent_stage(stage: str, *, allowed_stages: set[str]) -> bool:
    normalized = str(stage or "").strip().lower()
    if not normalized:
        return False
    return normalized in allowed_stages or normalized in _KNOWN_AGENT_STAGE_NAMES


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


def _load_runtime_trace_events() -> tuple[Path | None, list[dict[str, Any]]]:
    path = _resolve_runtime_trace_file()
    if path is None or not path.exists():
        return path, []
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
    except Exception:
        return path, []
    return path, events


def _runtime_trace_transport_summary() -> dict[str, Any]:
    path, events = _load_runtime_trace_events()
    websocket_select = 0
    ssh_fallback = 0
    fallback_reasons: list[str] = []
    for event in events:
        event_name = str(event.get("event") or "").strip()
        transport = str(event.get("transport") or "").strip()
        if event_name == "gateway.transport.select" and transport == "websocket_primary":
            websocket_select += 1
        if event_name == "gateway.transport.fallback":
            ssh_fallback += 1
            details = event.get("details") or {}
            if isinstance(details, dict):
                reason = str(details.get("reason") or "").strip()
                if reason:
                    fallback_reasons.append(reason)
    return {
        "trace_file": str(path) if path else "",
        "websocket_primary_select_count": websocket_select,
        "ssh_fallback_count": ssh_fallback,
        "fallback_reasons": fallback_reasons[-10:],
    }


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise AssertionError(f"Missing required env: {name}")
    return value


def _popup_title_candidates(project_slug: str) -> set[str]:
    titles = {title for title in _WINDOWS_DEFAULT_POPUP_TITLES if title}
    normalized_slug = str(project_slug or "").strip()
    if normalized_slug:
        titles.add(normalized_slug)
    return titles


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
    terminal_failure_text_fn: Callable[[str], str] | None = None,
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
            terminal_failure = _terminal_bot_failure_text(text)
            if terminal_failure:
                trace_fn(
                    "telegram.wait.terminal_failure",
                    step=step,
                    message_id=int(getattr(msg, "id", 0)),
                    text_preview=terminal_failure[:220],
                    buttons=btns,
                    polls=poll_count,
                    waited_s=round(time.monotonic() - started, 1),
                )
                raise _TerminalBotFailure(
                    f"Terminal bot failure encountered: {terminal_failure[:260]}"
                )
            if terminal_failure_text_fn is not None:
                extra_failure = terminal_failure_text_fn(text)
                if extra_failure:
                    trace_fn(
                        "telegram.wait.terminal_failure",
                        step=step,
                        message_id=int(getattr(msg, "id", 0)),
                        text_preview=extra_failure[:220],
                        buttons=btns,
                        polls=poll_count,
                        waited_s=round(time.monotonic() - started, 1),
                    )
                    raise _TerminalBotFailure(
                        f"Terminal bot failure encountered: {extra_failure[:260]}"
                    )
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

def _resolve_worker_projects_dir() -> Path:
    _home_projects = str(Path.home() / "Projects")
    candidates = [
        os.environ.get("OPENCLAW_PROJECT_BASE_DIR", "").strip(),
        os.environ.get("WORKER_PROJECTS_DIR", "").strip(),
        os.environ.get("SKYNET_PROJECT_BASE_DIR", "").strip(),
        _home_projects,
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
    return Path(_home_projects)


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


async def _bootstrap_worker_agent(
    *,
    trace_fn: Callable[..., None],
    policy: dict[str, Any],
) -> subprocess.Popen | None:
    """Start worker agent with SSH tunnel if not already connected to EC2 gateway."""
    bootstrap_cfg = dict(policy.get("worker_bootstrap") or {})
    if not bool(bootstrap_cfg.get("enabled", True)):
        trace_fn("worker.bootstrap.skip", reason="disabled")
        return None

    diagnostics_profile = str(policy.get("diagnostics_profile") or "")
    remote_container = str(policy.get("remote_gateway_container") or "openclaw-gateway")
    remote_status_url = str(policy.get("remote_status_url") or "http://localhost:8766/status")

    # Check if agent is already connected
    try:
        status = await fetch_remote_gateway_status(
            container_name=remote_container,
            status_url=remote_status_url,
            diagnostics_profile=diagnostics_profile,
        )
        agent_ok = bool(status.get("agent_connected", False))
        transport_ok = str(status.get("primary_transport_mode") or "") == "websocket_primary"
        trace_fn(
            "worker.bootstrap.status",
            status="ready" if (agent_ok and transport_ok) else "pending",
            agent_connected=agent_ok,
            primary_transport_mode=str(status.get("primary_transport_mode") or ""),
        )
        if agent_ok and transport_ok:
            trace_fn("worker.bootstrap.skip", reason="already_ready")
            return None
    except Exception as exc:
        trace_fn("worker.bootstrap.status", status="retry", error=f"{type(exc).__name__}: {exc}")

    # Resolve bootstrap script and env file
    repo_root = Path(__file__).resolve().parents[2]
    script = Path(str(bootstrap_cfg.get("script") or "scripts/run_worker_agent.ps1").strip())
    env_file = Path(str(bootstrap_cfg.get("env_file") or ".env.worker-agent").strip())
    if not script.is_absolute():
        script = repo_root / script
    if not env_file.is_absolute():
        env_file = repo_root / env_file

    if not script.exists():
        raise AssertionError(f"WORKER_BOOTSTRAP: script not found ({script})")
    if not env_file.exists():
        raise AssertionError(f"WORKER_BOOTSTRAP: env file not found ({env_file})")

    python_path = str(bootstrap_cfg.get("python_path") or "").strip() or sys.executable
    if script.suffix.lower() == ".ps1":
        shell = "powershell" if sys.platform == "win32" else "pwsh"
        cmd = [
            shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-RepoRoot", str(repo_root), "-EnvFile", str(env_file), "-PythonPath", python_path,
        ]
    else:
        cmd = [str(script), str(repo_root), str(env_file), python_path]

    proc = subprocess.Popen(
        cmd, cwd=str(repo_root), env=dict(os.environ),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
    )
    trace_fn("worker.bootstrap.start", pid=proc.pid, script=str(script), env_file=str(env_file))

    # Poll until agent connects
    wait_seconds = max(10, int(bootstrap_cfg.get("wait_seconds") or 60))
    poll_seconds = max(1, int(bootstrap_cfg.get("poll_seconds") or 3))
    deadline = time.monotonic() + wait_seconds

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"WORKER_BOOTSTRAP: launcher exited early (exit={proc.returncode})")
        try:
            status = await fetch_remote_gateway_status(
                container_name=remote_container,
                status_url=remote_status_url,
                diagnostics_profile=diagnostics_profile,
            )
            agent_ok = bool(status.get("agent_connected", False))
            transport_ok = str(status.get("primary_transport_mode") or "") == "websocket_primary"
            trace_fn(
                "worker.bootstrap.poll",
                agent_connected=agent_ok,
                primary_transport_mode=str(status.get("primary_transport_mode") or ""),
            )
            if agent_ok and transport_ok:
                trace_fn("worker.bootstrap.ready", pid=proc.pid)
                return proc
        except Exception as exc:
            trace_fn("worker.bootstrap.poll_error", error=f"{type(exc).__name__}: {exc}")
        await asyncio.sleep(poll_seconds)

    # Timed out
    proc.terminate()
    raise AssertionError(f"WORKER_BOOTSTRAP: agent did not connect within {wait_seconds}s")


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_telegram_chat_flow_no_github_repo_creation() -> None:
    trace_path, trace = make_live_trace_logger("telegram-real-live-e2e")
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
    policy = cfg.get_live_e2e_policy("telegram_real")
    container_diagnostics = LiveContainerDiagnostics(
        trace_fn=trace,
        config_override=dict(policy.get("container_log") or {}),
    )
    stream_config = container_diagnostics.config
    stream_required = bool(stream_config["require_stream"])
    runtime_stale_seconds = int(policy.get("runtime_trace_stale_seconds", 90) or 90)
    message_progress_timeout_seconds = int(policy.get("message_progress_timeout_seconds", 90) or 90)
    require_websocket_primary = str(policy.get("required_transport") or "") == "websocket_primary"
    allow_ssh_fallback = bool(policy.get("allow_fallback", False))
    allowed_live_stages = {
        str(stage).strip().lower()
        for stage in list(policy.get("effective_coding_stage_chain") or [])
        if str(stage).strip()
    }
    runtime_progress = _RuntimeTraceProgress()
    bundle_status = "ok"
    bundle_reason = "test_success"
    bundle_emitted = False
    trace(
        "test.start",
        bot_username=bot_username,
        flow="hi_to_project_completion",
        required_transport=policy.get("required_transport"),
        allow_fallback=bool(policy.get("allow_fallback", False)),
        effective_coding_stage_chain=sorted(allowed_live_stages),
        required_worker_agents=list(policy.get("required_worker_agents") or []),
        container_stream_enabled=stream_config["stream_enabled"],
        container_stream_required=stream_config["require_stream"],
        container_stream_sources=stream_config["sources"],
        diagnostics_profile=str(policy.get("diagnostics_profile") or ""),
        runtime_trace_stale_seconds=runtime_stale_seconds,
        message_progress_timeout_seconds=message_progress_timeout_seconds,
    )
    runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="test.start", tail_lines=80))

    project_slug = f"livee2e{int(time.time())}"
    requirement = _CONVERSATIONAL_REQUIREMENT
    trace("test.input", project_slug=project_slug, requirement_preview=requirement[:220])
    trace("test.requirement.payload", payload=requirement)

    worker_proc: subprocess.Popen | None = None
    popup_close_stop: asyncio.Event | None = None
    popup_close_task: asyncio.Task[None] | None = None
    try:
        async with TelegramClient(StringSession(session), api_id, api_hash) as client:
            trace("telegram.client.connected")
            bot = await client.get_entity(bot_username)
            history = await client.get_messages(bot, limit=1)
            last_id = int(history[0].id) if history else 0
            trace("telegram.history", last_message_id=last_id)
            try:
                await container_diagnostics.start()
            except Exception:
                bundle_status = "fail"
                bundle_reason = "container_stream_unavailable"
                raise
            worker_proc = await _bootstrap_worker_agent(
                trace_fn=trace,
                policy=policy,
            )
            await run_live_e2e_preflight(
                trace_fn=trace,
                flow="telegram_real",
                policy=policy,
            )
            trace("e2e.step.start", step=1, name="reset_to_main_menu")
            await client.send_message(bot, "/start")
            msg = await _wait_for_bot_message(
                client,
                bot,
                last_id,
                timeout_s=120,
                trace_fn=trace,
                step="menu_after_start",
                predicate=lambda text, btns: any("start a project" in b.lower() for b in btns),
            )
            last_id = int(msg.id)
            await _click_button_contains(msg, "Start a Project", trace_fn=trace, step="click_start_project")
            runtime_progress.observe(_emit_runtime_trace_snapshot(trace, checkpoint="after.start_project", tail_lines=60))
            trace("e2e.step.end", step=1, name="reset_to_main_menu", status="ok")

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
                bundle_status = "fail"
                bundle_reason = "planner_clarification_exhausted"
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
            popup_close_stop = asyncio.Event()
            popup_close_task = asyncio.create_task(
                _popup_auto_close_worker(
                    stop_event=popup_close_stop,
                    trace_fn=trace,
                    step="coding_and_run",
                    titles=_popup_title_candidates(project_slug),
                )
            )

            def _raise_terminal_coding_failure(failure_text: str, *, reason: str) -> None:
                nonlocal bundle_status, bundle_reason
                bundle_status = "fail"
                bundle_reason = reason
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(
                        trace,
                        checkpoint=f"coding.{reason}",
                        tail_lines=220,
                    )
                )
                trace(
                    "e2e.step.fail",
                    step=9,
                    name="coding_and_run",
                    status="fail",
                    error_message=f"Terminal coding failure: {failure_text[:260]}",
                )
                raise AssertionError(
                    f"Live Telegram E2E reached terminal coding failure: {failure_text[:260]}"
                )

            for idx in range(80):
                try:
                    msg = await _wait_for_bot_message(
                        client,
                        bot,
                        last_id,
                        timeout_s=message_progress_timeout_seconds,
                        trace_fn=trace,
                        step=f"coding_poll_{idx + 1}",
                        predicate=lambda text, btns: bool(text.strip()) or bool(btns),
                    )
                except _TerminalBotFailure as exc:
                    bundle_status = "fail"
                    bundle_reason = "terminal_bot_failure"
                    raise AssertionError(str(exc)) from exc
                except AssertionError:
                    tracker_last_text, tracker_edit_count, tracker_progress = await _poll_tracker_message_edit(
                        client=client,
                        bot=bot,
                        tracker_message_id=tracker_message_id,
                        tracker_last_text=tracker_last_text,
                        tracker_edit_count=tracker_edit_count,
                        trace_fn=trace,
                    )
                    if stream_required and container_diagnostics.has_errors():
                        summary = container_log_error_summary(container_diagnostics)
                        bundle_status = "fail"
                        bundle_reason = "container_stream_unhealthy"
                        raise AssertionError(f"CONTAINER_LOG_STREAM_UNAVAILABLE: {summary}")
                    if tracker_progress:
                        lowered_tracker = tracker_last_text.lower()
                        if "phase: director" in lowered_tracker:
                            saw_director_phase = True
                        if "phase: architect" in lowered_tracker:
                            saw_architect_phase = True
                        if "worker=" in lowered_tracker or "worker:" in lowered_tracker:
                            saw_worker_assignment_marker = True
                        terminal_tracker_failure = _terminal_coding_failure_text(tracker_last_text)
                        if terminal_tracker_failure:
                            _raise_terminal_coding_failure(
                                terminal_tracker_failure,
                                reason="tracker_terminal_failure",
                            )
                        strict_stage_violation = _strict_stage_policy_violation_text(
                            tracker_last_text,
                            allowed_stages=allowed_live_stages,
                        )
                        if strict_stage_violation:
                            _raise_terminal_coding_failure(
                                strict_stage_violation,
                                reason="tracker_stage_policy_violation",
                            )
                    timeout_snapshot = _emit_runtime_trace_snapshot(
                        trace,
                        checkpoint=f"coding_poll_timeout_{idx + 1}",
                        tail_lines=140,
                    )
                    trace_progress = runtime_progress.observe(timeout_snapshot)
                    if tracker_progress:
                        trace(
                            "coding.poll.recovered",
                            iteration=idx + 1,
                            tracker_progress=True,
                            trace_progress=trace_progress,
                            container_progress=container_diagnostics.has_recent_activity(within_seconds=60),
                            runtime_trace_stale_s=round(runtime_progress.stale_seconds(), 1),
                        )
                        continue
                    trace(
                        "coding.poll.timeout",
                        iteration=idx + 1,
                        tracker_progress=False,
                        trace_progress=trace_progress,
                        container_progress=container_diagnostics.has_recent_activity(within_seconds=60),
                        runtime_trace_stale_s=round(runtime_progress.stale_seconds(), 1),
                    )
                    last_id = await _request_trace_deep_snapshot(
                        client=client,
                        bot=bot,
                        after_id=last_id,
                        trace_fn=trace,
                    )
                    bundle_status = "fail"
                    bundle_reason = "coding_poll_timeout"
                    raise AssertionError("Coding poll timed out without user-visible progress")
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
                if stream_required and container_diagnostics.has_errors():
                    summary = container_log_error_summary(container_diagnostics)
                    bundle_status = "fail"
                    bundle_reason = "container_stream_unhealthy"
                    raise AssertionError(f"CONTAINER_LOG_STREAM_UNAVAILABLE: {summary}")
                if idx == 0 or (idx + 1) % 5 == 0:
                    runtime_progress.observe(
                        _emit_runtime_trace_snapshot(
                            trace,
                            checkpoint=f"coding_poll_{idx + 1}",
                            tail_lines=80,
                        )
                    )
                if runtime_progress.stale_seconds() >= runtime_stale_seconds:
                    trace(
                        "runtime.trace.stale",
                        step="coding_loop",
                        iteration=idx + 1,
                        stale_s=round(runtime_progress.stale_seconds(), 1),
                        threshold_s=runtime_stale_seconds,
                    )
                lowered = text.lower()
                if "phase: director" in lowered:
                    saw_director_phase = True
                if "phase: architect" in lowered:
                    saw_architect_phase = True
                if "worker=" in lowered or "worker:" in lowered:
                    saw_worker_assignment_marker = True
                terminal_failure_text = _terminal_coding_failure_text(text)
                if terminal_failure_text:
                    _raise_terminal_coding_failure(
                        terminal_failure_text,
                        reason="message_terminal_failure",
                    )
                strict_stage_violation = _strict_stage_policy_violation_text(
                    text,
                    allowed_stages=allowed_live_stages,
                )
                if strict_stage_violation:
                    _raise_terminal_coding_failure(
                        strict_stage_violation,
                        reason="message_stage_policy_violation",
                    )

                if any(marker in lowered for marker in preflight_fail_markers):
                    bundle_status = "fail"
                    bundle_reason = "coding_preflight_failure"
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
                    bundle_status = "fail"
                    bundle_reason = "coding_session_failed"
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
                    terminal_tracker_failure = _terminal_coding_failure_text(tracker_last_text)
                    if terminal_tracker_failure:
                        _raise_terminal_coding_failure(
                            terminal_tracker_failure,
                            reason="tracker_terminal_failure",
                        )
                    strict_stage_violation = _strict_stage_policy_violation_text(
                        tracker_last_text,
                        allowed_stages=allowed_live_stages,
                    )
                    if strict_stage_violation:
                        _raise_terminal_coding_failure(
                            strict_stage_violation,
                            reason="tracker_stage_policy_violation",
                        )

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
                        terminal_failure_text_fn=_terminal_run_project_failure_text,
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

            if popup_close_stop is not None and popup_close_task is not None:
                popup_close_stop.set()
                await popup_close_task
                popup_close_stop = None
                popup_close_task = None

            if saw_run_success:
                _validate_generated_project_artifacts(project_slug=project_slug, trace_fn=trace)
                runtime_progress.observe(
                    _emit_runtime_trace_snapshot(trace, checkpoint="artifact.validation.ok", tail_lines=160)
                )
            transport_summary = _runtime_trace_transport_summary()
            trace(
                "runtime.trace.transport.summary",
                require_websocket_primary=require_websocket_primary,
                allow_ssh_fallback=allow_ssh_fallback,
                **transport_summary,
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
                if require_websocket_primary:
                    assert transport_summary["websocket_primary_select_count"] >= 1, (
                        "Live Telegram E2E did not observe websocket-primary transport selection in runtime trace."
                    )
                if not allow_ssh_fallback:
                    assert transport_summary["ssh_fallback_count"] == 0, (
                        "Live Telegram E2E observed SSH fallback when SKYNET_E2E_ALLOW_SSH_FALLBACK=0: "
                        + ", ".join(transport_summary["fallback_reasons"])
                    )
            except Exception:
                bundle_status = "fail"
                bundle_reason = "final_assertion_failure"
                bundle_emitted = True
                await container_diagnostics.emit_bundle(
                    status=bundle_status,
                    reason=bundle_reason,
                    flow="telegram_real",
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
            bundle_emitted = True
            await container_diagnostics.emit_bundle(
                status=bundle_status,
                reason=bundle_reason,
                flow="telegram_real",
            )
            print(f"[LIVE TRACE] {trace_path}")
    except Exception:
        bundle_status = "fail"
        if bundle_reason == "test_success":
            bundle_reason = "test_failure"
        raise
    finally:
        if popup_close_stop is not None and popup_close_task is not None:
            popup_close_stop.set()
            await popup_close_task
        if not bundle_emitted:
            await container_diagnostics.emit_bundle(
                status=bundle_status,
                reason=bundle_reason,
                flow="telegram_real",
            )
        await container_diagnostics.stop()
        if worker_proc is not None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
            trace("worker.bootstrap.stopped", pid=worker_proc.pid)
