"""Retry-coding behavior tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helpers import make_callback_update, make_context

from bot.handlers.coding import (
    _CODING_PID_KEY,
    _GITHUB_PREF_KEY,
    _MS_DECISION_KEY,
    _MS_EVENT_KEY,
    _PROJECT_ID_KEY,
    _coding_loop,
    retry_coding_handler,
)
from bot.keyboards import (
    CB_CODING_GITHUB_SKIP,
    CB_CODING_GITHUB_YES,
    CB_CODING_RETRY_PREFIX,
    CB_MAIN_MENU,
    CB_MY_PROJECTS,
    CB_START_PROJECT,
)
from bot.state import KEY_DB


def _callbacks(markup) -> list[list[str]]:
    return [[btn.callback_data for btn in row] for row in markup.inline_keyboard]


@pytest.mark.asyncio
async def test_all_failed_session_shows_retry_keyboard():
    project = {
        "id": "proj_retry",
        "name": "Retry Project",
        "project_type": "Python App",
        "status": "approved",
    }
    user_id = 42
    chat_id = 100

    app = MagicMock()
    app.bot_data = {}
    sent_text: list[str] = []
    sent_markups = []

    async def _send(_cid, text, **kwargs):
        sent_text.append(text)
        if "reply_markup" in kwargs:
            sent_markups.append(kwargs["reply_markup"])

    app.bot.send_message = AsyncMock(side_effect=_send)

    async def _approve_once():
        event_key = _MS_EVENT_KEY.format(uid=user_id)
        decision_key = _MS_DECISION_KEY.format(uid=user_id)
        for _ in range(300):
            if event_key in app.bot_data:
                app.bot_data[decision_key] = "approve"
                app.bot_data[event_key].set()
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Milestone approval event was never created.")

    approve_task = asyncio.create_task(_approve_once())

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=["Do work"])),
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch(
            "bot.handlers.coding.send_action",
            new=AsyncMock(
                return_value={
                    "status": "ok",
                    "result": {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "OLLAMA_ERROR: HTTP Error 404: Not Found",
                    },
                }
            ),
        ),
        patch("bot.handlers.coding.create_task", new=AsyncMock(return_value={"id": 11})),
        patch("bot.handlers.coding.update_task_status", new=AsyncMock()),
    ):
        await _coding_loop(app, chat_id, user_id, project, do_github=False)

    await approve_task

    assert f"run_project_{user_id}" not in app.bot_data
    assert "Retry Coding" in " ".join(sent_text)
    assert sent_markups, "Expected at least one message with a keyboard."
    rows = _callbacks(sent_markups[-1])
    assert rows[0] == [f"{CB_CODING_RETRY_PREFIX}{project['id']}"]
    assert rows[1] == [CB_MY_PROJECTS, CB_MAIN_MENU]


@pytest.mark.asyncio
async def test_retry_handler_reuses_saved_github_preference():
    user_id = 42
    project_id = "proj1"
    update = make_callback_update(f"{CB_CODING_RETRY_PREFIX}{project_id}", user_id=user_id)
    context = make_context(bot_data={KEY_DB: MagicMock()})
    context.application = MagicMock()
    context.bot_data[_GITHUB_PREF_KEY.format(uid=user_id, pid=project_id)] = True

    with (
        patch("bot.handlers.coding.ensure_user", new=AsyncMock(return_value={"id": 7})),
        patch(
            "bot.handlers.coding.get_project",
            new=AsyncMock(
                return_value={
                    "id": project_id,
                    "user_id": 7,
                    "name": "Retry App",
                    "project_type": "Python App",
                }
            ),
        ),
        patch("bot.handlers.coding._start_coding_loop", new=AsyncMock(return_value=True)) as mock_start,
    ):
        await retry_coding_handler(update, context)

    assert context.user_data[_PROJECT_ID_KEY] == project_id
    mock_start.assert_awaited_once()
    assert mock_start.call_args.kwargs["do_github"] is True


@pytest.mark.asyncio
async def test_retry_handler_falls_back_to_github_prompt_when_pref_missing():
    user_id = 42
    project_id = "proj2"
    update = make_callback_update(f"{CB_CODING_RETRY_PREFIX}{project_id}", user_id=user_id)
    context = make_context(bot_data={KEY_DB: MagicMock()})
    context.application = MagicMock()

    with (
        patch("bot.handlers.coding.ensure_user", new=AsyncMock(return_value={"id": 9})),
        patch(
            "bot.handlers.coding.get_project",
            new=AsyncMock(
                return_value={
                    "id": project_id,
                    "user_id": 9,
                    "name": "Retry Prompt App",
                    "project_type": "Python App",
                }
            ),
        ),
        patch("bot.handlers.coding._start_coding_loop", new=AsyncMock(return_value=True)) as mock_start,
    ):
        await retry_coding_handler(update, context)

    mock_start.assert_not_awaited()
    assert context.user_data[_PROJECT_ID_KEY] == project_id
    assert context.user_data[_CODING_PID_KEY] == project_id

    markup = update.callback_query.message.reply_text.call_args.kwargs["reply_markup"]
    rows = _callbacks(markup)
    assert rows[0] == [CB_CODING_GITHUB_YES]
    assert rows[1] == [CB_CODING_GITHUB_SKIP]


@pytest.mark.asyncio
async def test_retry_handler_denies_project_not_owned_by_user():
    user_id = 42
    project_id = "proj3"
    update = make_callback_update(f"{CB_CODING_RETRY_PREFIX}{project_id}", user_id=user_id)
    context = make_context(bot_data={KEY_DB: MagicMock()})
    context.application = MagicMock()

    with (
        patch("bot.handlers.coding.ensure_user", new=AsyncMock(return_value={"id": 100})),
        patch(
            "bot.handlers.coding.get_project",
            new=AsyncMock(
                return_value={
                    "id": project_id,
                    "user_id": 200,
                    "name": "Other Owner App",
                    "project_type": "Python App",
                }
            ),
        ),
        patch("bot.handlers.coding._start_coding_loop", new=AsyncMock(return_value=True)) as mock_start,
    ):
        await retry_coding_handler(update, context)

    mock_start.assert_not_awaited()
    reply_text = update.callback_query.message.reply_text.call_args.args[0]
    assert "invalid" in reply_text.lower() or "access" in reply_text.lower()
    markup = update.callback_query.message.reply_text.call_args.kwargs["reply_markup"]
    rows = _callbacks(markup)
    assert rows and rows[0] == [CB_START_PROJECT], f"Expected main menu keyboard, got: {rows}"
