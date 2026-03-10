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
        if action == "delete_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
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
    actions: list[str] = []

    async def _send_action(action, params, **kwargs):
        del kwargs
        actions.append(action)
        if action == "run_coding_agent":
            assert params["agent"] == "qwen"
            assert params["task_mode"] == "planner_chat"
            assert "qwen_context_text" in params
            assert params["reply_contract"] == "ask_next_question"
            assert "planner_state_json" in params
            assert "requirement_summary_md" in params
            assert "working_dir" not in params
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
    assert actions == ["run_coding_agent"]
    router.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_qwen_plan_request_uses_plan_generation():
    router = MagicMock()
    router.chat = AsyncMock()

    async def _send_action(action, params, **kwargs):
        del kwargs
        if action == "create_directory":
            raise AssertionError("Qwen request-scoped planner should not create a remote planner directory")
        if action == "run_coding_agent":
            assert params["agent"] == "qwen"
            assert params["task_mode"] == "plan_generation"
            assert params["reply_contract"] == "emit_plan"
            assert "planner_state_json" in params
            assert "requirement_summary_md" in params
            assert "Do not say requirements are missing." in params["qwen_context_text"]
            assert "working_dir" not in params
            return {
                "status": "success",
                "result": {"returncode": 0, "stdout": "**Demo - Project Plan**\n**Overview:** demo\n**Core Features:**\n- one\n**Tech Stack:** python\n**Project Structure:**\n- app/\n**Milestones:**\n1. ship\n**Open Questions:** None", "stderr": ""},
            }
        raise AssertionError(f"Unexpected action: {action}")

    with (
        patch("bot.handlers.project.cfg.PLANNER_PRIMARY_AGENT", "qwen"),
        patch("bot.handlers.project.is_worker_available", return_value=True),
        patch("bot.handlers.project.send_action", new=AsyncMock(side_effect=_send_action)),
    ):
        reply = await _planner_via_codex_then_router(
            router=router,
            messages=[
                {"role": "assistant", "content": "What does this app do?"},
                {"role": "user", "content": "It is a small Windows terminal script."},
                {"role": "user", "content": "Generate the full project plan now based on everything we discussed."},
            ],
            system="planner system",
            max_tokens=512,
            task_type="planning",
            user_id=42,
        )

    assert "Project Plan" in reply
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
        if action == "delete_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
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
        if action == "delete_directory":
            return {"status": "success", "result": {"returncode": 0, "stdout": "", "stderr": ""}}
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


@pytest.mark.asyncio
async def test_planner_live_policy_blocks_router_fallback(monkeypatch: pytest.MonkeyPatch):
    router = MagicMock()
    router.chat = AsyncMock(return_value=MagicMock(text="Router fallback reply"))

    monkeypatch.setenv("SKYNET_E2E_LIVE", "1")
    monkeypatch.setenv("SKYNET_LIVE_E2E_FLOW", "telegram_real")
    monkeypatch.setenv("SKYNET_LIVE_E2E_ALLOW_FALLBACK", "0")
    monkeypatch.setenv("SKYNET_LIVE_E2E_AGENT", "qwen")

    with (
        patch("bot.handlers.project.cfg.PLANNER_PRIMARY_AGENT", "codex"),
        patch("bot.handlers.project.cfg.PLANNER_ROUTER_FALLBACK_ENABLED", True),
        patch("bot.handlers.project.cfg.CONTROL_LOOP_ENABLED", True),
        patch("bot.handlers.project.cfg.CONTROL_LOOP_FORCE_FOR_ALL", True),
        patch("bot.handlers.project.cfg.CONTROL_LOOP_PLANNER_AGENT", "qwen"),
        patch("bot.handlers.project.cfg.CONTROL_LOOP_CRITIC_AGENT", "qwen"),
        patch("bot.handlers.project.cfg.PLANNER_WORKER_AGENTS", ("codex",)),
        patch("bot.handlers.project.send_action", new=AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="fallback is disabled"):
            await _planner_via_codex_then_router(
                router=router,
                messages=[{"role": "user", "content": "plan this"}],
                system="planner system",
                max_tokens=512,
                task_type="planning",
                user_id=42,
            )

    router.chat.assert_not_awaited()
