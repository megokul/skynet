"""
SKYNET Bot — Full End-to-End Integration Test

Runs the complete user journey against a real in-memory SQLite database:

    hi → Start a Project → name → type → requirements → Generate Plan
      → ✅ Approve (project written to DB)
      → Start Coding → Skip GitHub (coding loop starts)
      → 2 milestones auto-approved (tasks written to DB, status=done)
      → 🎉 complete! + 📁 path + ▶️ Run Project button
      → ▶️ Run Project → exec_command dispatched → stdout shown

Only three things are stubbed (all genuinely unreachable in automated tests):
  • Telegram bot sends  (reply_text / send_message)
  • AI router .chat()   (would hit paid API)
  • CLAW worker         (send_action / is_agent_connected)

Everything else is REAL:
  • SQLite DB via init_db(":memory:")
  • All handler logic + state transitions
  • ensure_user, create_project, create_task, update_task_status
  • Keyboard builders, slug derivation, loop coordination
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ConversationHandler

# ── Conftest helpers ──────────────────────────────────────────────────────────
from conftest import make_callback_update, make_message_update, make_context

# ── Handlers ──────────────────────────────────────────────────────────────────
from bot.handlers.greeting import greeting_handler
from bot.handlers.project import (
    ask_project_name,
    approve_plan,
    receive_project_name,
    receive_project_type,
    requirements_done_handler,
    _NAME_KEY, _TYPE_KEY, _PLAN_KEY, _REQS_HISTORY,
    AWAITING_PROJECT_NAME, AWAITING_PROJECT_TYPE,
    GATHERING_REQUIREMENTS, REVIEWING_PLAN,
)
from bot.handlers.coding import (
    start_coding_handler,
    coding_github_choice_handler,
    run_project_handler,
    _MS_EVENT_KEY, _MS_DECISION_KEY, _ACTIVE_LOOP_KEY,
    _PROJECT_ID_KEY, _CODING_PID_KEY,
)

# ── Keyboards / constants ─────────────────────────────────────────────────────
from bot.keyboards import (
    CB_START_PROJECT, CB_PLAN_APPROVE, CB_REQUIREMENTS_DONE,
    CB_START_CODING, CB_CODING_GITHUB_SKIP, CB_RUN_PROJECT,
)
from bot.state import KEY_DB, KEY_ROUTER

# ── DB ────────────────────────────────────────────────────────────────────────
from db.schema import init_db
from db.store  import get_project, list_tasks


# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_flow_hi_to_project_deliverables():
    """
    Single-shot integration: every handler runs in sequence, every DB row
    is verified immediately after the step that creates it.
    """

    # ── Real SQLite DB ────────────────────────────────────────────────────────
    db      = await init_db(":memory:")
    USER_ID = 55
    CHAT_ID = 200

    MILESTONES = [
        "Set up project structure",
        "Implement main logic",
    ]
    FAKE_PLAN = (
        "**IntegrationProject — Project Plan**\n"
        "**Overview:** A test CLI app.\n"
        "**Milestones:**\n"
        "  1. Set up project structure\n"
        "  2. Implement main logic"
    )

    # AI router: returns the preset plan for any .chat() call
    fake_router = MagicMock()
    fake_router.chat = AsyncMock(return_value=MagicMock(text=FAKE_PLAN))

    # Shared bot_data — the app mock and every context share THE SAME dict
    # so _coding_loop(app, ...) and coding_github_choice_handler(context, ...)
    # both read/write the same event keys.
    bot_data = {KEY_DB: db, KEY_ROUTER: fake_router}

    # app mock that _coding_loop uses for app.bot.send_message
    app          = MagicMock()
    app.bot_data = bot_data          # same reference
    sent_by_loop: list[str] = []

    async def _loop_send(cid, text, **kw):
        sent_by_loop.append(text)

    app.bot.send_message = AsyncMock(side_effect=_loop_send)

    # Shared persistent user_data for the project creation conversation
    user_data: dict = {}

    def ctx(extra_user_data=None):
        """Return a context mock wired to the shared bot_data."""
        c = make_context(
            user_data=dict(user_data) if extra_user_data is None else extra_user_data,
            bot_data=bot_data,
        )
        c.application = app
        return c

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: "hi" → greeting → main menu with Start a Project button
    # ─────────────────────────────────────────────────────────────────────────
    upd = make_message_update("hi", user_id=USER_ID)
    await greeting_handler(upd, ctx())

    markup   = upd.message.reply_text.call_args.kwargs["reply_markup"]
    all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert CB_START_PROJECT in all_data, "Greeting must show Start a Project"

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: Tap "Start a Project" → asks for project name
    # ─────────────────────────────────────────────────────────────────────────
    upd   = make_callback_update(CB_START_PROJECT, user_id=USER_ID)
    state = await ask_project_name(upd, ctx())
    assert state == AWAITING_PROJECT_NAME

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: Type project name
    # ─────────────────────────────────────────────────────────────────────────
    upd     = make_message_update("IntegrationProject", user_id=USER_ID)
    c       = ctx(extra_user_data=user_data)
    state   = await receive_project_name(upd, c)
    user_data.update(c.user_data)

    assert state == AWAITING_PROJECT_TYPE
    assert user_data[_NAME_KEY] == "IntegrationProject"

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: Select "Python App"
    # ─────────────────────────────────────────────────────────────────────────
    upd   = make_callback_update("type:python_app", user_id=USER_ID)
    c     = ctx(extra_user_data=user_data)
    state = await receive_project_type(upd, c)
    user_data.update(c.user_data)

    assert state == GATHERING_REQUIREMENTS
    assert user_data[_TYPE_KEY] == "Python App"

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Tap "Done — Generate Plan"
    # ─────────────────────────────────────────────────────────────────────────
    upd   = make_callback_update(CB_REQUIREMENTS_DONE, user_id=USER_ID)
    c     = ctx(extra_user_data=user_data)
    state = await requirements_done_handler(upd, c)
    user_data.update(c.user_data)

    assert state == REVIEWING_PLAN
    assert user_data[_PLAN_KEY] == FAKE_PLAN

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: Tap "✅ Approve" → project row created in real DB
    # ─────────────────────────────────────────────────────────────────────────
    upd    = make_callback_update(CB_PLAN_APPROVE, user_id=USER_ID)
    c      = ctx(extra_user_data=user_data)
    result = await approve_plan(upd, c)
    user_data.update(c.user_data)

    assert result == ConversationHandler.END, "Must end conversation on approve"

    project_id = user_data.get("last_project_id")
    assert project_id, "approve_plan must set last_project_id"

    # ── DB: project row must exist ────────────────────────────────────────────
    row = await get_project(db, project_id)
    assert row is not None,                       "Project row missing from DB after approval"
    assert row["name"]         == "IntegrationProject", f"Bad name: {row['name']}"
    assert row["project_type"] == "Python App",         f"Bad type: {row['project_type']}"
    assert row["description"]  == FAKE_PLAN,            f"Bad description (truncated): {row['description'][:60]}"
    assert row["status"]       == "approved",            f"Bad status: {row['status']}"

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: Tap "🚀 Start Coding"
    # ─────────────────────────────────────────────────────────────────────────
    upd   = make_callback_update(CB_START_CODING, user_id=USER_ID, chat_id=CHAT_ID)
    c     = ctx(extra_user_data={"last_project_id": project_id})
    await start_coding_handler(upd, c)

    assert c.user_data.get(_CODING_PID_KEY) == project_id, (
        "start_coding_handler must set _CODING_PID_KEY"
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8: Tap "Skip — just start coding" → coding loop spawned
    # ─────────────────────────────────────────────────────────────────────────
    # c.user_data still has _CODING_PID_KEY from step 7
    loop_key  = _ACTIVE_LOOP_KEY.format(uid=USER_ID)
    event_key = _MS_EVENT_KEY.format(uid=USER_ID)
    dec_key   = _MS_DECISION_KEY.format(uid=USER_ID)

    async def auto_approve_all():
        """
        Concurrently approve each milestone as _coding_loop registers its event.
        Waits for the event key to appear, fires 'approve', then waits for it to
        be consumed before moving to the next milestone.
        """
        for milestone_num in range(len(MILESTONES)):
            # wait for loop to register the event
            for _ in range(500):
                if event_key in bot_data:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError(
                    f"Milestone {milestone_num + 1} event never appeared in bot_data"
                )
            bot_data[dec_key] = "approve"
            bot_data[event_key].set()
            # wait for loop to consume the event (pops the key)
            for _ in range(500):
                if event_key not in bot_data:
                    break
                await asyncio.sleep(0.01)

    approve_task = asyncio.create_task(auto_approve_all())

    upd = make_callback_update(CB_CODING_GITHUB_SKIP, user_id=USER_ID, chat_id=CHAT_ID)
    c   = ctx(extra_user_data={_CODING_PID_KEY: project_id})

    with (
        patch("bot.handlers.coding._extract_milestones",
              new=AsyncMock(return_value=MILESTONES)),
        patch("bot.handlers.coding.is_agent_connected", return_value=True),
        patch("bot.handlers.coding.send_action",
              new=AsyncMock(return_value={"stdout": "Integration test complete!", "exit_code": 0})),
    ):
        await coding_github_choice_handler(upd, c)

        # Retrieve the background task from the shared bot_data
        loop_task = bot_data.get(loop_key)
        assert loop_task is not None, "coding_github_choice_handler must create a loop task"

        # Wait for the full coding loop to run to completion
        await asyncio.wait_for(loop_task, timeout=10)

    await approve_task  # ensure no dangling task

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 9: Verify tasks written to real DB, all status=done
    # ─────────────────────────────────────────────────────────────────────────
    tasks = await list_tasks(db, project_id=project_id)
    assert len(tasks) == len(MILESTONES), (
        f"Expected {len(MILESTONES)} task rows in DB; got {len(tasks)}: {tasks}"
    )
    for t in tasks:
        assert t["status"] == "done", (
            f"Task {t['title']!r} should be status=done; got {t['status']!r}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 10: Verify completion message has 🎉, 📁, and project_id stored
    # ─────────────────────────────────────────────────────────────────────────
    done_msgs = [m for m in sent_by_loop if "🎉" in m]
    assert done_msgs, f"No completion message sent; all messages: {sent_by_loop}"
    assert "📁" in done_msgs[0], (
        f"Completion message must include folder path; got: {done_msgs[0]!r}"
    )

    assert bot_data.get(f"run_project_{USER_ID}") == project_id, (
        "run_project_{USER_ID} must be stored in bot_data after loop completion"
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 11: Tap "▶️ Run Project" → exec_command dispatched, stdout shown
    # ─────────────────────────────────────────────────────────────────────────
    upd = make_callback_update(CB_RUN_PROJECT, user_id=USER_ID, chat_id=CHAT_ID)
    # Pass the run_project key in bot_data
    run_bot_data = dict(bot_data)  # real DB is in here already

    c = make_context(bot_data=run_bot_data)

    with (
        patch("bot.handlers.coding.is_agent_connected", return_value=True),
        patch("bot.handlers.coding.send_action",
              new=AsyncMock(return_value={
                  "stdout": "Hello from IntegrationProject!\n",
                  "exit_code": 0,
              })) as mock_run,
    ):
        await run_project_handler(upd, c)

    # exec_command dispatched
    mock_run.assert_awaited_once()
    action, params = mock_run.call_args.args[:2]
    assert action == "exec_command", f"Expected exec_command; got {action!r}"
    assert "integrationproject.py" in params["command"], (
        f"Expected slug.py in command; got {params['command']!r}"
    )
    assert "integrationproject" in params["working_dir"].lower(), (
        f"working_dir should contain slug; got {params['working_dir']!r}"
    )

    # Output and exit status shown to user
    all_replies = " ".join(
        (c.args[0] if c.args else "") if hasattr(c, 'args') else str(c)
        for c in upd.callback_query.message.reply_text.call_args_list
    )
    assert "Hello from IntegrationProject" in all_replies, (
        f"stdout must appear in chat; got: {all_replies!r}"
    )
    assert "exit 0" in all_replies or "✅" in all_replies

    # ── Done ──────────────────────────────────────────────────────────────────
    await db.close()
