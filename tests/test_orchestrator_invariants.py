from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


def _ensure_paths() -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)


def _make_session(*, project_id: str | None, metadata: dict | None = None):
    from bot.session import Session

    return Session(
        user_id="999",
        project_id=project_id,
        conversation_phase="planning",
        last_intent="",
        last_mode="planning",
        last_message_at=None,
        time_gap=None,
        metadata=metadata or {},
        project={"id": project_id, "name": "proj"} if project_id else None,
    )


def test_scope_defaults_to_active_project() -> None:
    _ensure_paths()
    from bot.invariants import resolve_scope

    session = _make_session(project_id="proj-1")
    scope = resolve_scope(session, "new idea: add yurekaaa popup", "")
    assert scope.scope == "active"
    assert scope.project_id == "proj-1"
    assert scope.switch_intent is False


def test_short_new_without_pending_question_does_not_switch_scope() -> None:
    _ensure_paths()
    from bot.invariants import resolve_scope

    session = _make_session(project_id="proj-1", metadata={})
    scope = resolve_scope(session, "new", "")
    assert scope.scope == "active"
    assert scope.scope_answer is None
    assert scope.switch_intent is False


def test_short_new_with_scope_question_switches_scope() -> None:
    _ensure_paths()
    from bot.invariants import resolve_scope

    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    session = _make_session(
        project_id="proj-1",
        metadata={
            "pending_question": {
                "type": "choose_project_scope",
                "choices": ["existing", "new"],
                "turn_id": "t1",
                "expires_at": expires,
                "payload": {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    scope = resolve_scope(session, "new", "")
    assert scope.scope == "new"
    assert scope.scope_answer == "new"
    assert scope.switch_intent is True


def test_write_intent_executes_on_active_scope() -> None:
    _ensure_paths()
    from bot.invariants import enforce_continuity, resolve_scope
    from bot.intent import ClassifiedIntent

    session = _make_session(project_id="proj-1")
    scope = resolve_scope(session, "python app with tkinter and beep", "")
    decision = enforce_continuity(
        ClassifiedIntent(intent="propose_idea", confidence=0.95),
        scope,
        session,
    )
    assert decision.execute_write_intent is True
    assert decision.ask_question is False
    assert decision.target_project_id == "proj-1"


@pytest.mark.asyncio
async def test_conversation_switching_isolates_active_project_context() -> None:
    _ensure_paths()
    from core.conversation_manager import ConversationManager
    from db import schema, store

    db = await schema.init_db(":memory:")
    try:
        user = await store.ensure_user(db, telegram_user_id=12345, username="tester")
        cm = ConversationManager(db)

        conv1 = await cm.create_conversation(user["id"], title="Conversation A")
        conv2 = await cm.create_conversation(user["id"], title="Conversation B")

        project1 = await store.create_project(db, name="alpha_proj", display_name="Alpha", local_path="/tmp/alpha")
        project2 = await store.create_project(db, name="beta_proj", display_name="Beta", local_path="/tmp/beta")

        await cm.set_active_project(conv1.conversation_id, project1["id"])
        await cm.set_active_project(conv2.conversation_id, project2["id"])

        await cm.set_active_conversation(user["id"], conv1.conversation_id)
        active = await cm.get_or_create_active_conversation(user["id"])
        assert active.conversation_id == conv1.conversation_id
        assert active.active_project_id == project1["id"]

        await cm.set_active_conversation(user["id"], conv2.conversation_id)
        active = await cm.get_or_create_active_conversation(user["id"])
        assert active.conversation_id == conv2.conversation_id
        assert active.active_project_id == project2["id"]

        reloaded_conv1 = await cm.get_conversation(conv1.conversation_id)
        assert reloaded_conv1 is not None
        assert reloaded_conv1.active_project_id == project1["id"]
    finally:
        await db.close()
