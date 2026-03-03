"""Simulate a Telegram chat journey and reproduce coding-session setup failures."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from helpers import make_callback_update, make_context, make_message_update

from bot.handlers.coding import (
    _ACTIVE_LOOP_KEY,
    _CODING_PID_KEY,
    coding_github_choice_handler,
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
    CB_START_CODING,
    CB_START_PROJECT,
)
from bot.state import KEY_DB, KEY_ROUTER
from db.schema import init_db


def _worker_ok(returncode: int = 0, stdout: str = "", stderr: str = "") -> dict:
    return {
        "status": "success",
        "result": {
            "returncode": int(returncode),
            "stdout": stdout,
            "stderr": stderr,
        },
    }


@pytest.mark.asyncio
async def test_telegram_chat_simulates_claude_missing_preflight():
    """
    Simulate the same chat flow users do in Telegram and verify coding setup
    fails fast when Claude CLI is unavailable on the worker.
    """
    db = await init_db(":memory:")
    user_id = 42
    chat_id = 100

    fake_plan = (
        "**joo — Project Plan**\n"
        "**Overview:** A lightweight Python script for Windows that executes via terminal, "
        "displays a hi popup, and emits a short beep.\n"
        "**Core Features:**\n"
        "  - Windows popup with hi text\n"
        "  - Short beep sound on execution\n"
        "**Tech Stack:** Python 3.10+\n"
        "**Project Structure:** joo.py, README.md\n"
        "**Milestones:**\n"
        "  1. Implement basic script structure with Windows API popup\n"
        "  2. Integrate winsound for short beep and finalize terminal execution flow\n"
        "**Open Questions:** None"
    )

    fake_router = MagicMock()
    fake_router.chat = AsyncMock(
        side_effect=[
            MagicMock(text="I have everything I need. Send /plan to generate your project plan."),
            MagicMock(text=fake_plan),
        ]
    )

    bot_data = {KEY_DB: db, KEY_ROUTER: fake_router}
    app = MagicMock()
    app.bot_data = bot_data
    sent_text: list[str] = []

    async def _send(_cid, text, **_kwargs):
        sent_text.append(str(text))

    app.bot.send_message = AsyncMock(side_effect=_send)

    def make_ctx(*, extra_user_data=None):
        ctx = make_context(
            user_data={} if extra_user_data is None else extra_user_data,
            bot_data=bot_data,
        )
        ctx.application = app
        return ctx

    async def fake_send_action(action, params, **_kwargs):
        if action == "create_directory":
            return _worker_ok(0, f"Created {params['directory']}", "")
        if action == "check_coding_agents":
            return _worker_ok(
                0,
                (
                    "codex: available (codex)\n"
                    "claude: unavailable (expected binary: claude)\n"
                    "cline: unavailable (expected binary: cline)"
                ),
                "",
            )
        raise AssertionError(f"Unexpected action in simulation: {action}")

    user_data: dict = {}

    # 1) hi
    upd = make_message_update("hi", user_id=user_id, chat_id=chat_id)
    await greeting_handler(upd, make_ctx())

    # 2) start project
    upd = make_callback_update(CB_START_PROJECT, user_id=user_id, chat_id=chat_id)
    assert await ask_project_name(upd, make_ctx()) == AWAITING_PROJECT_NAME

    # 3) project name
    upd = make_message_update("joo", user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await receive_project_name(upd, ctx) == AWAITING_PROJECT_TYPE
    user_data.update(ctx.user_data)

    # 4) project type = other
    upd = make_callback_update("type:other", user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await receive_project_type(upd, ctx) == GATHERING_REQUIREMENTS
    user_data.update(ctx.user_data)

    # 5) requirements message
    upd = make_message_update(
        "python script windows terminal execution popup-hi short beep sound",
        user_id=user_id,
        chat_id=chat_id,
    )
    ctx = make_ctx(extra_user_data=user_data)
    assert await handle_requirements_message(upd, ctx) == GATHERING_REQUIREMENTS
    user_data.update(ctx.user_data)

    # 6) generate plan
    upd = make_callback_update(CB_REQUIREMENTS_DONE, user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await requirements_done_handler(upd, ctx) == REVIEWING_PLAN
    user_data.update(ctx.user_data)
    assert user_data.get(_PLAN_KEY), "Plan was not generated."

    # 7) approve plan
    upd = make_callback_update(CB_PLAN_APPROVE, user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data=user_data)
    assert await approve_plan(upd, ctx) == ConversationHandler.END
    user_data.update(ctx.user_data)
    project_id = user_data["last_project_id"]

    # 8) start coding
    upd = make_callback_update(CB_START_CODING, user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data={"last_project_id": project_id})
    await start_coding_handler(upd, ctx)
    assert ctx.user_data[_CODING_PID_KEY] == project_id

    # 9) choose "skip github", run coding loop, and await completion
    loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
    upd = make_callback_update(CB_CODING_GITHUB_SKIP, user_id=user_id, chat_id=chat_id)
    ctx = make_ctx(extra_user_data={_CODING_PID_KEY: project_id})

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=["m1", "m2"])),
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=fake_send_action)),
    ):
        await coding_github_choice_handler(upd, ctx)
        loop_task = bot_data.get(loop_key)
        assert loop_task is not None, "Coding loop did not start."
        await asyncio.wait_for(loop_task, timeout=20)

    # The simulation should fail at preflight and never enter milestone execution.
    all_text = "\n".join(sent_text)
    assert "Coding preflight failed" in all_text, all_text
    assert "claude: unavailable" in all_text.lower(), all_text
    assert "Milestone 1/2" not in all_text, all_text

    await db.close()
