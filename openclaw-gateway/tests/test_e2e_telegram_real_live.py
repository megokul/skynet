"""Fully real Telegram-network E2E (user account -> bot chat -> inline buttons)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Callable

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
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            repo_root / "logs" / "skynet.trace.log",
            repo_root / "openclaw-gateway" / "logs" / "skynet.trace.log",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if explicit:
        return Path(explicit)
    return None


def _emit_runtime_trace_snapshot(
    trace_fn: Callable[..., None],
    *,
    checkpoint: str,
    tail_lines: int = 120,
) -> None:
    path = _resolve_runtime_trace_file()
    if path is None:
        trace_fn(
            "runtime.trace.snapshot",
            checkpoint=checkpoint,
            status="missing",
            reason="trace file not found",
        )
        return
    if not path.exists():
        trace_fn(
            "runtime.trace.snapshot",
            checkpoint=checkpoint,
            status="missing",
            trace_file=str(path),
            reason="path does not exist",
        )
        return
    try:
        tail = deque(maxlen=max(1, tail_lines))
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                tail.append(line.rstrip("\n"))
        joined = "\n".join(tail)
        digest = sha1(joined.encode("utf-8", errors="replace")).hexdigest()
        preview = joined[-2500:]
        trace_fn(
            "runtime.trace.snapshot",
            checkpoint=checkpoint,
            status="ok",
            trace_file=str(path),
            lines=len(tail),
            digest=digest,
            preview=preview,
        )
    except Exception as exc:
        trace_fn(
            "runtime.trace.snapshot",
            checkpoint=checkpoint,
            status="error",
            trace_file=str(path),
            error=f"{type(exc).__name__}: {exc}",
        )


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
    trace(
        "test.start",
        bot_username=bot_username,
        flow="hi_to_project_completion",
    )
    _emit_runtime_trace_snapshot(trace, checkpoint="test.start", tail_lines=80)

    project_slug = f"livee2e{int(time.time())}"
    requirement = _CONVERSATIONAL_REQUIREMENT
    trace("test.input", project_slug=project_slug, requirement_preview=requirement[:220])
    trace("test.requirement.payload", payload=requirement)

    async with TelegramClient(StringSession(session), api_id, api_hash) as client:
        trace("telegram.client.connected")
        bot = await client.get_entity(bot_username)
        history = await client.get_messages(bot, limit=1)
        last_id = int(history[0].id) if history else 0
        trace("telegram.history", last_message_id=last_id)

        trace("step.start", step=1, name="send_hi")
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
        _emit_runtime_trace_snapshot(trace, checkpoint="after.start_project", tail_lines=60)

        trace("step.start", step=2, name="project_name")
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

        trace("step.start", step=3, name="project_type")
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

        trace("step.start", step=4, name="requirements")
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
        _emit_runtime_trace_snapshot(trace, checkpoint="after.requirements_sent", tail_lines=80)

        trace("step.start", step=5, name="generate_plan")
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
            raise AssertionError("Planner clarification loop exhausted before plan approval")

        trace("step.start", step=6, name="approve_plan")
        msg = plan_msg
        await _click_button_contains(msg, "Approve", trace_fn=trace, step="click_plan_approve")
        _emit_runtime_trace_snapshot(trace, checkpoint="after.plan_approved", tail_lines=100)

        trace("step.start", step=7, name="start_coding")
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
        _emit_runtime_trace_snapshot(trace, checkpoint="after.start_coding_clicked", tail_lines=120)

        trace("step.start", step=8, name="skip_github_repo_creation")
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
        _emit_runtime_trace_snapshot(trace, checkpoint="after.skip_github", tail_lines=120)

        trace("step.start", step=9, name="coding_and_run")
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
            msg = await _wait_for_bot_message(
                client,
                bot,
                last_id,
                timeout_s=90,
                trace_fn=trace,
                step=f"coding_poll_{idx + 1}",
                predicate=lambda text, btns: bool(text.strip()) or bool(btns),
            )
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
            if idx == 0 or (idx + 1) % 5 == 0:
                _emit_runtime_trace_snapshot(
                    trace,
                    checkpoint=f"coding_poll_{idx + 1}",
                    tail_lines=80,
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
                _emit_runtime_trace_snapshot(trace, checkpoint="coding.preflight.failure", tail_lines=200)
                raise AssertionError(
                    f"Live Telegram E2E encountered terminal preflight failure: {text[:260]}"
                )

            if "session failed" in lowered and "complete=" in lowered:
                _emit_runtime_trace_snapshot(trace, checkpoint="coding.session.failed", tail_lines=220)
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

            if tracker_message_id is not None:
                with contextlib.suppress(Exception):
                    tracker_msg = await client.get_messages(bot, ids=tracker_message_id)
                    current_tracker_text = str(getattr(tracker_msg, "message", "") or "")
                    if current_tracker_text and current_tracker_text != tracker_last_text:
                        tracker_edit_count += 1
                        tracker_last_text = current_tracker_text
                        lowered_tracker = current_tracker_text.lower()
                        if "phase: director" in lowered_tracker:
                            saw_director_phase = True
                        if "phase: architect" in lowered_tracker:
                            saw_architect_phase = True
                        if "worker=" in lowered_tracker or "worker:" in lowered_tracker:
                            saw_worker_assignment_marker = True
                        trace(
                            "tracker.message.edited",
                            message_id=tracker_message_id,
                            edits=tracker_edit_count,
                            text_preview=current_tracker_text[:220],
                        )

            if "github repo created and pushed" in lowered:
                saw_no_github_push = False
                break

            if any("run it" in b.lower() for b in btns):
                await _click_button_contains(msg, "Run It", trace_fn=trace, step="click_milestone_run_it")
                _emit_runtime_trace_snapshot(
                    trace,
                    checkpoint=f"milestone.run_it.clicked.{idx + 1}",
                    tail_lines=120,
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
                _emit_runtime_trace_snapshot(trace, checkpoint="run_project.output", tail_lines=220)
                if "exit 0" in run_text.lower() or "finished (exit 0)" in run_text.lower():
                    saw_run_success = True
                break

        if saw_run_success:
            _validate_generated_project_artifacts(project_slug=project_slug, trace_fn=trace)
            _emit_runtime_trace_snapshot(trace, checkpoint="artifact.validation.ok", tail_lines=160)

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
        trace(
            "test.success",
            saw_run_button=saw_run_button,
            saw_run_success=saw_run_success,
            complete_count=complete_count,
            failed_count=failed_count,
            tracker_message_id=tracker_message_id,
            tracker_edit_count=tracker_edit_count,
        )
        _emit_runtime_trace_snapshot(trace, checkpoint="test.success", tail_lines=180)
        print(f"[LIVE TRACE] {trace_path}")
