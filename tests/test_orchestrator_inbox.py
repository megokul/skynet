"""Regression tests for orchestrator inbox queue behavior.

Purpose:
- Ensure message processing remains sequential and lossless per user/conversation.
- Verify short-window coalescing combines burst messages correctly.
- Prevent duplicate responses or dropped futures in queue worker paths."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def _ensure_paths() -> None:
    """
    Ensure paths.
    
    Purpose:
    - Implement `_ensure_paths` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


class _FakeUser:
    """
    FakeUser.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_FakeUser`.
    """

    def __init__(self, user_id: int):
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `user_id`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.id = user_id


class _FakeUpdate:
    """
    FakeUpdate.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_FakeUpdate`.
    """

    def __init__(self, user_id: int):
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `user_id`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.effective_user = _FakeUser(user_id)
        self.effective_chat = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(message_id=1)


class _NoopProviderRouter:
    """
    NoopProviderRouter.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_NoopProviderRouter`.
    """

    async def chat(self, *args, **kwargs):
        """
        Chat.
        
        Purpose:
        - Implement `chat` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `*args`: input used by this function to compute or route work.
        - `**kwargs`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        raise RuntimeError("not used in inbox sequencing test")

    def available_provider_names(self, **kwargs):
        """
        Available provider names.
        
        Purpose:
        - Implement `available_provider_names` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `**kwargs`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        return ["ollama"]


class _NoopSkillRegistry:
    """
    NoopSkillRegistry.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_NoopSkillRegistry`.
    """

    def get_all_tools(self):
        """
        Get all tools.
        
        Purpose:
        - Implement `get_all_tools` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        return []

    def get_skill_for_tool(self, tool_name: str):
        """
        Get skill for tool.
        
        Purpose:
        - Implement `get_skill_for_tool` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `tool_name`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        return None


@pytest.mark.asyncio
async def test_inbox_processes_messages_sequentially_without_drop() -> None:
    """
    Test scenario `test_inbox_processes_messages_sequentially_without_drop`.
    
    Purpose:
    - Implement `test_inbox_processes_messages_sequentially_without_drop` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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
        """
        Fake handle internal.
        
        Purpose:
        - Implement `_fake_handle_internal` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

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
    """
    Test scenario `test_inbox_coalesces_burst_messages_without_losing_content`.
    
    Purpose:
    - Implement `test_inbox_coalesces_burst_messages_without_losing_content` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

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
        """
        Fake handle internal.
        
        Purpose:
        - Implement `_fake_handle_internal` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `update`: input used by this function to compute or route work.
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

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
