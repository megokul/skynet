"""Regression tests for deterministic continuity and scope invariants.

Purpose:
- Validate scope resolution behavior for active/new project transitions.
- Confirm write-intent routing decisions under pending-question conditions.
- Protect project-isolation guarantees when switching conversations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def _make_session(*, project_id: str | None, metadata: dict | None = None):
    """
    Make session.
    
    Purpose:
    - Implement `_make_session` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `project_id`: input used by this function to compute or route work.
    - `metadata`: input used by this function to compute or route work.
    
    Returns:
    - Function-specific value or side effects consumed by upstream callers.
    """

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
    """
    Test scenario `test_scope_defaults_to_active_project`.
    
    Purpose:
    - Implement `test_scope_defaults_to_active_project` within this module's workflow.
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
    from bot.invariants import resolve_scope

    session = _make_session(project_id="proj-1")
    scope = resolve_scope(session, "new idea: add yurekaaa popup", "")
    assert scope.scope == "active"
    assert scope.project_id == "proj-1"
    assert scope.switch_intent is False


def test_short_new_without_pending_question_does_not_switch_scope() -> None:
    """
    Test scenario `test_short_new_without_pending_question_does_not_switch_scope`.
    
    Purpose:
    - Implement `test_short_new_without_pending_question_does_not_switch_scope` within this module's workflow.
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
    from bot.invariants import resolve_scope

    session = _make_session(project_id="proj-1", metadata={})
    scope = resolve_scope(session, "new", "")
    assert scope.scope == "active"
    assert scope.scope_answer is None
    assert scope.switch_intent is False


def test_short_new_with_scope_question_switches_scope() -> None:
    """
    Test scenario `test_short_new_with_scope_question_switches_scope`.
    
    Purpose:
    - Implement `test_short_new_with_scope_question_switches_scope` within this module's workflow.
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
    """
    Test scenario `test_write_intent_executes_on_active_scope`.
    
    Purpose:
    - Implement `test_write_intent_executes_on_active_scope` within this module's workflow.
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
    """
    Test scenario `test_conversation_switching_isolates_active_project_context`.
    
    Purpose:
    - Implement `test_conversation_switching_isolates_active_project_context` within this module's workflow.
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
