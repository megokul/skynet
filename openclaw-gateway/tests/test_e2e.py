"""
SKYNET Bot — End-to-End Handler Tests

Three full-stack scenarios (all Telegram + DB + AI + gateway calls are mocked):

  TC-1  FULL PROJECT CREATION FLOW
        hi → greeting → "Start a Project" → name → type → requirements →
        "Done — Generate Plan" → plan shown → "✅ Approve" →
        project saved in DB, no GitHub question, "Start Coding" button shown.

  TC-2  STOP SESSION MID-CODING
        "Start Coding" → "Skip GitHub" → coding loop starts →
        milestone 1 shown → user taps "🛑 Stop Session" →
        loop exits with stop message, no "🎉 complete" sent.

  TC-3  RUN PROJECT AFTER COMPLETION
        "▶️ Run Project" → exec_command dispatched to CLAW worker →
        stdout shown in chat → exit status shown.
        ALSO: worker-disconnected sub-case returns graceful error.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from telegram.ext import ConversationHandler

# ── Helpers from conftest ─────────────────────────────────────────────────────
from conftest import make_callback_update, make_message_update, make_context

# ── Modules under test ────────────────────────────────────────────────────────
from bot.handlers.greeting import greeting_handler
from bot.handlers.project import (
    approve_plan,
    ask_project_name,
    receive_project_name,
    receive_project_type,
    requirements_done_handler,
    _do_generate_plan,
    _NAME_KEY,
    _TYPE_KEY,
    _PLAN_KEY,
    _REQS_HISTORY,
    AWAITING_PROJECT_NAME,
    AWAITING_PROJECT_TYPE,
    GATHERING_REQUIREMENTS,
    REVIEWING_PLAN,
)
from bot.handlers.coding import (
    coding_github_choice_handler,
    stop_milestone_handler,
    run_project_handler,
    _coding_loop,
    _MS_EVENT_KEY,
    _MS_DECISION_KEY,
    _ACTIVE_LOOP_KEY,
    _CODING_PID_KEY,
    _PROJECT_ID_KEY,
)
from bot.keyboards import (
    CB_PLAN_APPROVE,
    CB_PLAN_CHANGES,
    CB_REQUIREMENTS_DONE,
    CB_START_PROJECT,
    CB_START_CODING,
    CB_CODING_GITHUB_SKIP,
    CB_CODING_GITHUB_YES,
    CB_MILESTONE_APPROVE,
    CB_MILESTONE_STOP,
    CB_RUN_PROJECT,
)
from bot.state import KEY_DB, KEY_ROUTER


# ═══════════════════════════════════════════════════════════════════════════════
# TEST CASE 1  —  Full project creation flow (hi → approve plan → Start Coding)
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjectCreationFlow:
    """TC-1: From greeting all the way to 'Start Coding' button."""

    # ── Step 1: "hi" shows main menu ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_greeting_shows_main_menu(self):
        update  = make_message_update("hi")
        context = make_context()

        await greeting_handler(update, context)

        update.message.reply_text.assert_awaited_once()
        call_kwargs = update.message.reply_text.call_args.kwargs
        markup = call_kwargs.get("reply_markup")
        assert markup is not None
        all_data = [btn.callback_data
                    for row in markup.inline_keyboard for btn in row]
        assert CB_START_PROJECT in all_data, (
            "Main menu must contain 'Start a Project' button"
        )

    # ── Step 2: "Start a Project" → asks for name ─────────────────────────────

    @pytest.mark.asyncio
    async def test_ask_project_name(self):
        update  = make_callback_update(CB_START_PROJECT)
        context = make_context()

        state = await ask_project_name(update, context)

        assert state == AWAITING_PROJECT_NAME
        update.callback_query.message.reply_text.assert_awaited_once()
        reply = update.callback_query.message.reply_text.call_args.args[0]
        assert "project" in reply.lower() or "name" in reply.lower()

    # ── Step 3: User types project name → asks for type ───────────────────────

    @pytest.mark.asyncio
    async def test_receive_project_name_stores_and_asks_type(self):
        update  = make_message_update("SkyApp")
        context = make_context()

        state = await receive_project_name(update, context)

        assert state == AWAITING_PROJECT_TYPE
        assert context.user_data[_NAME_KEY] == "SkyApp"
        # Should show project-type keyboard
        markup = update.message.reply_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        all_data = [btn.callback_data
                    for row in markup.inline_keyboard for btn in row]
        assert any(d.startswith("type:") for d in all_data)

    @pytest.mark.asyncio
    async def test_receive_project_name_rejects_empty(self):
        update  = make_message_update("   ")
        context = make_context()

        state = await receive_project_name(update, context)

        assert state == AWAITING_PROJECT_NAME
        assert _NAME_KEY not in context.user_data

    # ── Step 4: User picks project type → requirements chat opens ─────────────

    @pytest.mark.asyncio
    async def test_receive_project_type_starts_requirements(self):
        update  = make_callback_update("type:python_app")
        context = make_context(user_data={_NAME_KEY: "SkyApp"})

        state = await receive_project_type(update, context)

        assert state == GATHERING_REQUIREMENTS
        assert context.user_data[_TYPE_KEY] == "Python App"
        # Requirements history seeded with assistant opening
        history = context.user_data.get(_REQS_HISTORY, [])
        assert len(history) >= 1
        assert history[0]["role"] == "assistant"

    # ── Step 5: User taps "Done — Generate Plan" → plan generated ─────────────

    @pytest.mark.asyncio
    async def test_requirements_done_generates_plan(self):
        fake_plan = (
            "**SkyApp — Project Plan**\n"
            "**Overview:** A simple Python CLI.\n"
            "**Milestones:**\n  1. Setup\n  2. Core logic"
        )
        fake_router = MagicMock()
        fake_router.chat = AsyncMock(return_value=MagicMock(text=fake_plan))

        update  = make_callback_update(CB_REQUIREMENTS_DONE)
        context = make_context(
            user_data={
                _NAME_KEY:     "SkyApp",
                _TYPE_KEY:     "Python App",
                _REQS_HISTORY: [{"role": "assistant", "content": "What does it do?"}],
            },
            bot_data={KEY_ROUTER: fake_router},
        )

        state = await requirements_done_handler(update, context)

        assert state == REVIEWING_PLAN
        assert context.user_data[_PLAN_KEY] == fake_plan
        # Plan shown to user with Approve / Request Changes buttons
        markup = update.callback_query.message.reply_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        all_data = [btn.callback_data
                    for row in markup.inline_keyboard for btn in row]
        assert CB_PLAN_APPROVE in all_data
        assert CB_PLAN_CHANGES in all_data

    # ── Step 6: "✅ Approve" saves to DB, shows Start Coding — no GitHub ask ───

    @pytest.mark.asyncio
    async def test_approve_plan_saves_project_no_github_question(self):
        fake_user    = {"id": 7, "telegram_user_id": 42}
        fake_project = {
            "id":           "abc123",
            "name":         "SkyApp",
            "project_type": "Python App",
            "status":       "approved",
        }

        update  = make_callback_update(CB_PLAN_APPROVE, user_id=42)
        context = make_context(
            user_data={
                _NAME_KEY: "SkyApp",
                _TYPE_KEY: "Python App",
                _PLAN_KEY: "## Plan\n1. Setup\n2. Core logic",
            },
            bot_data={KEY_DB: MagicMock()},
        )

        with (
            patch("bot.handlers.project.ensure_user",    new=AsyncMock(return_value=fake_user)),
            patch("bot.handlers.project.create_project", new=AsyncMock(return_value=fake_project)),
        ):
            result = await approve_plan(update, context)

        # Must end the conversation immediately — no AWAITING_GITHUB redirect
        assert result == ConversationHandler.END, (
            "approve_plan must return END, not redirect to a GitHub state"
        )

        # Reply must contain project name and "saved" indicator
        reply_text = update.callback_query.message.reply_text.call_args.args[0]
        assert "SkyApp" in reply_text
        assert "✅" in reply_text or "saved" in reply_text.lower()

        # No GitHub question in the reply
        assert "github" not in reply_text.lower(), (
            "Project approval must NOT ask about GitHub"
        )

        # Start Coding button must be present
        markup = update.callback_query.message.reply_text.call_args.kwargs.get("reply_markup")
        all_data = [btn.callback_data
                    for row in markup.inline_keyboard for btn in row]
        assert CB_START_CODING in all_data, (
            "'Start Coding' button must appear after plan approval"
        )

        # project_id preserved for the coding handler
        assert context.user_data.get("last_project_id") == "abc123"

        # Temp user_data keys cleared
        assert _NAME_KEY     not in context.user_data
        assert _TYPE_KEY     not in context.user_data
        assert _PLAN_KEY     not in context.user_data
        assert _REQS_HISTORY not in context.user_data


# ═══════════════════════════════════════════════════════════════════════════════
# TEST CASE 2  —  Stop session mid-coding
# ═══════════════════════════════════════════════════════════════════════════════

class TestStopSession:
    """TC-2: User aborts the coding loop with 🛑 Stop Session."""

    PROJECT = {
        "id":           "proj1",
        "name":         "SkyApp",
        "project_type": "Python App",
        "description":  "## Plan\n1. Set up project\n2. Add main logic",
        "status":       "approved",
    }

    # ── 2a: stop handler signals the event ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stop_handler_fires_stop_decision(self):
        user_id   = 42
        event     = asyncio.Event()
        event_key = _MS_EVENT_KEY.format(uid=user_id)
        dec_key   = _MS_DECISION_KEY.format(uid=user_id)

        update  = make_callback_update(CB_MILESTONE_STOP, user_id=user_id)
        context = make_context(bot_data={event_key: event})

        await stop_milestone_handler(update, context)

        assert event.is_set(), "Event must be set so the loop unblocks"
        assert context.bot_data[dec_key] == "stop", (
            "Decision must be 'stop' not 'skip'"
        )
        update.callback_query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_handler_graceful_when_no_session(self):
        """Tapping Stop with no active session should show a friendly message."""
        update  = make_callback_update(CB_MILESTONE_STOP, user_id=99)
        context = make_context()  # no event in bot_data

        await stop_milestone_handler(update, context)  # must not raise

        update.callback_query.message.reply_text.assert_awaited_once()
        msg = update.callback_query.message.reply_text.call_args.args[0]
        assert "no active" in msg.lower() or "session" in msg.lower()

    # ── 2b: the coding loop exits cleanly on stop ─────────────────────────────

    @pytest.mark.asyncio
    async def test_coding_loop_stops_on_stop_decision(self):
        """
        Full _coding_loop run with 2 milestones.
        A concurrent task triggers 'stop' as soon as the first milestone
        event is registered, then the loop must:
          • send "🛑 Session stopped at milestone 1/2"
          • NOT send the "🎉 complete" message
        """
        user_id  = 42
        chat_id  = 100
        milestones = ["Set up project structure", "Add main logic"]

        app          = MagicMock()
        app.bot_data = {}
        sent: list[str] = []

        async def _send(cid, text, **kwargs):
            sent.append(text)

        app.bot.send_message = AsyncMock(side_effect=_send)

        async def trigger_stop():
            """Wait until the loop registers the event, then fire stop."""
            event_key = _MS_EVENT_KEY.format(uid=user_id)
            dec_key   = _MS_DECISION_KEY.format(uid=user_id)
            for _ in range(200):          # up to 2 s
                if event_key in app.bot_data:
                    app.bot_data[dec_key] = "stop"
                    app.bot_data[event_key].set()
                    return
                await asyncio.sleep(0.01)

        trigger_task = asyncio.create_task(trigger_stop())

        with (
            patch("bot.handlers.coding._extract_milestones",
                  new=AsyncMock(return_value=milestones)),
            patch("bot.handlers.coding.is_agent_connected", return_value=True),
        ):
            await _coding_loop(app, chat_id, user_id, self.PROJECT, do_github=False)

        await trigger_task  # ensure no dangling task

        # Must have sent a stop message
        stop_msgs = [m for m in sent if "🛑" in m or "stopped" in m.lower()]
        assert stop_msgs, f"Expected a stop message; got: {sent}"
        assert "1/2" in stop_msgs[0], (
            f"Stop message should say milestone 1/2; got: {stop_msgs[0]!r}"
        )

        # Must NOT have sent the completion message
        done_msgs = [m for m in sent if "🎉" in m]
        assert not done_msgs, (
            f"Should NOT send '🎉 complete' after stop; got: {done_msgs}"
        )

    # ── 2c: request_changes returns to requirements chat ──────────────────────

    @pytest.mark.asyncio
    async def test_request_changes_returns_to_requirements(self):
        from bot.handlers.project import request_changes

        update  = make_callback_update(CB_PLAN_CHANGES)
        context = make_context()

        state = await request_changes(update, context)

        assert state == GATHERING_REQUIREMENTS
        reply = update.callback_query.message.reply_text.call_args.args[0]
        assert "/plan" in reply.lower() or "change" in reply.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# TEST CASE 3  —  Run Project after completion
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunProject:
    """TC-3: ▶️ Run Project dispatches exec_command and shows output."""

    PROJECT = {
        "id":           "proj1",
        "name":         "SkyApp",
        "project_type": "Python App",
        "status":       "approved",
    }
    USER_ID = 42

    # ── 3a: successful run shows stdout and exit 0 ────────────────────────────

    @pytest.mark.asyncio
    async def test_run_project_dispatches_exec_command(self):
        update  = make_callback_update(CB_RUN_PROJECT, user_id=self.USER_ID)
        context = make_context(bot_data={
            KEY_DB: MagicMock(),
            f"run_project_{self.USER_ID}": self.PROJECT["id"],
        })

        mock_result = {"stdout": "Hello from SkyApp!\n", "stderr": "", "exit_code": 0}

        with (
            patch("bot.handlers.coding.get_project",
                  new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_agent_connected", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value=mock_result)) as mock_send,
        ):
            await run_project_handler(update, context)

        # exec_command dispatched with correct action
        mock_send.assert_awaited_once()
        action, params = mock_send.call_args.args[:2]
        assert action == "exec_command", f"Expected 'exec_command', got {action!r}"

        # Script file is the slugified name
        assert "skyapp.py" in params["command"], (
            f"Expected 'skyapp.py' in command; got {params['command']!r}"
        )

        # Working dir contains the project slug
        assert "skyapp" in params["working_dir"].lower(), (
            f"working_dir should contain 'skyapp'; got {params['working_dir']!r}"
        )

        # Output shown to user
        all_replies = " ".join(
            str(c.args[0] if c.args else c)
            for c in update.callback_query.message.reply_text.call_args_list
        )
        assert "Hello from SkyApp" in all_replies, (
            f"stdout must appear in chat; replies: {all_replies!r}"
        )
        assert "exit 0" in all_replies or "✅" in all_replies

    # ── 3b: failed run shows stderr and non-zero exit code ───────────────────

    @pytest.mark.asyncio
    async def test_run_project_shows_failure_output(self):
        update  = make_callback_update(CB_RUN_PROJECT, user_id=self.USER_ID)
        context = make_context(bot_data={
            KEY_DB: MagicMock(),
            f"run_project_{self.USER_ID}": self.PROJECT["id"],
        })

        mock_result = {
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'requests'",
            "exit_code": 1,
        }

        with (
            patch("bot.handlers.coding.get_project",
                  new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_agent_connected", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value=mock_result)),
        ):
            await run_project_handler(update, context)

        all_replies = " ".join(
            str(c.args[0] if c.args else c)
            for c in update.callback_query.message.reply_text.call_args_list
        )
        assert "ModuleNotFoundError" in all_replies
        assert "exit 1" in all_replies or "❌" in all_replies

    # ── 3c: worker disconnected shows graceful error ──────────────────────────

    @pytest.mark.asyncio
    async def test_run_project_worker_not_connected(self):
        update  = make_callback_update(CB_RUN_PROJECT, user_id=self.USER_ID)
        context = make_context(bot_data={
            KEY_DB: MagicMock(),
            f"run_project_{self.USER_ID}": self.PROJECT["id"],
        })

        with (
            patch("bot.handlers.coding.get_project",
                  new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_agent_connected", return_value=False),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock()) as mock_send,
        ):
            await run_project_handler(update, context)

        # send_action must NOT be called when worker is disconnected
        mock_send.assert_not_awaited()

        all_replies = " ".join(
            str(c.args[0] if c.args else c)
            for c in update.callback_query.message.reply_text.call_args_list
        )
        assert "not connected" in all_replies.lower() or "⚠️" in all_replies, (
            f"Expected worker-not-connected message; got: {all_replies!r}"
        )

    # ── 3d: run_project button appears after coding loop completion ───────────

    @pytest.mark.asyncio
    async def test_coding_loop_shows_run_project_button_on_complete(self):
        """
        After all milestones complete, _coding_loop must:
          • store run_project_{user_id} in bot_data
          • send the completion message with the run_project() keyboard
        """
        user_id  = 42
        chat_id  = 100
        project  = dict(self.PROJECT)
        milestones = ["Only milestone"]

        app          = MagicMock()
        app.bot_data = {}
        sent_markups: list = []

        async def _send(cid, text, **kwargs):
            if "reply_markup" in kwargs:
                sent_markups.append((text, kwargs["reply_markup"]))

        app.bot.send_message = AsyncMock(side_effect=_send)

        async def trigger_approve():
            """Auto-approve the only milestone."""
            event_key = _MS_EVENT_KEY.format(uid=user_id)
            dec_key   = _MS_DECISION_KEY.format(uid=user_id)
            for _ in range(200):
                if event_key in app.bot_data:
                    app.bot_data[dec_key] = "approve"
                    app.bot_data[event_key].set()
                    return
                await asyncio.sleep(0.01)

        trigger_task = asyncio.create_task(trigger_approve())

        with (
            patch("bot.handlers.coding._extract_milestones",
                  new=AsyncMock(return_value=milestones)),
            patch("bot.handlers.coding.is_agent_connected", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value={"stdout": "done", "exit_code": 0})),
            patch("bot.handlers.coding.create_task",
                  new=AsyncMock(return_value={"id": 99})),
            patch("bot.handlers.coding.update_task_status", new=AsyncMock()),
        ):
            await _coding_loop(app, chat_id, user_id, project, do_github=False)

        await trigger_task

        # project_id stored for run handler
        assert app.bot_data.get(f"run_project_{user_id}") == project["id"], (
            "run_project_{uid} must be set in bot_data after completion"
        )

        # Completion message contains 🎉 and 📁
        completion = [(t, m) for t, m in sent_markups if "🎉" in t]
        assert completion, "Must send a '🎉 complete' message"
        comp_text, comp_markup = completion[0]
        assert "📁" in comp_text, "Completion message must include folder path"

        # Keyboard must include Run Project button
        all_data = [btn.callback_data
                    for row in comp_markup.inline_keyboard for btn in row]
        assert CB_RUN_PROJECT in all_data, (
            f"Run Project button missing from completion keyboard; got: {all_data}"
        )
