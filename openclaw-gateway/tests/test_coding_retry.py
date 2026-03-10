"""Retry-coding behavior tests."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helpers import make_callback_update, make_context

from bot.handlers.coding import (
    _CODING_PID_KEY,
    _GITHUB_PREF_KEY,
    _MS_DECISION_KEY,
    _MS_EVENT_KEY,
    _PROJECT_ID_KEY,
    _build_coding_stage_chain,
    _effective_coding_profile,
    _parse_coding_fallback_chain,
    _preflight_coding_environment,
    _coding_loop,
    _stop_request_key,
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


@pytest.fixture(autouse=True)
def _legacy_defaults(monkeypatch):
    monkeypatch.setattr("bot.handlers.coding.cfg.CONTROL_LOOP_ENABLED", False)
    monkeypatch.setattr("bot.handlers.coding.cfg.CONTROL_LOOP_FORCE_FOR_ALL", False)
    monkeypatch.setattr(
        "bot.handlers.coding.cfg.CODING_FALLBACK_CHAIN",
        "codex,claude_ollama,cline",
    )


def _callbacks(markup) -> list[list[str]]:
    return [[btn.callback_data for btn in row] for row in markup.inline_keyboard]


def test_parse_coding_fallback_chain_filters_invalid_values():
    chain = _parse_coding_fallback_chain(" codex ,invalid,claude_ollama, codex ,cline ")
    assert chain == ["codex", "claude_ollama", "cline"]

    default_chain = _parse_coding_fallback_chain(",,,bad,,")
    assert default_chain == ["qwen", "codex"]


def test_build_coding_stage_chain_filters_policy_disabled_claude():
    project = {"coding_profile": "codex_primary"}
    with (
        patch("bot.handlers.coding.cfg.CODING_FORCE_PRIMARY_FOR_ALL", True),
        patch("bot.handlers.coding.cfg.CLAUDE_OLLAMA_STAGE_ENABLED", False),
    ):
        assert _build_coding_stage_chain(project) == ["codex", "cline"]


def test_effective_coding_profile_force_all_override():
    project = {"coding_profile": "legacy"}
    with patch("bot.handlers.coding.cfg.CODING_FORCE_PRIMARY_FOR_ALL", True):
        assert _effective_coding_profile(project) == "codex_primary"
    with patch("bot.handlers.coding.cfg.CODING_FORCE_PRIMARY_FOR_ALL", False):
        assert _effective_coding_profile(project) == "legacy"
        assert _build_coding_stage_chain(project) == []


def test_build_coding_stage_chain_uses_live_policy_when_active(monkeypatch: pytest.MonkeyPatch):
    project = {"coding_profile": "codex_primary"}
    monkeypatch.setenv("SKYNET_E2E_LIVE", "1")
    monkeypatch.setenv("SKYNET_LIVE_E2E_FLOW", "telegram_real")
    monkeypatch.setenv("SKYNET_LIVE_E2E_AGENT", "qwen")
    monkeypatch.setenv("SKYNET_LIVE_E2E_ALLOW_FALLBACK", "0")
    monkeypatch.setenv("SKYNET_CODING_FALLBACK_CHAIN", "qwen,codex")

    assert _build_coding_stage_chain(project) == ["qwen"]


@pytest.mark.asyncio
async def test_preflight_all_chain_agents_unavailable_fails():
    project = {"id": "proj_pf_all_down", "coding_profile": "codex_primary"}

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        assert action == "check_coding_agents"
        return {
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout": (
                    "codex: unavailable (expected binary: codex)\n"
                    "claude: unavailable (expected binary: claude)\n"
                    "cline: unavailable (expected binary: cline)"
                ),
                "stderr": "",
            },
        }

    with (
        patch("bot.handlers.coding.cfg.CODING_FORCE_PRIMARY_FOR_ALL", True),
        patch("bot.handlers.coding.cfg.CODING_FALLBACK_CHAIN", "codex,cline"),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)),
    ):
        ok, message, stage_chain = await _preflight_coding_environment(project=project)

    assert ok is False
    assert "No coding agents available for chain" in message
    assert stage_chain == ["codex", "cline"]


@pytest.mark.asyncio
async def test_preflight_primary_unavailable_uses_fallback():
    project = {"id": "proj_pf_fallback", "coding_profile": "codex_primary"}

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        assert action == "check_coding_agents"
        return {
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout": (
                    "codex: unavailable (expected binary: codex)\n"
                    "claude: available (claude)\n"
                    "cline: unavailable (expected binary: cline)"
                ),
                "stderr": "",
            },
        }

    with (
        patch("bot.handlers.coding.cfg.CODING_FORCE_PRIMARY_FOR_ALL", True),
        patch("bot.handlers.coding.cfg.CLAUDE_OLLAMA_STAGE_ENABLED", True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)),
    ):
        ok, message, stage_chain = await _preflight_coding_environment(project=project)

    assert ok is True
    assert "fallback" in message.lower()
    assert stage_chain == ["claude_ollama"]


@pytest.mark.asyncio
async def test_preflight_ssh_mode_skips_local_acp_stage_probe():
    project = {"id": "proj_pf_ssh_mode", "coding_profile": "codex_primary"}
    runner = MagicMock()
    runner.available_stages = MagicMock(return_value=([], {"codex": "missing binary: codex"}))

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        assert action == "check_coding_agents"
        return {
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout": (
                    "codex: available (codex)\n"
                    "claude: unavailable (expected binary: claude)\n"
                    "cline: unavailable (expected binary: cline)"
                ),
                "stderr": "",
            },
        }

    with (
        patch.dict(os.environ, {"OPENCLAW_EXECUTION_MODE": "ssh_tunnel"}, clear=False),
        patch("bot.handlers.coding.cfg.ORCHESTRATION_MODE", "acp_first"),
        patch("bot.handlers.coding.cfg.ORCHESTRATION_ALLOW_ACP_WITH_SSH", False),
        patch("bot.handlers.coding.get_openclaw_runner", return_value=runner),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)) as mock_send,
    ):
        ok, message, stage_chain = await _preflight_coding_environment(project=project)

    assert ok is True
    assert stage_chain == ["codex"]
    assert "fallback" in message.lower() or "filtered" in message.lower()
    runner.available_stages.assert_not_called()
    mock_send.assert_awaited_once()


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
async def test_coding_preflight_blocks_when_claude_cli_missing():
    project = {
        "id": "proj_preflight",
        "name": "Preflight Project",
        "project_type": "Python App",
        "status": "approved",
        "coding_profile": "claude_ollama",
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

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        if action == "create_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
        if action == "check_coding_agents":
            return {
                "status": "success",
                "result": {
                    "returncode": 0,
                    "stdout": (
                        "codex: available (codex)\n"
                        "claude: unavailable (expected binary: claude)\n"
                        "cline: unavailable (expected binary: cline)"
                    ),
                    "stderr": "",
                },
            }
        raise AssertionError(f"Unexpected action called during preflight test: {action}")

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=["Should not run"])) as mock_extract,
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)),
        patch("bot.handlers.coding.cfg.CODING_FORCE_PRIMARY_FOR_ALL", False),
        patch("bot.handlers.coding.cfg.CLAUDE_OLLAMA_STAGE_ENABLED", True),
        patch("bot.handlers.coding.create_task", new=AsyncMock()) as mock_create_task,
        patch("bot.handlers.coding.update_task_status", new=AsyncMock()) as mock_update_status,
    ):
        await _coding_loop(app, chat_id, user_id, project, do_github=False)

    mock_extract.assert_not_awaited()
    mock_create_task.assert_not_awaited()
    mock_update_status.assert_not_awaited()

    all_text = " ".join(sent_text)
    assert "preflight failed" in all_text.lower(), f"Expected preflight failure message, got: {sent_text}"
    assert "claude: unavailable" in all_text.lower(), f"Expected Claude CLI detail, got: {sent_text}"

    assert sent_markups, "Expected retry keyboard on preflight failure."
    rows = _callbacks(sent_markups[-1])
    assert rows[0] == [f"{CB_CODING_RETRY_PREFIX}{project['id']}"]
    assert rows[1] == [CB_MY_PROJECTS, CB_MAIN_MENU]


@pytest.mark.asyncio
async def test_milestone_event_registered_before_message_render():
    project = {
        "id": "proj_event_order",
        "name": "Event Order",
        "project_type": "Python App",
        "status": "approved",
        "coding_profile": "legacy",
    }
    user_id = 42
    chat_id = 100
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    decision_key = _MS_DECISION_KEY.format(uid=user_id)

    app = MagicMock()
    app.bot_data = {}
    observed_registration = False

    async def _send(_cid, text, **_kwargs):
        nonlocal observed_registration
        if "Milestone 1/1" in str(text):
            observed_registration = True
            assert event_key in app.bot_data, "Milestone event must exist before message is sent."
            app.bot_data[decision_key] = "approve"
            app.bot_data[event_key].set()

    app.bot.send_message = AsyncMock(side_effect=_send)

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        if action == "run_coding_agent":
            return {
                "status": "success",
                "result": {
                    "returncode": 0,
                    "stdout": "Wrote 1 file(s): event_order.py",
                    "stderr": "",
                    "files_written": ["event_order.py"],
                },
            }
        return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=["Do one thing"])),
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)),
        patch("bot.handlers.coding.create_task", new=AsyncMock(return_value={"id": 101})),
        patch("bot.handlers.coding.update_task_status", new=AsyncMock()),
    ):
        await _coding_loop(app, chat_id, user_id, project, do_github=False)

    assert observed_registration, "Milestone message was not observed in test."


@pytest.mark.asyncio
async def test_coding_loop_emits_progress_heartbeat_for_slow_agent():
    project = {
        "id": "proj_heartbeat",
        "name": "Heartbeat Project",
        "project_type": "Python App",
        "status": "approved",
        "coding_profile": "legacy",
    }
    user_id = 42
    chat_id = 100

    app = MagicMock()
    app.bot_data = {}
    sent_text: list[str] = []

    async def _send(_cid, text, **_kwargs):
        sent_text.append(str(text))

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

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        if action == "run_coding_agent":
            await asyncio.sleep(1.2)
            return {
                "status": "success",
                "result": {
                    "returncode": 0,
                    "stdout": "Wrote 1 file(s): heartbeat.py",
                    "stderr": "",
                    "files_written": ["heartbeat.py"],
                },
            }
        return {
            "status": "success",
            "result": {"returncode": 0, "stdout": "", "stderr": ""},
        }

    approve_task = asyncio.create_task(_approve_once())

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=["Do work"])),
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)),
        patch("bot.handlers.coding.create_task", new=AsyncMock(return_value={"id": 501})),
        patch("bot.handlers.coding.update_task_status", new=AsyncMock()),
        patch("bot.handlers.coding.cfg.CODING_PROGRESS_HEARTBEAT_SECONDS", 1),
    ):
        await _coding_loop(app, chat_id, user_id, project, do_github=False)

    await approve_task

    all_text = "\n".join(sent_text)
    assert "Still working on coding generation" in all_text, (
        f"Expected progress heartbeat while run_coding_agent is slow; messages: {sent_text}"
    )


@pytest.mark.asyncio
async def test_coding_loop_timeout_aborts_and_shows_retry():
    project = {
        "id": "proj_timeout",
        "name": "Timeout Project",
        "project_type": "Python App",
        "status": "approved",
        "coding_profile": "legacy",
    }
    user_id = 42
    chat_id = 100

    app = MagicMock()
    app.bot_data = {}
    sent_text: list[str] = []
    sent_markups = []

    async def _send(_cid, text, **kwargs):
        sent_text.append(str(text))
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

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        if action == "run_coding_agent":
            await asyncio.sleep(3.0)
            return {
                "status": "success",
                "result": {
                    "returncode": 0,
                    "stdout": "Wrote 1 file(s): timeout.py",
                    "stderr": "",
                    "files_written": ["timeout.py"],
                },
            }
        return {
            "status": "success",
            "result": {"returncode": 0, "stdout": "", "stderr": ""},
        }

    approve_task = asyncio.create_task(_approve_once())

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=["Do work"])),
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)),
        patch("bot.handlers.coding.create_task", new=AsyncMock(return_value={"id": 601})),
        patch("bot.handlers.coding.update_task_status", new=AsyncMock()),
        patch("bot.handlers.coding.cfg.CODING_PROGRESS_HEARTBEAT_SECONDS", 1),
        patch("bot.handlers.coding.cfg.CODING_AGENT_MAX_WAIT_SECONDS", 2),
    ):
        await _coding_loop(app, chat_id, user_id, project, do_github=False)

    await approve_task

    all_text = "\n".join(sent_text)
    assert "timed out while waiting for the coding agent" in all_text.lower()
    assert sent_markups, "Expected retry keyboard on timeout."
    rows = _callbacks(sent_markups[-1])
    assert rows[0] == [f"{CB_CODING_RETRY_PREFIX}{project['id']}"]


@pytest.mark.asyncio
async def test_coding_loop_user_stop_interrupts_slow_agent():
    project = {
        "id": "proj_user_stop",
        "name": "Stop Project",
        "project_type": "Python App",
        "status": "approved",
        "coding_profile": "legacy",
    }
    user_id = 42
    chat_id = 100

    app = MagicMock()
    app.bot_data = {}
    sent_text: list[str] = []

    async def _send(_cid, text, **_kwargs):
        sent_text.append(str(text))

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

    async def _send_action_side_effect(action, params, **kwargs):
        del params, kwargs
        if action == "run_coding_agent":
            app.bot_data[_stop_request_key(user_id)] = True
            await asyncio.sleep(2.5)
            return {
                "status": "success",
                "result": {
                    "returncode": 0,
                    "stdout": "Wrote 1 file(s): stop.py",
                    "stderr": "",
                    "files_written": ["stop.py"],
                },
            }
        return {
            "status": "success",
            "result": {"returncode": 0, "stdout": "", "stderr": ""},
        }

    approve_task = asyncio.create_task(_approve_once())

    with (
        patch("bot.handlers.coding._extract_milestones", new=AsyncMock(return_value=["Do work"])),
        patch("bot.handlers.coding.is_worker_available", return_value=True),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action_side_effect)),
        patch("bot.handlers.coding.create_task", new=AsyncMock(return_value={"id": 602})),
        patch("bot.handlers.coding.update_task_status", new=AsyncMock()),
        patch("bot.handlers.coding.cfg.CODING_PROGRESS_HEARTBEAT_SECONDS", 1),
        patch("bot.handlers.coding.cfg.CODING_AGENT_MAX_WAIT_SECONDS", 10),
    ):
        await _coding_loop(app, chat_id, user_id, project, do_github=False)

    await approve_task

    all_text = "\n".join(sent_text)
    assert "session stopped while executing milestone" in all_text.lower()
    assert _stop_request_key(user_id) not in app.bot_data


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
