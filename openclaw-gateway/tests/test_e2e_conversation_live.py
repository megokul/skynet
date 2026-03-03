"""Manual live conversation E2E against real planner + SSH worker + GitHub."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

import gateway as gateway_module
from ai.provider_router import ProviderRouter, build_providers, parse_provider_priority
from db.schema import init_db
from db.store import list_tasks
from helpers import make_callback_update, make_context, make_message_update
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
    CB_CODING_GITHUB_YES,
    CB_PLAN_APPROVE,
    CB_REQUIREMENTS_DONE,
    CB_RUN_PROJECT,
    CB_START_CODING,
    CB_START_PROJECT,
)
from bot.state import KEY_DB, KEY_ROUTER


def _make_live_trace_logger(test_name: str):
    env_path = os.environ.get("SKYNET_LIVE_TRACE_FILE", "").strip()
    if env_path:
        path = Path(env_path)
    else:
        artifacts = Path(__file__).resolve().parent / ".artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        path = artifacts / f"{test_name}-{int(time.time())}.log"
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


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_conversation_real_planner_codegen_and_github_push():
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
        pytest.skip(f"Missing live E2E SSH env vars: {', '.join(missing)}")

    with patch.dict(os.environ, {"OPENCLAW_EXECUTION_MODE": "ssh"}, clear=False):
        if not get_ssh_executor().is_configured():
            trace("test.skip", reason="SSH executor is not configured")
            pytest.skip("SSH executor is not configured for live E2E.")

        trace(
            "test.start",
            trace_file=str(trace_path),
            ssh_host=os.environ.get("OPENCLAW_SSH_HOST", ""),
            ssh_port=os.environ.get("OPENCLAW_SSH_PORT", ""),
        )

        db = await init_db(":memory:")
        try:
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
            app = MagicMock()
            app.bot_data = bot_data

            async def _send_message_trace(chat_id_arg, text, **kwargs):
                trace(
                    "bot.send_message",
                    chat_id=chat_id_arg,
                    text_preview=str(text)[:220],
                    has_reply_markup=bool(kwargs.get("reply_markup")),
                    parse_mode=str(kwargs.get("parse_mode", "")),
                )

            app.bot.send_message = AsyncMock(side_effect=_send_message_trace)

            def make_ctx(*, extra_user_data=None):
                ctx = make_context(
                    user_data={} if extra_user_data is None else extra_user_data,
                    bot_data=bot_data,
                )
                ctx.application = app
                return ctx

            user_data: dict = {}
            captured: dict[str, object] = {
                "gh_success": False,
                "gh_stdout": "",
                "repo_url": "",
                "actions": [],
            }

            async def wrapped_send_action(action, params, **kwargs):
                started = time.monotonic()
                trace(
                    "action.start",
                    action=action,
                    param_keys=sorted(params.keys()),
                    confirmed=bool(kwargs.get("confirmed")),
                    timeout=kwargs.get("timeout"),
                )
                result = await gateway_module.send_action(action, params, **kwargs)
                inner = result.get("result", result)
                trace(
                    "action.done",
                    action=action,
                    elapsed_s=round(time.monotonic() - started, 2),
                    status=result.get("status"),
                    returncode=inner.get("returncode", inner.get("exit_code")),
                    error=str(result.get("error", ""))[:220],
                )
                captured["actions"].append(action)
                if action == "gh_create_repo":
                    rc = inner.get("returncode", inner.get("exit_code", 1))
                    captured["gh_success"] = result.get("status") != "error" and rc == 0
                    stdout = str(inner.get("stdout", ""))
                    captured["gh_stdout"] = stdout
                    match = re.search(r"https://github\\.com/\\S+", stdout)
                    if match:
                        captured["repo_url"] = match.group(0).rstrip(").,")
                    trace(
                        "action.gh_create_repo",
                        success=bool(captured["gh_success"]),
                        repo_url=str(captured["repo_url"]),
                        stdout_preview=stdout[:220],
                    )
                return result

            trace("step.start", step=1, name="greeting")
            upd = make_message_update("hi", user_id=user_id, chat_id=chat_id)
            await greeting_handler(upd, make_ctx())

            trace("step.start", step=2, name="start_project")
            upd = make_callback_update(CB_START_PROJECT, user_id=user_id, chat_id=chat_id)
            assert await ask_project_name(upd, make_ctx()) == AWAITING_PROJECT_NAME

            trace("step.start", step=3, name="project_name")
            upd = make_message_update(project_name, user_id=user_id, chat_id=chat_id)
            ctx = make_ctx(extra_user_data=user_data)
            assert await receive_project_name(upd, ctx) == AWAITING_PROJECT_TYPE
            user_data.update(ctx.user_data)

            trace("step.start", step=4, name="project_type")
            upd = make_callback_update("type:python_app", user_id=user_id, chat_id=chat_id)
            ctx = make_ctx(extra_user_data=user_data)
            assert await receive_project_type(upd, ctx) == GATHERING_REQUIREMENTS
            user_data.update(ctx.user_data)

            trace("step.start", step=5, name="requirements_message")
            upd = make_message_update(requirement_text, user_id=user_id, chat_id=chat_id)
            ctx = make_ctx(extra_user_data=user_data)
            assert await handle_requirements_message(upd, ctx) == GATHERING_REQUIREMENTS
            user_data.update(ctx.user_data)

            trace("step.start", step=6, name="plan_generation")
            upd = make_callback_update(CB_REQUIREMENTS_DONE, user_id=user_id, chat_id=chat_id)
            ctx = make_ctx(extra_user_data=user_data)
            assert await requirements_done_handler(upd, ctx) == REVIEWING_PLAN
            user_data.update(ctx.user_data)
            plan = str(user_data.get(_PLAN_KEY, "")).strip()
            trace("planner.plan", chars=len(plan))
            assert plan, "Planner returned empty project plan."

            trace("step.start", step=7, name="approve_plan")
            upd = make_callback_update(CB_PLAN_APPROVE, user_id=user_id, chat_id=chat_id)
            ctx = make_ctx(extra_user_data=user_data)
            assert await approve_plan(upd, ctx) == ConversationHandler.END
            user_data.update(ctx.user_data)
            project_id = user_data["last_project_id"]
            trace("project.created", project_id=project_id, project_name=project_name)

            trace("step.start", step=8, name="start_coding")
            upd = make_callback_update(CB_START_CODING, user_id=user_id, chat_id=chat_id)
            ctx = make_ctx(extra_user_data={"last_project_id": project_id})
            await start_coding_handler(upd, ctx)
            assert ctx.user_data[_CODING_PID_KEY] == project_id

            loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
            event_key = _MS_EVENT_KEY.format(uid=user_id)
            decision_key = _MS_DECISION_KEY.format(uid=user_id)

            async def auto_approve_until_complete():
                for idx in range(4800):  # ~20 min max @ 0.25s
                    loop_task = bot_data.get(loop_key)
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
            upd = make_callback_update(CB_CODING_GITHUB_YES, user_id=user_id, chat_id=chat_id)
            ctx = make_ctx(extra_user_data={_CODING_PID_KEY: project_id})

            trace("step.start", step=9, name="coding_loop")
            with patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=wrapped_send_action)):
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
            assert bool(captured["gh_success"]), f"gh_create_repo did not succeed: {captured['gh_stdout']}"
            print(f"[LIVE E2E] GitHub repo output: {captured['gh_stdout']}")
            print(f"[LIVE E2E] Parsed repo URL: {captured['repo_url']}")

            trace("step.start", step=10, name="run_project")
            upd = make_callback_update(CB_RUN_PROJECT, user_id=user_id, chat_id=chat_id)
            ctx = make_context(bot_data=dict(bot_data))
            with patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=wrapped_send_action)):
                await run_project_handler(upd, ctx)

            all_replies = " ".join(
                str(c.args[0] if c.args else "")
                for c in upd.callback_query.message.reply_text.call_args_list
            )
            trace("run_project.replies", text_preview=all_replies[:500])
            assert "exit 0" in all_replies or "✅" in all_replies, (
                f"Run project did not report success. Replies: {all_replies}"
            )
            assert "SKYNET_LIVE_E2E_OK" in all_replies, (
                f"Expected live app output not found. Replies: {all_replies}"
            )
            trace(
                "test.success",
                repo_url=str(captured["repo_url"]),
                actions_count=len(captured["actions"]),
            )
            print(f"[LIVE TRACE] {trace_path}")
        finally:
            trace("test.cleanup", closing_db=True)
            await db.close()
