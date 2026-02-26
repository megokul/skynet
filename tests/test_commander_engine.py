from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import sys

import pytest


def _ensure_paths() -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


class _ProviderResponse:
    def __init__(self, text: str):
        self.text = text
        self.tool_calls = []


class _FakeProviderRouter:
    def __init__(self, scripted_text: list[str] | None = None):
        self._scripted = deque(scripted_text or [])
        self.calls: list[dict] = []

    async def chat(self, messages, *, tools=None, system=None, max_tokens=0, task_type="general", allowed_providers=None):
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
    def __init__(self, db):
        self.db = db
        self.started: list[str] = []

    async def create_project(self, name: str):
        from db import store

        normalized = "".join(ch for ch in name.lower().replace(" ", "-") if ch.isalnum() or ch in {"-", "_"}).strip("-")
        normalized = normalized or "project"
        existing = await store.get_project_by_name(self.db, normalized)
        if existing:
            return existing
        return await store.create_project(self.db, name=normalized, display_name=name, local_path="")

    async def start_execution(self, project_id: str):
        self.started.append(project_id)


@pytest.mark.asyncio
async def test_igris_delegates_to_project_specialist() -> None:
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
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_conversation_switching_keeps_project_isolation() -> None:
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
