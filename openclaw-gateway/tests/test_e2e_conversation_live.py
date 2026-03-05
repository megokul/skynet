"""Live handler-path conversation E2E (simulated Telegram transport; real planner + SSH worker)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram.ext import ConversationHandler

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency at runtime
    load_dotenv = None  # type: ignore[assignment]

from ai.provider_router import ProviderRouter, build_providers, parse_provider_priority
from db.schema import init_db
from db.store import list_tasks
from ssh_tunnel_executor import get_ssh_executor

from bot.handlers.coding import (
    _ACTIVE_LOOP_KEY,
    _CODING_PID_KEY,
    _MS_DECISION_KEY,
    _MS_EVENT_KEY,
    coding_github_choice_handler,
    run_project_handler,
    start_coding_handler,
)
from bot.handlers.greeting import greeting_handler
from bot.handlers.project import (
    AWAITING_PROJECT_NAME,
    AWAITING_PROJECT_TYPE,
    GATHERING_REQUIREMENTS,
    REVIEWING_PLAN,
    _PLAN_KEY,
    approve_plan,
    ask_project_name,
    handle_requirements_message,
    receive_project_name,
    receive_project_type,
    requirements_done_handler,
)
from bot.keyboards import (
    CB_CODING_GITHUB_SKIP,
    CB_PLAN_APPROVE,
    CB_REQUIREMENTS_DONE,
    CB_RUN_PROJECT,
    CB_START_CODING,
    CB_START_PROJECT,
)
from bot.state import KEY_DB, KEY_ROUTER


def _load_live_env_from_dotenv() -> None:
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parents[2]
    candidates = []
    explicit = os.environ.get("SKYNET_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            repo_root / ".env",
            repo_root / "openclaw-gateway" / ".env",
        ]
    )
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


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _skip_or_fail_live(reason: str, detail: str | None = None) -> None:
    message = reason if not detail else f"{reason}: {detail}"
    if _bool_env("SKYNET_E2E_FAIL_ON_SKIP", True):
        raise AssertionError(message)
    pytest.skip(message)


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


@dataclass
class _SimpleChat:
    id: int
    actions: list[str] = field(default_factory=list)

    async def send_action(self, action: str) -> None:
        self.actions.append(str(action))


@dataclass
class _SimpleMessage:
    chat: _SimpleChat
    text: str = ""
    replies: list[dict[str, Any]] = field(default_factory=list)

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append({"text": str(text), "kwargs": dict(kwargs)})


@dataclass
class _SimpleCallbackQuery:
    data: str
    message: _SimpleMessage
    answered: bool = False

    async def answer(self) -> None:
        self.answered = True


@dataclass
class _SimpleUpdate:
    effective_user: Any
    effective_chat: _SimpleChat
    message: _SimpleMessage | None = None
    callback_query: _SimpleCallbackQuery | None = None
    effective_message: _SimpleMessage | None = None


def _make_user(user_id: int) -> Any:
    return SimpleNamespace(
        id=user_id,
        username="skynet_live",
        first_name="Live",
        last_name="E2E",
    )


def _make_message_update(text: str, *, user_id: int, chat_id: int) -> _SimpleUpdate:
    chat = _SimpleChat(id=chat_id)
    message = _SimpleMessage(chat=chat, text=text)
    return _SimpleUpdate(
        effective_user=_make_user(user_id),
        effective_chat=chat,
        message=message,
        callback_query=None,
        effective_message=message,
    )


def _make_callback_update(data: str, *, user_id: int, chat_id: int) -> _SimpleUpdate:
    chat = _SimpleChat(id=chat_id)
    message = _SimpleMessage(chat=chat, text="")
    callback_query = _SimpleCallbackQuery(data=data, message=message)
    return _SimpleUpdate(
        effective_user=_make_user(user_id),
        effective_chat=chat,
        message=None,
        callback_query=callback_query,
        effective_message=message,
    )


@dataclass
class _SimpleContext:
    user_data: dict[str, Any]
    bot_data: dict[str, Any]
    application: Any | None = None


class _TraceBot:
    def __init__(self, trace_fn) -> None:
        self._trace = trace_fn
        self.sent_messages: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        payload = {
            "chat_id": chat_id,
            "text": str(text),
            "kwargs": dict(kwargs),
        }
        self.sent_messages.append(payload)
        self._trace(
            "bot.send_message",
            chat_id=chat_id,
            text_preview=str(text)[:220],
            has_reply_markup=bool(kwargs.get("reply_markup")),
            parse_mode=str(kwargs.get("parse_mode", "")),
        )


class _HarnessApp:
    def __init__(self, bot_data: dict[str, Any], trace_fn) -> None:
        self.bot_data = bot_data
        self.bot = _TraceBot(trace_fn)


def _with_env(overrides: dict[str, str]) -> tuple[dict[str, str | None], None]:
    previous: dict[str, str | None] = {}
    for key, value in overrides.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous, None


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_conversation_real_planner_codegen_no_github_push():
    trace_path, trace = _make_live_trace_logger("live-conversation-e2e")
    if os.environ.get("SKYNET_E2E_LIVE") != "1":
        trace("test.skip", reason="SKYNET_E2E_LIVE is not 1")
        pytest.skip("Set SKYNET_E2E_LIVE=1 to run live conversation E2E.")

    missing = [
        key for key in ("OPENCLAW_SSH_HOST", "OPENCLAW_SSH_USER")
        if not os.environ.get(key)
    ]
    if missing:
        trace("test.skip", reason="Missing SSH env vars", missing=missing)
        _skip_or_fail_live("Missing live E2E SSH env vars", ", ".join(missing))

    previous_env, _ = _with_env(
        {
            "OPENCLAW_EXECUTION_MODE": "ssh",
            "SKYNET_STRICT_EMPTY_OUTPUT_EMERGENCY_SCAFFOLD": "1",
        }
    )
    db = await init_db(":memory:")
    try:
        executor = get_ssh_executor()
        if not executor.is_configured():
            trace("test.skip", reason="SSH executor is not configured")
            _skip_or_fail_live("SSH executor is not configured for live E2E.")
        healthy, detail = await executor.health_check()
        if not healthy:
            trace("test.skip", reason="SSH executor unreachable", detail=detail)
            _skip_or_fail_live("SSH executor unreachable", detail)

        trace(
            "test.start",
            trace_file=str(trace_path),
            ssh_host=os.environ.get("OPENCLAW_SSH_HOST", ""),
            ssh_port=os.environ.get("OPENCLAW_SSH_PORT", ""),
        )

        provider_cfg = {
            "OLLAMA_DEFAULT_MODEL": os.environ.get("OLLAMA_DEFAULT_MODEL", ""),
            "GOOGLE_AI_API_KEY": os.environ.get("GOOGLE_AI_API_KEY", ""),
            "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", ""),
            "GEMINI_ONLY_MODE": os.environ.get("GEMINI_ONLY_MODE", "0"),
            "GROQ_API_KEY": os.environ.get("GROQ_API_KEY", ""),
            "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
            "OPENROUTER_MODEL": os.environ.get("OPENROUTER_MODEL", ""),
            "OPENROUTER_FALLBACK_MODELS": os.environ.get("OPENROUTER_FALLBACK_MODELS", ""),
            "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        }
        providers = build_providers(provider_cfg)
        router = ProviderRouter(
            providers,
            db,
            provider_priority=parse_provider_priority(os.environ.get("AI_PROVIDER_PRIORITY")),
        )

        user_id = 91572
        chat_id = 305
        slug = f"live-e2e-{int(time.time())}"
        project_name = slug
        requirement_text = (
            f"Build a minimal Python CLI project for Windows.\n"
            f"The entrypoint script must be exactly named {slug}.py.\n"
            "When run, it should print SKYNET_LIVE_E2E_OK and exit 0.\n"
            "Use only standard library."
        )

        bot_data = {KEY_DB: db, KEY_ROUTER: router}
        app = _HarnessApp(bot_data=bot_data, trace_fn=trace)

        def make_ctx(*, extra_user_data: dict[str, Any] | None = None) -> _SimpleContext:
            return _SimpleContext(
                user_data={} if extra_user_data is None else extra_user_data,
                bot_data=bot_data,
                application=app,
            )

        user_data: dict[str, Any] = {}

        trace("step.start", step=1, name="greeting")
        upd = _make_message_update("hi", user_id=user_id, chat_id=chat_id)
        await greeting_handler(upd, make_ctx())

        trace("step.start", step=2, name="start_project")
        upd = _make_callback_update(CB_START_PROJECT, user_id=user_id, chat_id=chat_id)
        assert await ask_project_name(upd, make_ctx()) == AWAITING_PROJECT_NAME

        trace("step.start", step=3, name="project_name")
        upd = _make_message_update(project_name, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await receive_project_name(upd, ctx) == AWAITING_PROJECT_TYPE
        user_data.update(ctx.user_data)

        trace("step.start", step=4, name="project_type")
        upd = _make_callback_update("type:python_app", user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await receive_project_type(upd, ctx) == GATHERING_REQUIREMENTS
        user_data.update(ctx.user_data)

        trace("step.start", step=5, name="requirements_message")
        upd = _make_message_update(requirement_text, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await handle_requirements_message(upd, ctx) == GATHERING_REQUIREMENTS
        user_data.update(ctx.user_data)

        trace("step.start", step=6, name="plan_generation")
        upd = _make_callback_update(CB_REQUIREMENTS_DONE, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await requirements_done_handler(upd, ctx) == REVIEWING_PLAN
        user_data.update(ctx.user_data)
        plan = str(user_data.get(_PLAN_KEY, "")).strip()
        trace("planner.plan", chars=len(plan))
        assert plan, "Planner returned empty project plan."

        trace("step.start", step=7, name="approve_plan")
        upd = _make_callback_update(CB_PLAN_APPROVE, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await approve_plan(upd, ctx) == ConversationHandler.END
        user_data.update(ctx.user_data)
        project_id = user_data["last_project_id"]
        trace("project.created", project_id=project_id, project_name=project_name)

        trace("step.start", step=8, name="start_coding")
        upd = _make_callback_update(CB_START_CODING, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data={"last_project_id": project_id})
        await start_coding_handler(upd, ctx)
        assert ctx.user_data[_CODING_PID_KEY] == project_id

        loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
        event_key = _MS_EVENT_KEY.format(uid=user_id)
        decision_key = _MS_DECISION_KEY.format(uid=user_id)

        async def auto_approve_until_complete() -> None:
            seen_loop = False
            loop_missing_ticks = 0
            for idx in range(4800):
                loop_task = bot_data.get(loop_key)
                if loop_task:
                    seen_loop = True
                    loop_missing_ticks = 0
                elif seen_loop:
                    loop_missing_ticks += 1
                    if loop_missing_ticks >= 20:
                        trace(
                            "auto_approve.loop_finished_without_task",
                            iterations=idx,
                        )
                        return
                if loop_task and loop_task.done():
                    trace("auto_approve.done", iterations=idx)
                    return
                event = bot_data.get(event_key)
                if event is not None:
                    bot_data[decision_key] = "approve"
                    event.set()
                    trace("auto_approve.approve_sent", iterations=idx)
                if idx % 40 == 0:
                    trace(
                        "auto_approve.heartbeat",
                        iterations=idx,
                        loop_present=bool(loop_task),
                        loop_done=bool(loop_task.done()) if loop_task else False,
                        waiting_for_event=bool(event is not None),
                    )
                await asyncio.sleep(0.25)
            raise AssertionError("Timed out auto-approving live milestones.")

        approve_task = asyncio.create_task(auto_approve_until_complete())
        upd = _make_callback_update(CB_CODING_GITHUB_SKIP, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data={_CODING_PID_KEY: project_id})

        trace("step.start", step=9, name="coding_loop")
        await coding_github_choice_handler(upd, ctx)
        loop_task = bot_data.get(loop_key)
        assert loop_task is not None, "Live coding loop did not start."
        await asyncio.wait_for(loop_task, timeout=1500)
        await approve_task

        tasks = await list_tasks(db, project_id=project_id)
        trace(
            "coding.tasks",
            total=len(tasks),
            statuses=[t.get("status") for t in tasks],
            titles=[str(t.get("title", ""))[:80] for t in tasks],
        )
        assert tasks, "Live coding did not create any task records."
        assert any(t["status"] == "done" for t in tasks), "No milestone completed successfully."

        all_bot_text = " ".join(str(item.get("text", "")) for item in app.bot.sent_messages)
        assert "GitHub repo created and pushed" not in all_bot_text, (
            "Live conversation E2E should skip GitHub repo creation."
        )

        trace("step.start", step=10, name="run_project")
        upd = _make_callback_update(CB_RUN_PROJECT, user_id=user_id, chat_id=chat_id)
        ctx = _SimpleContext(user_data={}, bot_data=dict(bot_data), application=app)
        await run_project_handler(upd, ctx)

        run_replies = upd.callback_query.message.replies if upd.callback_query else []
        all_replies = " ".join(str(item.get("text", "")) for item in run_replies)
        trace("run_project.replies", text_preview=all_replies[:500])
        assert "exit 0" in all_replies or "Finished (exit 0)" in all_replies, (
            f"Run project did not report success. Replies: {all_replies}"
        )
        assert "SKYNET_LIVE_E2E_OK" in all_replies, (
            f"Expected live app output not found. Replies: {all_replies}"
        )
        trace("test.success", actions_count=0)
        print(f"[LIVE TRACE] {trace_path}")
    finally:
        trace("test.cleanup", closing_db=True)
        await db.close()
        _restore_env(previous_env)
