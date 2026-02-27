"""Integration-style tests for commander engine and specialist delegation flows.

Purpose:
- Validate role delegation, conversation switching, and project lifecycle behavior.
- Exercise inbox sequencing and specialist interactions with deterministic fake routers.
- Catch regressions in end-to-end engine orchestration semantics.

How it works:
- Builds in-memory DB fixtures and lightweight fake provider/project-manager collaborators.
- Sends user messages through real engine entrypoints.
- Asserts DB state and role transitions after each scenario."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import sys

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


@pytest.fixture(autouse=True)
def trace_blocks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    _ensure_paths()
    from core import dev_trace

    blocks: list[str] = []

    def _capture(text: str) -> None:
        blocks.append(text)

    monkeypatch.setattr(dev_trace, "_append_trace_block", _capture)
    return blocks


class _ProviderResponse:
    """
    ProviderResponse.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_ProviderResponse`.
    """

    def __init__(self, text: str):
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
        - `text`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.text = text
        self.tool_calls = []


class _FakeProviderRouter:
    """
    FakeProviderRouter.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_FakeProviderRouter`.
    """

    def __init__(self, scripted_text: list[str] | None = None):
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
        - `scripted_text`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self._scripted = deque(scripted_text or [])
        self.calls: list[dict] = []

    async def chat(self, messages, *, tools=None, system=None, max_tokens=0, task_type="general", allowed_providers=None):
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
        - `messages`: input used by this function to compute or route work.
        - `tools`: input used by this function to compute or route work.
        - `system`: input used by this function to compute or route work.
        - `max_tokens`: input used by this function to compute or route work.
        - `task_type`: input used by this function to compute or route work.
        - `allowed_providers`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.calls.append({
            "messages": messages,
            "tools": tools,
            "system": system,
            "task_type": task_type,
            "allowed_providers": allowed_providers,
        })
        if self._scripted:
            return _ProviderResponse(self._scripted.popleft())
        return _ProviderResponse('{"intent":"exploratory","confidence":0.1,"entities":{},"recommended_role":"igris"}')


class _FakeProjectManager:
    """
    FakeProjectManager.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `_FakeProjectManager`.
    """

    def __init__(self, db):
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
        - `db`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.db = db
        self.started: list[str] = []

    async def create_project(self, name: str):
        """
        Create project.
        
        Purpose:
        - Implement `create_project` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `name`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        from db import store

        normalized = "".join(ch for ch in name.lower().replace(" ", "-") if ch.isalnum() or ch in {"-", "_"}).strip("-")
        normalized = normalized or "project"
        existing = await store.get_project_by_name(self.db, normalized)
        if existing:
            return existing
        return await store.create_project(self.db, name=normalized, display_name=name, local_path="")

    async def start_execution(self, project_id: str):
        """
        Start execution.
        
        Purpose:
        - Implement `start_execution` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `project_id`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.started.append(project_id)


@pytest.mark.asyncio
async def test_igris_delegates_to_project_specialist(trace_blocks: list[str]) -> None:
    """
    Test scenario `test_igris_delegates_to_project_specialist`.
    
    Purpose:
    - Implement `test_igris_delegates_to_project_specialist` within this module's workflow.
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
    from core.engine import ConversationEngine
    from db import schema

    db = await schema.init_db(":memory:")
    try:
        router = _FakeProviderRouter([
            '{"intent":"start_project","confidence":0.93,"entities":{},"recommended_role":"project_specialist"}',
        ])
        pm = _FakeProjectManager(db)
        engine = ConversationEngine(db, router, project_manager=pm)

        result = await engine.process_user_message(telegram_user_id=1001, text="can we start a new project")
        assert "name" in result.text.lower()

        conversations = await engine.list_user_conversations(telegram_user_id=1001)
        assert conversations
        assert conversations[0].active_role == "project_specialist"
        assert trace_blocks
        trace_content = "".join(trace_blocks)
        assert "trace_id:" in trace_content
        assert "call_sequence:" in trace_content
        assert 'phase: "PHASE 1 - Entry & Normalisation"' in trace_content
        assert 'phase: "PHASE 2 - Intent Resolution"' in trace_content
        assert 'phase: "PHASE 3 - Role Routing"' in trace_content
        assert 'phase: "PHASE 4 - Specialist Execution"' in trace_content
        assert 'phase: "PHASE 6 - Response Construction"' in trace_content
        assert "summary:" in trace_content
        assert "role_events:" in trace_content
        assert "[STEP" not in trace_content
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_conversation_switching_keeps_project_isolation() -> None:
    """
    Test scenario `test_conversation_switching_keeps_project_isolation`.
    
    Purpose:
    - Implement `test_conversation_switching_keeps_project_isolation` within this module's workflow.
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
    from core.engine import ConversationEngine
    from db import schema, store

    db = await schema.init_db(":memory:")
    try:
        router = _FakeProviderRouter()
        pm = _FakeProjectManager(db)
        engine = ConversationEngine(db, router, project_manager=pm)

        conv_a = await engine.start_new_conversation(telegram_user_id=1002, title="A")
        conv_b = await engine.start_new_conversation(telegram_user_id=1002, title="B")
        user = await store.get_user_by_telegram_id(db, 1002)

        p1 = await store.create_project(db, name="proj_a", display_name="Proj A", local_path="")
        p2 = await store.create_project(db, name="proj_b", display_name="Proj B", local_path="")

        await engine.conversation_manager.set_active_project(conv_a.id, p1["id"])
        await engine.conversation_manager.set_active_project(conv_b.id, p2["id"])

        await engine.switch_conversation(telegram_user_id=1002, conversation_id=conv_a.id)
        active = await engine.conversation_manager.get_or_create_active_conversation(int(user["id"]))
        assert active.id == conv_a.id
        assert active.active_project_id == p1["id"]

        await engine.switch_conversation(telegram_user_id=1002, conversation_id=conv_b.id)
        active = await engine.conversation_manager.get_or_create_active_conversation(int(user["id"]))
        assert active.id == conv_b.id
        assert active.active_project_id == p2["id"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_project_specialist_full_creation_flow_returns_to_igris() -> None:
    """
    Test scenario `test_project_specialist_full_creation_flow_returns_to_igris`.
    
    Purpose:
    - Implement `test_project_specialist_full_creation_flow_returns_to_igris` within this module's workflow.
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
    from core.engine import ConversationEngine
    from db import schema, store

    db = await schema.init_db(":memory:")
    try:
        router = _FakeProviderRouter([
            '{"intent":"start_project","confidence":0.95,"entities":{},"recommended_role":"project_specialist"}',
        ])
        pm = _FakeProjectManager(db)
        engine = ConversationEngine(db, router, project_manager=pm)

        first = await engine.process_user_message(telegram_user_id=1003, text="start new project")
        assert "name" in first.text.lower()

        second = await engine.process_user_message(telegram_user_id=1003, text="yureka-app")
        assert "what should this project do" in second.text.lower()

        third = await engine.process_user_message(telegram_user_id=1003, text="show a tkinter popup and beep")
        assert "requirements captured" in third.text.lower()

        conversations = await engine.list_user_conversations(telegram_user_id=1003)
        assert conversations[0].active_role == "igris"
        assert conversations[0].active_project_id

        ideas = await store.get_ideas(db, conversations[0].active_project_id)
        assert ideas and "tkinter" in ideas[-1]["message_text"].lower()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_weather_specialist_invocation_without_network(monkeypatch) -> None:
    """
    Test scenario `test_weather_specialist_invocation_without_network`.
    
    Purpose:
    - Implement `test_weather_specialist_invocation_without_network` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `monkeypatch`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    _ensure_paths()
    from core.engine import ConversationEngine
    from core.roles.weather_specialist import WeatherSpecialistRole
    from db import schema

    db = await schema.init_db(":memory:")
    try:
        router = _FakeProviderRouter([
            '{"intent":"weather","confidence":0.91,"entities":{},"recommended_role":"weather_specialist"}',
            '{"location":"London","confidence":0.9}',
        ])
        pm = _FakeProjectManager(db)
        engine = ConversationEngine(db, router, project_manager=pm)

        async def _fake_fetch(self, location: str) -> str:
            """
            Fake fetch.
            
            Purpose:
            - Implement `_fake_fetch` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `location`: input used by this function to compute or route work.
            
            Returns:
            - Return value typed as `str` when available; otherwise side effects only.
            """

            return f"Weather for {location}: sunny"

        monkeypatch.setattr(WeatherSpecialistRole, "_fetch_weather", _fake_fetch)

        result = await engine.process_user_message(telegram_user_id=1004, text="weather in london")
        assert "weather for london" in result.text.lower()

        conversations = await engine.list_user_conversations(telegram_user_id=1004)
        assert conversations[0].active_role == "igris"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_reminder_specialist_creates_db_reminder() -> None:
    """
    Test scenario `test_reminder_specialist_creates_db_reminder`.
    
    Purpose:
    - Implement `test_reminder_specialist_creates_db_reminder` within this module's workflow.
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
    from core.engine import ConversationEngine
    from db import schema, store

    db = await schema.init_db(":memory:")
    try:
        router = _FakeProviderRouter([
            '{"intent":"set_reminder","confidence":0.90,"entities":{},"recommended_role":"reminder_specialist"}',
            '{"title":"Call mom","due_at":"2026-03-01T10:00:00+00:00","notes":"","confidence":0.92}',
        ])
        pm = _FakeProjectManager(db)
        engine = ConversationEngine(db, router, project_manager=pm)

        result = await engine.process_user_message(telegram_user_id=1005, text="remind me to call mom tomorrow at 10")
        assert "reminder created" in result.text.lower()

        user = await store.get_user_by_telegram_id(db, 1005)
        reminders = await store.list_reminders(db, user_id=user["id"])
        assert reminders
        assert reminders[0]["title"] == "Call mom"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_coding_specialist_queues_background_execution() -> None:
    """
    Test scenario `test_coding_specialist_queues_background_execution`.
    
    Purpose:
    - Implement `test_coding_specialist_queues_background_execution` within this module's workflow.
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
    from core.engine import ConversationEngine
    from db import schema, store

    db = await schema.init_db(":memory:")
    try:
        router = _FakeProviderRouter()
        pm = _FakeProjectManager(db)
        engine = ConversationEngine(db, router, project_manager=pm)

        # Prepare user + conversation + approved project
        user = await store.ensure_user(db, telegram_user_id=1006)
        conv = await engine.conversation_manager.create_conversation(user["id"], title="coding")
        project = await store.create_project(db, name="code_proj", display_name="Code Proj", local_path="")
        await store.update_project(db, project["id"], status="approved")
        await engine.conversation_manager.set_active_project(conv.id, project["id"])
        await engine.conversation_manager.set_active_role(conv.id, "coding_specialist")

        result = await engine.process_user_message(
            telegram_user_id=1006,
            text="implement the first milestone",
            conversation_id=conv.id,
        )
        assert "queued" in result.text.lower()

        await asyncio.sleep(0.05)
        assert project["id"] in pm.started
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_inbox_messages_are_sequential_and_not_dropped() -> None:
    """
    Test scenario `test_inbox_messages_are_sequential_and_not_dropped`.
    
    Purpose:
    - Implement `test_inbox_messages_are_sequential_and_not_dropped` within this module's workflow.
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
    from core.engine import ConversationEngine
    from db import schema

    db = await schema.init_db(":memory:")
    try:
        router = _FakeProviderRouter()
        pm = _FakeProjectManager(db)
        engine = ConversationEngine(db, router, project_manager=pm)
        engine.inbox._coalesce_window = 0.0

        calls: list[str] = []

        async def _fake_run(conversation, text: str) -> str:
            """
            Fake run.
            
            Purpose:
            - Implement `_fake_run` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `conversation`: input used by this function to compute or route work.
            - `text`: input used by this function to compute or route work.
            
            Returns:
            - Return value typed as `str` when available; otherwise side effects only.
            """

            calls.append(text)
            await asyncio.sleep(0.02)
            return f"ack:{text}"

        engine._run_role_cycle = _fake_run  # type: ignore[assignment]

        t1 = asyncio.create_task(engine.process_user_message(telegram_user_id=1007, text="A"))
        await asyncio.sleep(0.001)
        t2 = asyncio.create_task(engine.process_user_message(telegram_user_id=1007, text="B"))

        r1, r2 = await asyncio.gather(t1, t2)

        assert calls == ["A", "B"]
        assert r1.text == "ack:A"
        assert r2.text == "ack:B"
    finally:
        await db.close()

