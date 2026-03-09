from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.handlers.project import _planner_via_codex_then_router


@pytest.mark.asyncio
async def test_planner_codex_success_skips_router_fallback():
    router = MagicMock()
    router.chat = AsyncMock()

    async def _send_action(action, params, **kwargs):
        del params, kwargs
        if action == "create_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
        if action == "run_coding_agent":
            return {
                "status": "success",
                "result": {"returncode": 0, "stdout": "Codex planner reply", "stderr": ""},
            }
        raise AssertionError(f"Unexpected action: {action}")

    with (
        patch("bot.handlers.project.cfg.PLANNER_PRIMARY_AGENT", "codex"),
        patch("bot.handlers.project.cfg.CONTROL_LOOP_ROUTER_FALLBACK_ENABLED", True),
        patch("bot.handlers.project.is_worker_available", return_value=True),
        patch("bot.handlers.project.send_action", new=AsyncMock(side_effect=_send_action)),
    ):
        reply = await _planner_via_codex_then_router(
            router=router,
            messages=[{"role": "user", "content": "plan this"}],
            system="planner system",
            max_tokens=512,
            task_type="planning",
            user_id=42,
        )

    assert reply == "Codex planner reply"
    router.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_qwen_success_skips_router_fallback():
    router = MagicMock()
    router.chat = AsyncMock()

    async def _send_action(action, params, **kwargs):
        del kwargs
        if action == "create_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
        if action == "run_coding_agent":
            assert params["agent"] == "qwen"
            return {
                "status": "success",
                "result": {"returncode": 0, "stdout": "Qwen planner reply", "stderr": ""},
            }
        raise AssertionError(f"Unexpected action: {action}")

    with (
        patch("bot.handlers.project.cfg.PLANNER_PRIMARY_AGENT", "qwen"),
        patch("bot.handlers.project.is_worker_available", return_value=True),
        patch("bot.handlers.project.send_action", new=AsyncMock(side_effect=_send_action)),
    ):
        reply = await _planner_via_codex_then_router(
            router=router,
            messages=[{"role": "user", "content": "plan this"}],
            system="planner system",
            max_tokens=512,
            task_type="planning",
            user_id=42,
        )

    assert reply == "Qwen planner reply"
    router.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_worker_agent_list_is_configurable():
    router = MagicMock()
    router.chat = AsyncMock(return_value=MagicMock(text="Router fallback reply"))

    with (
        patch("bot.handlers.project.cfg.PLANNER_PRIMARY_AGENT", "qwen"),
        patch("bot.handlers.project.cfg.PLANNER_WORKER_AGENTS", ("codex",)),
        patch("bot.handlers.project.cfg.PLANNER_ROUTER_FALLBACK_ENABLED", True),
        patch("bot.handlers.project.send_action", new=AsyncMock()),
    ):
        reply = await _planner_via_codex_then_router(
            router=router,
            messages=[{"role": "user", "content": "plan this"}],
            system="planner system",
            max_tokens=512,
            task_type="planning",
            user_id=42,
        )

    assert reply == "Router fallback reply"
    router.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_planner_codex_failure_falls_back_to_router():
    router = MagicMock()
    router.chat = AsyncMock(return_value=MagicMock(text="Router fallback reply"))

    async def _send_action(action, params, **kwargs):
        del params, kwargs
        if action == "create_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
        if action == "run_coding_agent":
            return {"status": "error", "error": "codex unavailable"}
        raise AssertionError(f"Unexpected action: {action}")

    with (
        patch("bot.handlers.project.cfg.PLANNER_PRIMARY_AGENT", "codex"),
        patch("bot.handlers.project.cfg.CONTROL_LOOP_ROUTER_FALLBACK_ENABLED", True),
        patch("bot.handlers.project.is_worker_available", return_value=True),
        patch("bot.handlers.project.send_action", new=AsyncMock(side_effect=_send_action)),
    ):
        reply = await _planner_via_codex_then_router(
            router=router,
            messages=[{"role": "user", "content": "plan this"}],
            system="planner system",
            max_tokens=512,
            task_type="planning",
            user_id=42,
        )

    assert reply == "Router fallback reply"
    router.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_planner_ssh_mode_forces_send_action_path():
    router = MagicMock()
    router.chat = AsyncMock()
    runner = MagicMock()

    async def _send_action(action, params, **kwargs):
        del params, kwargs
        if action == "create_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
        if action == "run_coding_agent":
            return {
                "status": "success",
                "result": {"returncode": 0, "stdout": "Codex planner over SSH", "stderr": ""},
            }
        raise AssertionError(f"Unexpected action: {action}")

    with (
        patch.dict(os.environ, {"OPENCLAW_EXECUTION_MODE": "ssh_tunnel"}, clear=False),
        patch("bot.handlers.project.cfg.PLANNER_PRIMARY_AGENT", "codex"),
        patch("bot.handlers.project.cfg.ORCHESTRATION_MODE", "acp_first"),
        patch("bot.handlers.project.cfg.ORCHESTRATION_ALLOW_ACP_WITH_SSH", False),
        patch("bot.handlers.project.is_worker_available", return_value=True),
        patch("bot.handlers.project.get_openclaw_runner", return_value=runner),
        patch("bot.handlers.project.send_action", new=AsyncMock(side_effect=_send_action)),
    ):
        reply = await _planner_via_codex_then_router(
            router=router,
            messages=[{"role": "user", "content": "plan this"}],
            system="planner system",
            max_tokens=512,
            task_type="planning",
            user_id=42,
        )

    assert reply == "Codex planner over SSH"
    runner.start_session.assert_not_called()
    runner.run_prompt.assert_not_called()
