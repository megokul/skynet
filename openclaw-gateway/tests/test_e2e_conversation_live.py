"""Manual live conversation E2E against real planner + SSH worker + GitHub."""

from __future__ import annotations

import asyncio
import os
import re
import time
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


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_conversation_real_planner_codegen_and_github_push():
    if os.environ.get("SKYNET_E2E_LIVE") != "1":
        pytest.skip("Set SKYNET_E2E_LIVE=1 to run live conversation E2E.")

    missing = [
        key for key in ("OPENCLAW_SSH_HOST", "OPENCLAW_SSH_USER")
        if not os.environ.get(key)
    ]
    if missing:
        pytest.skip(f"Missing live E2E SSH env vars: {', '.join(missing)}")

    with patch.dict(os.environ, {"OPENCLAW_EXECUTION_MODE": "ssh"}, clear=False):
        if not get_ssh_executor().is_configured():
            pytest.skip("SSH executor is not configured for live E2E.")

        db = await init_db(":memory:")
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
        app.bot.send_message = AsyncMock()

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
            result = await gateway_module.send_action(action, params, **kwargs)
            captured["actions"].append(action)
            if action == "gh_create_repo":
                inner = result.get("result", result)
                rc = inner.get("returncode", inner.get("exit_code", 1))
                captured["gh_success"] = result.get("status") != "error" and rc == 0
                stdout = str(inner.get("stdout", ""))
                captured["gh_stdout"] = stdout
                match = re.search(r"https://github\\.com/\\S+", stdout)
                if match:
                    captured["repo_url"] = match.group(0).rstrip(").,")
            return result

        # Step 1: greeting
        upd = make_message_update("hi", user_id=user_id, chat_id=chat_id)
        await greeting_handler(upd, make_ctx())

        # Step 2: project start
        upd = make_callback_update(CB_START_PROJECT, user_id=user_id, chat_id=chat_id)
        assert await ask_project_name(upd, make_ctx()) == AWAITING_PROJECT_NAME

        # Step 3: project name
        upd = make_message_update(project_name, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await receive_project_name(upd, ctx) == AWAITING_PROJECT_TYPE
        user_data.update(ctx.user_data)

        # Step 4: project type
        upd = make_callback_update("type:python_app", user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await receive_project_type(upd, ctx) == GATHERING_REQUIREMENTS
        user_data.update(ctx.user_data)

        # Step 5: real planner requirements turn
        upd = make_message_update(requirement_text, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await handle_requirements_message(upd, ctx) == GATHERING_REQUIREMENTS
        user_data.update(ctx.user_data)

        # Step 6: real planner plan generation
        upd = make_callback_update(CB_REQUIREMENTS_DONE, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await requirements_done_handler(upd, ctx) == REVIEWING_PLAN
        user_data.update(ctx.user_data)
        plan = str(user_data.get(_PLAN_KEY, "")).strip()
        assert plan, "Planner returned empty project plan."

        # Step 7: approve plan
        upd = make_callback_update(CB_PLAN_APPROVE, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data=user_data)
        assert await approve_plan(upd, ctx) == ConversationHandler.END
        user_data.update(ctx.user_data)
        project_id = user_data["last_project_id"]

        # Step 8: start coding
        upd = make_callback_update(CB_START_CODING, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data={"last_project_id": project_id})
        await start_coding_handler(upd, ctx)
        assert ctx.user_data[_CODING_PID_KEY] == project_id

        # Step 9: GitHub path + auto-approve milestones
        loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
        event_key = _MS_EVENT_KEY.format(uid=user_id)
        decision_key = _MS_DECISION_KEY.format(uid=user_id)

        async def auto_approve_until_complete():
            for _ in range(4800):  # ~20 min max @ 0.25s
                loop_task = bot_data.get(loop_key)
                if loop_task and loop_task.done():
                    return
                event = bot_data.get(event_key)
                if event is not None:
                    bot_data[decision_key] = "approve"
                    event.set()
                await asyncio.sleep(0.25)
            raise AssertionError("Timed out auto-approving live milestones.")

        approve_task = asyncio.create_task(auto_approve_until_complete())
        upd = make_callback_update(CB_CODING_GITHUB_YES, user_id=user_id, chat_id=chat_id)
        ctx = make_ctx(extra_user_data={_CODING_PID_KEY: project_id})

        with patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=wrapped_send_action)):
            await coding_github_choice_handler(upd, ctx)
            loop_task = bot_data.get(loop_key)
            assert loop_task is not None, "Live coding loop did not start."
            await asyncio.wait_for(loop_task, timeout=1500)
        await approve_task

        tasks = await list_tasks(db, project_id=project_id)
        assert tasks, "Live coding did not create any task records."
        assert any(t["status"] == "done" for t in tasks), "No milestone completed successfully."
        assert bool(captured["gh_success"]), f"gh_create_repo did not succeed: {captured['gh_stdout']}"
        print(f"[LIVE E2E] GitHub repo output: {captured['gh_stdout']}")
        print(f"[LIVE E2E] Parsed repo URL: {captured['repo_url']}")

        # Step 10: run project
        upd = make_callback_update(CB_RUN_PROJECT, user_id=user_id, chat_id=chat_id)
        ctx = make_context(bot_data=dict(bot_data))
        with patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=wrapped_send_action)):
            await run_project_handler(upd, ctx)

        all_replies = " ".join(
            str(c.args[0] if c.args else "")
            for c in upd.callback_query.message.reply_text.call_args_list
        )
        assert "exit 0" in all_replies or "✅" in all_replies, (
            f"Run project did not report success. Replies: {all_replies}"
        )
        assert "SKYNET_LIVE_E2E_OK" in all_replies, (
            f"Expected live app output not found. Replies: {all_replies}"
        )

        await db.close()
