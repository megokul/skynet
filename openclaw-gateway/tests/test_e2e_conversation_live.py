"""Live handler-path conversation E2E (simulated Telegram transport; real planner + worker).

Transport modes (SKYNET_E2E_TRANSPORT env var):
  websocket  — (default) starts a gateway WS server + spawns openclaw-agent subprocess
  ssh        — legacy SSH-only mode (requires OPENCLAW_SSH_HOST/OPENCLAW_SSH_USER)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram.ext import ConversationHandler

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = REPO_ROOT / "openclaw-gateway"
for candidate in (str(REPO_ROOT), str(GATEWAY_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from live_settings import bootstrap_gateway_runtime
from live_diagnostics import LiveContainerDiagnostics, make_live_trace_logger

bootstrap_gateway_runtime()

import config as cfg
from ai.provider_router import ProviderRouter, build_providers, parse_provider_priority
from db.schema import init_db
from db.store import list_tasks
from ssh_tunnel_executor import get_ssh_executor

from bot.handlers.coding import (
    _ACTIVE_LOOP_KEY,
    _CODING_PID_KEY,
    _MS_DECISION_KEY,
    _MS_EVENT_KEY,
    _tracker_state_key,
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
        self._next_message_id = 1

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Any:
        message_id = self._next_message_id
        self._next_message_id += 1
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
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
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        **kwargs: Any,
    ) -> None:
        for payload in reversed(self.sent_messages):
            if int(payload.get("message_id", 0) or 0) != int(message_id):
                continue
            payload["chat_id"] = chat_id
            payload["text"] = str(text)
            payload["kwargs"] = dict(kwargs)
            self._trace(
                "bot.edit_message_text",
                chat_id=chat_id,
                message_id=message_id,
                text_preview=str(text)[:220],
                has_reply_markup=bool(kwargs.get("reply_markup")),
                parse_mode=str(kwargs.get("parse_mode", "")),
            )
            return
        raise RuntimeError("message to edit not found")


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
    trace_path, trace = make_live_trace_logger("live-conversation-e2e")
    if os.environ.get("SKYNET_E2E_LIVE") != "1":
        trace("test.skip", reason="SKYNET_E2E_LIVE is not 1")
        pytest.skip("Set SKYNET_E2E_LIVE=1 to run live conversation E2E.")

    transport = os.environ.get("SKYNET_E2E_TRANSPORT", "websocket").strip().lower()
    use_websocket = transport != "ssh"
    trace("transport.mode", transport=transport, use_websocket=use_websocket)

    if use_websocket:
        if not os.environ.get("SKYNET_AUTH_TOKEN"):
            trace("test.skip", reason="Missing SKYNET_AUTH_TOKEN for WebSocket E2E")
            _skip_or_fail_live("Missing SKYNET_AUTH_TOKEN for WebSocket E2E")
    else:
        missing = [
            key for key in ("OPENCLAW_SSH_HOST", "OPENCLAW_SSH_USER")
            if not os.environ.get(key)
        ]
        if missing:
            trace("test.skip", reason="Missing SSH env vars", missing=missing)
            _skip_or_fail_live("Missing live E2E SSH env vars", ", ".join(missing))

    if use_websocket:
        previous_env, _ = _with_env(
            {
                "OPENCLAW_EXECUTION_MODE": "agent_preferred",
                "SKYNET_WEBSOCKET_FALLBACK_TO_SSH": "0",
                "SKYNET_STRICT_EMPTY_OUTPUT_EMERGENCY_SCAFFOLD": "1",
            }
        )
    else:
        previous_env, _ = _with_env(
            {
                "OPENCLAW_EXECUTION_MODE": "ssh",
                "SKYNET_STRICT_EMPTY_OUTPUT_EMERGENCY_SCAFFOLD": "1",
            }
        )

    ws_server = None
    agent_proc = None
    container_diagnostics = LiveContainerDiagnostics(trace_fn=trace)
    bundle_status = "ok"
    bundle_reason = "test_success"
    db = await init_db(":memory:")
    try:
        try:
            await container_diagnostics.start()
        except Exception:
            bundle_status = "fail"
            bundle_reason = "container_stream_unavailable"
            raise

        if use_websocket:
            import gateway as _gw

            ws_server = await _gw.start_ws_server()
            trace("websocket.server_started", port=_gw.cfg.WS_PORT)

            # Spawn openclaw-agent as subprocess
            agent_root = str(Path(__file__).resolve().parents[2] / "openclaw-agent")
            agent_env = os.environ.copy()
            agent_env["SKYNET_AUTH_TOKEN"] = os.environ["SKYNET_AUTH_TOKEN"]
            agent_env["SKYNET_GATEWAY_URL"] = f"ws://localhost:{_gw.cfg.WS_PORT}/agent/ws"
            project_base = os.environ.get(
                "OPENCLAW_PROJECT_BASE_DIR", "E:/SKYNET-SANDBOX/Projects"
            )
            agent_env["SKYNET_ALLOWED_ROOTS"] = os.environ.get(
                "SKYNET_ALLOWED_ROOTS",
                os.environ.get("OPENCLAW_SSH_ALLOWED_ROOTS", project_base),
            )
            agent_env["SKYNET_LOG_LEVEL"] = "WARNING"
            agent_proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=agent_root,
                env=agent_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            trace("websocket.agent_spawned", pid=agent_proc.pid)

            # Wait for agent to connect
            try:
                await asyncio.wait_for(_gw.agent_connected.wait(), timeout=15)
            except asyncio.TimeoutError:
                stderr_tail = ""
                if agent_proc.poll() is not None:
                    stderr_tail = (agent_proc.stderr.read() or b"").decode(errors="replace")[-500:]
                trace(
                    "websocket.agent_connect_timeout",
                    agent_alive=agent_proc.poll() is None,
                    stderr_tail=stderr_tail,
                )
                _skip_or_fail_live(
                    "WebSocket agent did not connect within 15s",
                    stderr_tail or "agent may have crashed",
                )
            trace("websocket.agent_connected", worker_available=_gw.is_agent_connected())
        else:
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
            container_stream_enabled=container_diagnostics.config["stream_enabled"],
            container_stream_required=container_diagnostics.config["require_stream"],
            container_stream_sources=container_diagnostics.config["sources"],
        )

        providers = build_providers(cfg.get_provider_config())
        router = ProviderRouter(
            providers,
            db,
            provider_priority=parse_provider_priority(cfg.AI_PROVIDER_PRIORITY),
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
        tracker_key = _tracker_state_key(user_id, project_id)

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

        async def wait_for_loop_terminal() -> None:
            terminal_grace_seconds = 10
            max_wait_seconds = max(
                terminal_grace_seconds,
                int(getattr(cfg, "TELEGRAM_TRACKER_STUCK_EXIT_SECONDS", 300) or 300) + 60,
            )
            started = asyncio.get_running_loop().time()
            while True:
                loop_task = bot_data.get(loop_key)
                if loop_task is None:
                    trace("coding_loop.monitor", loop_present=False, reason="loop_task_missing")
                    return
                if loop_task.done():
                    await loop_task
                    trace("coding_loop.monitor", loop_present=True, reason="loop_task_done")
                    return
                tracker_state = bot_data.get(tracker_key)
                if isinstance(tracker_state, dict):
                    status = str(tracker_state.get("status") or "").strip().lower()
                    phase = str(tracker_state.get("phase") or "").strip().lower()
                    detail = str(tracker_state.get("phase_detail") or "").strip()
                    if phase == "finalization" and status in {"failed", "stopped", "completed"}:
                        trace(
                            "coding_loop.monitor.terminal_tracker",
                            status=status,
                            phase=phase,
                            detail=detail[:220],
                        )
                        await asyncio.wait_for(loop_task, timeout=terminal_grace_seconds)
                        return
                if (asyncio.get_running_loop().time() - started) >= max_wait_seconds:
                    raise AssertionError(
                        f"Live coding loop did not exit within {max_wait_seconds}s."
                    )
                await asyncio.sleep(1)

        approve_task = asyncio.create_task(auto_approve_until_complete())
        upd = _make_callback_update(CB_CODING_GITHUB_SKIP, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data={_CODING_PID_KEY: project_id})

        trace("step.start", step=9, name="coding_loop")
        await coding_github_choice_handler(upd, ctx)
        loop_task = bot_data.get(loop_key)
        assert loop_task is not None, "Live coding loop did not start."
        await wait_for_loop_terminal()
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
        if use_websocket and _bool_env("SKYNET_E2E_REQUIRE_WEBSOCKET_PRIMARY", False):
            import gateway as _gw_check
            assert _gw_check.is_agent_connected(), (
                "WebSocket agent disconnected before E2E completed."
            )
            trace("websocket.transport_verified", agent_connected=True)

        if container_diagnostics.config["require_stream"] and container_diagnostics.has_errors():
            bundle_status = "fail"
            bundle_reason = "container_stream_unhealthy"
            raise AssertionError(
                f"CONTAINER_LOG_STREAM_UNAVAILABLE: {container_diagnostics.error_tail()[-5:]}"
            )

        trace("test.success", actions_count=0, transport=transport)
        print(f"[LIVE TRACE] {trace_path}")
    except Exception:
        bundle_status = "fail"
        if bundle_reason == "test_success":
            bundle_reason = "test_failure"
        raise
    finally:
        await container_diagnostics.emit_bundle(
            status=bundle_status,
            reason=bundle_reason,
            flow="conversation",
        )
        await container_diagnostics.stop()
        trace("test.cleanup", closing_db=True, transport=transport)
        if agent_proc is not None:
            agent_proc.terminate()
            try:
                agent_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                agent_proc.kill()
            trace("websocket.agent_stopped", pid=agent_proc.pid)
        if ws_server is not None:
            ws_server.close()
            await ws_server.wait_closed()
            trace("websocket.server_stopped")
        await db.close()
        _restore_env(previous_env)
