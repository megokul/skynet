from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.handlers.coding import _extract_milestones_codex_then_router
from bot.handlers.project import (
    _build_deterministic_plan,
    _is_requirement_grounded_plan,
)


def _history() -> list[dict]:
    return [
        {"role": "user", "content": "python script"},
        {"role": "user", "content": "windows"},
        {"role": "user", "content": "terminal execution"},
        {"role": "user", "content": "popup-hi"},
        {"role": "user", "content": "short beep sound"},
    ]


def test_deterministic_plan_fallback_is_requirement_grounded():
    plan = _build_deterministic_plan(
        name="starboy",
        project_type_label="Python App",
        template={"stack": "Python 3.10+ (standard library)"},
        history=_history(),
    )
    lowered = plan.lower()
    assert "overview" in lowered
    assert "core features" in lowered
    assert "tech stack" in lowered
    assert "project structure" in lowered
    assert "milestones" in lowered
    assert _is_requirement_grounded_plan(plan, _history()) is True


def test_meta_planner_boilerplate_is_rejected():
    bad_plan = (
        "I'll act as your planner assistant for the Telegram product workflow.\n"
        "Send what you want to plan and I will structure it."
    )
    assert _is_requirement_grounded_plan(bad_plan, _history()) is False


def test_prompt_echo_plan_is_rejected():
    bad_plan = (
        "**demo — Project Plan**\n"
        "**Overview:** You MUST generate the full project plan now.\n"
        "**Core Features:**\n- item\n"
        "**Tech Stack:**\n- Python\n"
        "**Project Structure:**\n- main.py\n"
        "**Milestones:**\n1. step one\n2. step two"
    )
    assert _is_requirement_grounded_plan(bad_plan, _history()) is False


@pytest.mark.asyncio
async def test_extract_milestones_invalid_codex_json_uses_local_fallback():
    project = {
        "id": "proj-1",
        "name": "starboy",
        "description": (
            "A Windows Python script with popup hi and short beep.\n"
            "Original user requirements:\n"
            "- python script\n"
            "- windows\n"
            "- terminal execution\n"
            "- popup-hi\n"
            "- short beep sound"
        ),
    }

    async def _send_action(action, params, **kwargs):
        del params, kwargs
        assert action == "run_coding_agent"
        return {
            "status": "success",
            "result": {
                "returncode": 0,
                "stdout": "I will help you plan this workflow.",
                "stderr": "",
            },
        }

    with (
        patch("bot.handlers.coding.cfg.PLANNER_PRIMARY_AGENT", "codex"),
        patch("bot.handlers.coding.cfg.CONTROL_LOOP_ROUTER_FALLBACK_ENABLED", False),
        patch("bot.handlers.coding._use_acp_orchestration", return_value=False),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action)),
    ):
        milestones = await _extract_milestones_codex_then_router(
            router=MagicMock(),
            project=project,
            working_dir="E:/SKYNET-SANDBOX/Projects/starboy",
        )

    assert len(milestones) >= 2
    assert all(isinstance(item, str) and item.strip() for item in milestones)
    assert project.get("_loop_node_specs") == []


@pytest.mark.asyncio
async def test_extract_milestones_qwen_uses_primary_agent_without_router():
    project = {
        "id": "proj-qwen",
        "name": "qwen-plan",
        "description": "Build a Windows Python script with popup hi and beep using stdlib only.",
    }
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
                "result": {
                    "returncode": 0,
                    "stdout": '["Implement main script", "Add tests", "Add skynet_run.json"]',
                    "stderr": "",
                },
            }
        raise AssertionError(f"Unexpected action: {action}")

    with (
        patch("bot.handlers.coding.cfg.PLANNER_PRIMARY_AGENT", "qwen"),
        patch("bot.handlers.coding._use_acp_orchestration", return_value=False),
        patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=_send_action)),
    ):
        milestones = await _extract_milestones_codex_then_router(
            router=router,
            project=project,
            working_dir="E:/SKYNET-SANDBOX/Projects/qwen-plan",
        )

    assert milestones == [
        "Implement main script",
        "Add tests",
        "Add skynet_run.json",
    ]
    router.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_milestones_prefers_numbered_plan_without_codex_call():
    project = {
        "id": "proj-3",
        "name": "numbered-plan",
        "description": (
            "**Milestones:**\n"
            "1. Build main script\n"
            "2. Add tests\n"
            "3. Add skynet_run.json"
        ),
    }
    send_action = AsyncMock()
    with (
        patch("bot.handlers.coding.cfg.PLANNER_PRIMARY_AGENT", "codex"),
        patch("bot.handlers.coding.send_action", new=send_action),
    ):
        milestones = await _extract_milestones_codex_then_router(
            router=MagicMock(),
            project=project,
            working_dir="E:/SKYNET-SANDBOX/Projects/numbered-plan",
        )
    assert milestones == ["Build main script", "Add tests", "Add skynet_run.json"]
    send_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_milestones_non_codex_policy_uses_local_fallback_without_router():
    project = {
        "id": "proj-2",
        "name": "fallback-demo",
        "description": "1. Implement core script\n2. Add tests\n3. Add skynet_run.json",
    }
    router = MagicMock()
    router.chat = AsyncMock()

    with (
        patch("bot.handlers.coding.cfg.PLANNER_PRIMARY_AGENT", "router"),
        patch("bot.handlers.coding.cfg.CONTROL_LOOP_ROUTER_FALLBACK_ENABLED", False),
    ):
        milestones = await _extract_milestones_codex_then_router(
            router=router,
            project=project,
            working_dir="E:/SKYNET-SANDBOX/Projects/fallback-demo",
        )

    assert milestones == [
        "Implement core script",
        "Add tests",
        "Add skynet_run.json",
    ]
    router.chat.assert_not_awaited()
