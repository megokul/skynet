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
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from telegram.ext import ConversationHandler

# ── Helpers from conftest ─────────────────────────────────────────────────────
from helpers import make_callback_update, make_message_update, make_context

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
        plan_text = "## Plan\n1. Setup\n2. Core logic"

        update  = make_callback_update(CB_PLAN_APPROVE, user_id=42)
        context = make_context(
            user_data={
                _NAME_KEY: "SkyApp",
                _TYPE_KEY: "Python App",
                _PLAN_KEY: plan_text,
            },
            bot_data={KEY_DB: MagicMock()},
        )

        mock_ensure  = AsyncMock(return_value=fake_user)
        mock_create  = AsyncMock(return_value=fake_project)

        with (
            patch("bot.handlers.project.ensure_user",    new=mock_ensure),
            patch("bot.handlers.project.create_project", new=mock_create),
        ):
            result = await approve_plan(update, context)

        # ── DB calls actually happened ─────────────────────────────────────────

        mock_ensure.assert_awaited_once()
        ensure_kwargs = mock_ensure.call_args.kwargs
        assert ensure_kwargs["telegram_user_id"] == 42, (
            f"ensure_user called with wrong telegram_user_id: {ensure_kwargs}"
        )

        mock_create.assert_awaited_once()
        create_kwargs = mock_create.call_args.kwargs
        assert create_kwargs["name"]         == "SkyApp",      f"Wrong name: {create_kwargs}"
        assert create_kwargs["project_type"] == "Python App",  f"Wrong type: {create_kwargs}"
        assert create_kwargs["description"]  == plan_text,     f"Wrong description: {create_kwargs}"
        assert create_kwargs["user_id"]      == fake_user["id"], (
            f"user_id must come from ensure_user return value; got: {create_kwargs}"
        )

        # ── Conversation ends immediately — no AWAITING_GITHUB redirect ────────

        assert result == ConversationHandler.END, (
            "approve_plan must return END, not redirect to a GitHub state"
        )

        # ── Reply contains project name and no GitHub question ─────────────────

        reply_text = update.callback_query.message.reply_text.call_args.args[0]
        assert "SkyApp" in reply_text
        assert "✅" in reply_text or "saved" in reply_text.lower()
        assert "github" not in reply_text.lower(), (
            "Project approval must NOT ask about GitHub"
        )

        # ── Start Coding button present ────────────────────────────────────────

        markup = update.callback_query.message.reply_text.call_args.kwargs.get("reply_markup")
        all_data = [btn.callback_data
                    for row in markup.inline_keyboard for btn in row]
        assert CB_START_CODING in all_data, (
            "'Start Coding' button must appear after plan approval"
        )

        # ── project_id forwarded to coding handler, temp keys cleared ──────────

        assert context.user_data.get("last_project_id") == "abc123"
        assert _NAME_KEY     not in context.user_data
        assert _TYPE_KEY     not in context.user_data
        assert _PLAN_KEY     not in context.user_data
        assert _REQS_HISTORY not in context.user_data

    @pytest.mark.asyncio
    async def test_approve_plan_project_written_to_real_db(self):
        """
        Integration check: approve_plan with a real in-memory SQLite DB.
        Verifies the project row actually exists after the handler runs.
        """
        from db.schema import init_db
        from db.store import get_project, list_projects

        db = await init_db(":memory:")
        plan_text = "## Plan\n1. Setup\n2. Build it"

        update  = make_callback_update(CB_PLAN_APPROVE, user_id=77)
        context = make_context(
            user_data={
                _NAME_KEY: "RealDBProject",
                _TYPE_KEY: "CLI",
                _PLAN_KEY: plan_text,
            },
            bot_data={KEY_DB: db},
        )

        await approve_plan(update, context)

        # project_id must have been stored in user_data by the handler
        project_id = context.user_data.get("last_project_id")
        assert project_id, "Handler must set last_project_id in user_data"

        # Row must exist in the DB
        row = await get_project(db, project_id)
        assert row is not None,                    "Project row missing from DB"
        assert row["name"]         == "RealDBProject", f"Wrong name: {row}"
        assert row["project_type"] == "CLI",           f"Wrong type: {row}"
        assert row["description"]  == plan_text,       f"Wrong description: {row}"
        assert row["status"]       == "approved",      f"Wrong status: {row}"

        # Must be findable via list_projects too
        all_projects = await list_projects(db, user_id=row["user_id"])
        ids = [p["id"] for p in all_projects]
        assert project_id in ids, "Project not returned by list_projects"

        await db.close()


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
            patch("bot.handlers.coding.is_worker_available", return_value=True),
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
            f"run_files_{self.USER_ID}_{self.PROJECT['id']}": ["skyapp.py"],
        })

        mock_result = {"stdout": "Hello from SkyApp!\n", "stderr": "", "exit_code": 0}

        with (
            patch("bot.handlers.coding.get_project",
                  new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value=mock_result)) as mock_send,
        ):
            await run_project_handler(update, context)

        # Only one call: exec_command (no list_directory needed — files stored)
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
    async def test_run_project_falls_back_to_list_directory(self):
        """No stored files (e.g. bot restarted) → falls back to list_directory."""
        update = make_callback_update(CB_RUN_PROJECT, user_id=self.USER_ID)
        context = make_context(bot_data={
            KEY_DB: MagicMock(),
            f"run_project_{self.USER_ID}": self.PROJECT["id"],
            # NOTE: no run_files_ key → triggers fallback
        })
        list_result = {
            "status": "success",
            "result": {
                "stdout": "skyapp.py  (120 bytes)\n",
                "stderr": "",
                "returncode": 0,
            },
        }
        mock_result = {"stdout": "Hello from fallback!\n", "stderr": "", "exit_code": 0}

        with (
            patch("bot.handlers.coding.get_project", new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch(
                "bot.handlers.coding.send_action",
                new=AsyncMock(side_effect=[list_result, mock_result]),
            ) as mock_send,
        ):
            await run_project_handler(update, context)

        # Two calls: list_directory fallback + exec_command
        assert mock_send.await_count == 2
        assert mock_send.call_args_list[0].args[0] == "list_directory"
        action, params = mock_send.call_args_list[1].args[:2]
        assert action == "exec_command"
        assert "skyapp.py" in params["command"]

    @pytest.mark.asyncio
    async def test_run_project_shows_failure_output(self):
        update  = make_callback_update(CB_RUN_PROJECT, user_id=self.USER_ID)
        context = make_context(bot_data={
            KEY_DB: MagicMock(),
            f"run_project_{self.USER_ID}": self.PROJECT["id"],
            f"run_files_{self.USER_ID}_{self.PROJECT['id']}": ["skyapp.py"],
        })

        mock_result = {
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'requests'",
            "exit_code": 1,
        }

        with (
            patch("bot.handlers.coding.get_project",
                  new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value=mock_result)),
        ):
            await run_project_handler(update, context)

        all_replies = " ".join(
            str(c.args[0] if c.args else c)
            for c in update.callback_query.message.reply_text.call_args_list
        )
        assert "ModuleNotFoundError" in all_replies
        assert "code 1" in all_replies or "exit 1" in all_replies or "❌" in all_replies

    @pytest.mark.asyncio
    async def test_run_project_handles_top_level_error_envelope(self):
        update = make_callback_update(CB_RUN_PROJECT, user_id=self.USER_ID)
        context = make_context(bot_data={
            KEY_DB: MagicMock(),
            f"run_project_{self.USER_ID}": self.PROJECT["id"],
            f"run_files_{self.USER_ID}_{self.PROJECT['id']}": ["skyapp.py"],
        })

        mock_result = {"status": "error", "error": "Worker rejected exec_command"}

        with (
            patch("bot.handlers.coding.get_project",
                  new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value=mock_result)),
        ):
            await run_project_handler(update, context)

        all_replies = " ".join(
            str(c.args[0] if c.args else c)
            for c in update.callback_query.message.reply_text.call_args_list
        )
        assert "Run failed" in all_replies
        assert "Worker rejected exec_command" in all_replies

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
            patch("bot.handlers.coding.is_worker_available", return_value=False),
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
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value={
                      "stdout": "Wrote 1 file(s): skyapp.py",
                      "exit_code": 0,
                      "files_written": ["skyapp.py"],
                  })),
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

        # Written files stored for run handler
        assert app.bot_data.get(f"run_files_{user_id}_{project['id']}") == ["skyapp.py"], (
            "run_files_{uid} must be set in bot_data after completion"
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


# ═══════════════════════════════════════════════════════════════════════════════
# TEST CASE 4  —  Worker error surfacing in coding loop
# ═══════════════════════════════════════════════════════════════════════════════

class TestCodingLoopErrorSurfacing:
    """
    TC-4: verify that errors returned by the real CLAW worker are surfaced
    to the user and recorded in the DB — not silently swallowed.

    The real gateway wraps worker responses:
      success → {"status": "success", "result": {"returncode": 0, "stdout": "...", ...}}
      error   → {"status": "error",   "error":  "<reason>"}

    Before this fix the loop always showed ✅ and marked tasks done regardless.
    """

    PROJECT = {
        "id":           "proj99",
        "name":         "ErrorApp",
        "project_type": "Python App",
        "status":       "approved",
    }
    USER_ID  = 77
    CHAT_ID  = 300

    # ── shared loop runner ────────────────────────────────────────────────────

    async def _run_loop_single_milestone(
        self,
        send_action_return,
        *,
        do_github=False,
    ):
        """Run the coding loop for one milestone and return (app, sent_messages)."""
        app          = MagicMock()
        app.bot_data = {}
        sent: list[str] = []

        async def _send(cid, text, **kw):
            sent.append(text)

        app.bot.send_message = AsyncMock(side_effect=_send)

        task_rec = {"id": 55}
        mock_create_task   = AsyncMock(return_value=task_rec)
        mock_update_status = AsyncMock()

        async def _approve():
            event_key = _MS_EVENT_KEY.format(uid=self.USER_ID)
            dec_key   = _MS_DECISION_KEY.format(uid=self.USER_ID)
            for _ in range(300):
                if event_key in app.bot_data:
                    app.bot_data[dec_key] = "approve"
                    app.bot_data[event_key].set()
                    return
                await asyncio.sleep(0.01)

        approve_task = asyncio.create_task(_approve())

        with (
            patch("bot.handlers.coding._extract_milestones",
                  new=AsyncMock(return_value=["Do the thing"])),
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value=send_action_return)),
            patch("bot.handlers.coding.create_task",      new=mock_create_task),
            patch("bot.handlers.coding.update_task_status", new=mock_update_status),
        ):
            await _coding_loop(
                app, self.CHAT_ID, self.USER_ID, self.PROJECT, do_github=do_github,
            )

        await approve_task
        return app, sent, mock_create_task, mock_update_status

    # ── 4a: worker error envelope → task marked failed ───────────────────────

    @pytest.mark.asyncio
    async def test_milestone_marked_failed_when_worker_returns_error(self):
        """
        When send_action returns {"status": "error", "error": "cline not found"},
        the task must be marked 'failed' in the DB and the user sees ❌.
        """
        error_envelope = {"status": "error", "error": "cline not found on PATH"}

        app, sent, mock_create, mock_update = await self._run_loop_single_milestone(
            error_envelope
        )

        # DB: task created then marked failed (not done)
        # status is always passed as a kwarg: update_task_status(db, id, status=...)
        mock_create.assert_awaited_once()
        statuses = [c.kwargs.get("status") for c in mock_update.call_args_list]
        assert "failed" in statuses, (
            f"Task must be marked 'failed'; update kwargs: {statuses}"
        )
        assert "done" not in statuses, (
            f"Task must NOT be marked 'done' on error; update kwargs: {statuses}"
        )

        # User sees an error message, not a success message
        all_text = " ".join(sent)
        assert "❌" in all_text or "failed" in all_text.lower(), (
            f"User must see ❌/failed message; sent: {sent}"
        )
        assert "✅ Milestone" not in all_text, (
            f"Must not show ✅ Milestone complete on error; sent: {sent}"
        )

    @pytest.mark.asyncio
    async def test_milestone_marked_failed_when_inner_returncode_non_zero(self):
        """
        The gateway can return {"status":"ok","result":{"returncode":1,...}}.
        This must be treated as a failed milestone, not a success.
        """
        nested_failure = {
            "status": "ok",
            "result": {
                "returncode": 1,
                "stdout": "",
                "stderr": "OLLAMA_ERROR: HTTP Error 404: Not Found",
            },
        }

        app, sent, _, mock_update = await self._run_loop_single_milestone(nested_failure)

        statuses = [c.kwargs.get("status") for c in mock_update.call_args_list]
        assert "failed" in statuses, (
            f"Task must be marked failed when inner returncode != 0; update kwargs: {statuses}"
        )
        assert "done" not in statuses, (
            f"Task must not be marked done when inner returncode != 0; update kwargs: {statuses}"
        )

        all_text = " ".join(sent)
        assert "OLLAMA_ERROR" in all_text, (
            f"Nested failure reason must be surfaced to user; sent: {sent}"
        )
        assert "Milestone 1 complete" not in all_text, (
            f"Must not show milestone complete when inner returncode != 0; sent: {sent}"
        )
        assert f"run_project_{self.USER_ID}" not in app.bot_data, (
            "Run-project shortcut must not be set when there are no successful milestones."
        )

    # ── 4b: real success envelope → summary from result["result"]["stdout"] ──

    @pytest.mark.asyncio
    async def test_milestone_summary_read_from_real_worker_envelope(self):
        """
        When send_action returns the real worker format
          {"status": "success", "result": {"returncode": 0, "stdout": "agent output", ...}}
        the milestone completion message must contain the stdout summary.
        """
        real_envelope = {
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout":     "Created vodoo.py — 5 lines, 0 errors.",
                "stderr":     "",
            },
        }

        app, sent, mock_create, mock_update = await self._run_loop_single_milestone(
            real_envelope
        )

        # Task must be marked done
        statuses = [c.kwargs.get("status") for c in mock_update.call_args_list]
        assert "done" in statuses, (
            f"Task must be marked 'done' on success; update kwargs: {statuses}"
        )

        # stdout from the INNER result dict must appear in the chat message
        all_text = " ".join(sent)
        assert "Created vodoo.py" in all_text, (
            f"stdout from result['result']['stdout'] must appear in chat; sent: {sent}"
        )

    # ── 4c: GitHub error → warning shown, not fake ✅ ─────────────────────────

    @pytest.mark.asyncio
    async def test_github_setup_shows_warning_on_worker_error(self):
        """
        When gh_create_repo returns {"status": "error", "error": "gh: not authenticated"},
        the bot must show ⚠️ GitHub setup failed — NOT ✅ GitHub repo created.
        """
        error_envelope = {
            "status": "error",
            "error":  "gh: not authenticated. Run 'gh auth login'.",
        }

        # For this test, the gh_create_repo call returns error; milestone
        # send_action after that returns success so the loop completes.
        success_envelope = {
            "status": "success",
            "result": {"returncode": 0, "stdout": "done", "stderr": ""},
        }
        call_count = 0

        async def _side_effect(action, params, **kw):
            nonlocal call_count
            call_count += 1
            if action == "gh_create_repo":
                return error_envelope
            return success_envelope

        app          = MagicMock()
        app.bot_data = {}
        sent: list[str] = []

        async def _send(cid, text, **kw):
            sent.append(text)

        app.bot.send_message = AsyncMock(side_effect=_send)

        async def _approve():
            event_key = _MS_EVENT_KEY.format(uid=self.USER_ID)
            dec_key   = _MS_DECISION_KEY.format(uid=self.USER_ID)
            for _ in range(300):
                if event_key in app.bot_data:
                    app.bot_data[dec_key] = "approve"
                    app.bot_data[event_key].set()
                    return
                await asyncio.sleep(0.01)

        approve_task = asyncio.create_task(_approve())

        with (
            patch("bot.handlers.coding._extract_milestones",
                  new=AsyncMock(return_value=["Do the thing"])),
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(side_effect=_side_effect)),
            patch("bot.handlers.coding.create_task",
                  new=AsyncMock(return_value={"id": 1})),
            patch("bot.handlers.coding.update_task_status", new=AsyncMock()),
        ):
            await _coding_loop(
                app, self.CHAT_ID, self.USER_ID, self.PROJECT, do_github=True,
            )

        await approve_task

        all_text = " ".join(sent)
        assert "⚠️" in all_text or "failed" in all_text.lower(), (
            f"Must show GitHub setup warning; sent: {sent}"
        )
        assert "✅ GitHub repo created" not in all_text, (
            f"Must NOT show fake ✅ on GitHub error; sent: {sent}"
        )
        assert "gh: not authenticated" in all_text, (
            f"Error reason must appear in the warning; sent: {sent}"
        )

    # ── 4d: run_project handler reads real worker envelope ────────────────────

    @pytest.mark.asyncio
    async def test_run_project_reads_real_worker_envelope(self):
        """
        run_project_handler must unwrap result["result"]["stdout"] (real worker format)
        and display it, not look at the top-level result["stdout"].
        """
        real_envelope = {
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout":     "Hello from errorapp!\n",
                "stderr":     "",
            },
        }
        # Deliberately put WRONG data at top level to prove we read the inner dict.
        real_envelope["stdout"] = "SHOULD_NOT_APPEAR"
        real_envelope["exit_code"] = 99

        update  = make_callback_update(CB_RUN_PROJECT, user_id=self.USER_ID)
        context = make_context(bot_data={
            KEY_DB: MagicMock(),
            f"run_project_{self.USER_ID}": self.PROJECT["id"],
            f"run_files_{self.USER_ID}_{self.PROJECT['id']}": ["skyapp.py"],
        })

        with (
            patch("bot.handlers.coding.get_project",
                  new=AsyncMock(return_value=self.PROJECT)),
            patch("bot.handlers.coding.is_worker_available", return_value=True),
            patch("bot.handlers.coding.send_action",
                  new=AsyncMock(return_value=real_envelope)),
        ):
            await run_project_handler(update, context)

        all_replies = " ".join(
            str(c.args[0] if c.args else c)
            for c in update.callback_query.message.reply_text.call_args_list
        )
        # Inner stdout must appear
        assert "Hello from errorapp" in all_replies, (
            f"Inner stdout must appear in reply; got: {all_replies!r}"
        )
        # Top-level wrong value must NOT appear
        assert "SHOULD_NOT_APPEAR" not in all_replies, (
            f"Top-level stdout must be ignored; got: {all_replies!r}"
        )
        # exit code from inner result["result"]["returncode"] = 0 → ✅
        assert "✅" in all_replies or "exit 0" in all_replies, (
            f"exit 0 from inner returncode must show ✅; got: {all_replies!r}"
        )
