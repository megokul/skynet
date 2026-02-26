from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def _ensure_paths() -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class _FakeUpdate:
    def __init__(self, user_id: int):
        self.effective_user = _FakeUser(user_id)
        self.effective_chat = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(message_id=1)


class _NoopProviderRouter:
    async def chat(self, *args, **kwargs):
        raise RuntimeError("not used in inbox sequencing test")

    def available_provider_names(self, **kwargs):
        return ["ollama"]


class _NoopSkillRegistry:
    def get_all_tools(self):
        return []

    def get_skill_for_tool(self, tool_name: str):
        return None


@pytest.mark.asyncio
async def test_inbox_processes_messages_sequentially_without_drop() -> None:
    _ensure_paths()
    from bot.orchestrator import Orchestrator

    orch = Orchestrator(
        db=None,
        project_manager=SimpleNamespace(db=None),
        provider_router=_NoopProviderRouter(),
        skill_registry=_NoopSkillRegistry(),
        gateway_api_url="http://127.0.0.1:8766",
        chat_provider_allowlist=["ollama"],
    )
    orch._coalesce_window_seconds = 0.0

    calls: list[str] = []

    async def _fake_handle_internal(update, text: str) -> str:
        calls.append(text)
        await asyncio.sleep(0.02)
        return f"ack:{text}"

    orch._handle_internal = _fake_handle_internal

    update_a = _FakeUpdate(user_id=777)
    update_b = _FakeUpdate(user_id=777)

    task_a = asyncio.create_task(orch.handle(update_a, "message A"))
    await asyncio.sleep(0.001)
    task_b = asyncio.create_task(orch.handle(update_b, "message B"))

    reply_a, reply_b = await asyncio.gather(task_a, task_b)
    assert calls == ["message A", "message B"]
    assert reply_a == "ack:message A"
    assert reply_b == "ack:message B"
    assert "still working" not in reply_a.lower()
    assert "still working" not in reply_b.lower()


@pytest.mark.asyncio
async def test_inbox_coalesces_burst_messages_without_losing_content() -> None:
    _ensure_paths()
    from bot.orchestrator import Orchestrator

    orch = Orchestrator(
        db=None,
        project_manager=SimpleNamespace(db=None),
        provider_router=_NoopProviderRouter(),
        skill_registry=_NoopSkillRegistry(),
        gateway_api_url="http://127.0.0.1:8766",
        chat_provider_allowlist=["ollama"],
    )
    orch._coalesce_window_seconds = 0.05

    handled: list[str] = []

    async def _fake_handle_internal(update, text: str) -> str:
        handled.append(text)
        return f"ack:{text}"

    orch._handle_internal = _fake_handle_internal

    update_a = _FakeUpdate(user_id=778)
    update_b = _FakeUpdate(user_id=778)

    task_a = asyncio.create_task(orch.handle(update_a, "part one"))
    await asyncio.sleep(0.001)
    task_b = asyncio.create_task(orch.handle(update_b, "part two"))
    reply_a, reply_b = await asyncio.gather(task_a, task_b)

    assert handled == ["part one\npart two"]
    assert reply_a == "ack:part one\npart two"
    assert reply_b == ""
