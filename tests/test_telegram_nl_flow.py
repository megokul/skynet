"""Telegram bot natural-language flow regressions.

Tests the LLM-first conversation architecture introduced to replace the
old waterfall intent pipeline. Functions under test are the lightweight
helpers that remain in nl_intent.py plus the ProjectManagementSkill tools.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


def _ensure_gateway_path() -> None:
    """
    Ensure gateway path.
    
    Purpose:
    - Implement `_ensure_gateway_path` within this module's workflow.
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


# ---------------------------------------------------------------------------
# _is_pure_greeting
# ---------------------------------------------------------------------------

def test_pure_greeting_hi() -> None:
    """
    Test scenario `test_pure_greeting_hi`.
    
    Purpose:
    - Implement `test_pure_greeting_hi` within this module's workflow.
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

    _ensure_gateway_path()
    from bot.nl_intent import _is_pure_greeting

    assert _is_pure_greeting("hi") is True
    assert _is_pure_greeting("Hello!") is True
    assert _is_pure_greeting("hey there") is True
    assert _is_pure_greeting("good morning") is True


def test_pure_greeting_rejects_substantive_text() -> None:
    """
    Test scenario `test_pure_greeting_rejects_substantive_text`.
    
    Purpose:
    - Implement `test_pure_greeting_rejects_substantive_text` within this module's workflow.
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

    _ensure_gateway_path()
    from bot.nl_intent import _is_pure_greeting

    assert _is_pure_greeting("hi, start a project") is False
    assert _is_pure_greeting("hello, what projects do I have?") is False
    assert _is_pure_greeting("build the app") is False
    assert _is_pure_greeting("") is False


def test_pure_greeting_case_insensitive() -> None:
    """
    Test scenario `test_pure_greeting_case_insensitive`.
    
    Purpose:
    - Implement `test_pure_greeting_case_insensitive` within this module's workflow.
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

    _ensure_gateway_path()
    from bot.nl_intent import _is_pure_greeting

    assert _is_pure_greeting("HI") is True
    assert _is_pure_greeting("HELLO") is True
    assert _is_pure_greeting("Hey Skynet") is True


# ---------------------------------------------------------------------------
# _is_new_project_intent
# ---------------------------------------------------------------------------

def test_new_project_intent_positive_cases() -> None:
    """
    Test scenario `test_new_project_intent_positive_cases`.
    
    Purpose:
    - Implement `test_new_project_intent_positive_cases` within this module's workflow.
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

    _ensure_gateway_path()
    from bot.nl_intent import _is_new_project_intent

    assert _is_new_project_intent("can we start a project") is True
    assert _is_new_project_intent("start a project") is True
    assert _is_new_project_intent("create a project") is True
    assert _is_new_project_intent("new project") is True
    assert _is_new_project_intent("make a new project") is True
    assert _is_new_project_intent("i want to create a project") is True
    assert _is_new_project_intent("let's begin a new project") is True
    assert _is_new_project_intent("can you help me start a project called myapp") is True
    assert _is_new_project_intent("new app please") is True


def test_new_project_intent_negative_cases() -> None:
    """
    Test scenario `test_new_project_intent_negative_cases`.
    
    Purpose:
    - Implement `test_new_project_intent_negative_cases` within this module's workflow.
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

    _ensure_gateway_path()
    from bot.nl_intent import _is_new_project_intent

    # These should NOT match — no new-project creation signal
    assert _is_new_project_intent("what projects do I have") is False
    assert _is_new_project_intent("list my projects") is False
    assert _is_new_project_intent("add idea to the project") is False
    assert _is_new_project_intent("what's the status of my project") is False
    assert _is_new_project_intent("hi") is False
    assert _is_new_project_intent("how's the boomboom project going") is False
    assert _is_new_project_intent("") is False


# ---------------------------------------------------------------------------
# _resolve_project — no project manager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_project_no_manager_returns_error() -> None:
    """
    Test scenario `test_resolve_project_no_manager_returns_error`.
    
    Purpose:
    - Implement `test_resolve_project_no_manager_returns_error` within this module's workflow.
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

    _ensure_gateway_path()
    from bot import state
    from bot.nl_intent import _resolve_project

    original = state._project_manager
    try:
        state._project_manager = None
        project, err = await _resolve_project()
        assert project is None
        assert err is not None
        assert "not initialized" in err.lower()
    finally:
        state._project_manager = original


# ---------------------------------------------------------------------------
# ProjectManagementSkill — basic tool interface
# ---------------------------------------------------------------------------

def _make_context():
    """Build a minimal SkillContext for testing."""
    _ensure_gateway_path()
    from skills.base import SkillContext
    return SkillContext(
        project_id="",
        project_path="",
        gateway_api_url="http://127.0.0.1:8766",
    )


@pytest.mark.asyncio
async def test_project_skill_no_manager_returns_error() -> None:
    """
    Test scenario `test_project_skill_no_manager_returns_error`.
    
    Purpose:
    - Implement `test_project_skill_no_manager_returns_error` within this module's workflow.
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

    _ensure_gateway_path()
    from skills.project_skill import ProjectManagementSkill
    from bot import state

    skill = ProjectManagementSkill()
    ctx = _make_context()

    original = state._project_manager
    try:
        state._project_manager = None
        result = await skill.execute("project_create", {"name": "test"}, ctx)
        assert "error" in result.lower() or "manager" in result.lower()
    finally:
        state._project_manager = original


@pytest.mark.asyncio
async def test_project_skill_create_empty_name_returns_error() -> None:
    """
    Test scenario `test_project_skill_create_empty_name_returns_error`.
    
    Purpose:
    - Implement `test_project_skill_create_empty_name_returns_error` within this module's workflow.
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

    _ensure_gateway_path()
    from skills.project_skill import ProjectManagementSkill
    from bot import state

    skill = ProjectManagementSkill()
    ctx = _make_context()

    class _DummyManager:
        """
        DummyManager.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyManager`.
        """

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

            return {"id": "x", "name": name, "status": "ideation", "bootstrap_ok": True, "bootstrap_summary": ""}

    original = state._project_manager
    try:
        state._project_manager = _DummyManager()
        result = await skill.execute("project_create", {"name": ""}, ctx)
        assert "required" in result.lower() or "name" in result.lower() or "error" in result.lower()
    finally:
        state._project_manager = original


@pytest.mark.asyncio
async def test_project_skill_list_with_empty_project_list() -> None:
    """
    Test scenario `test_project_skill_list_with_empty_project_list`.
    
    Purpose:
    - Implement `test_project_skill_list_with_empty_project_list` within this module's workflow.
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

    _ensure_gateway_path()
    from skills.project_skill import ProjectManagementSkill
    from bot import state

    skill = ProjectManagementSkill()
    ctx = _make_context()

    class _DummyManager:
        """
        DummyManager.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyManager`.
        """

        async def list_projects(self):
            """
            List projects.
            
            Purpose:
            - Implement `list_projects` within this module's workflow.
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

    original = state._project_manager
    try:
        state._project_manager = _DummyManager()
        result = await skill.execute("project_list", {}, ctx)
        assert "no projects" in result.lower()
    finally:
        state._project_manager = original


@pytest.mark.asyncio
async def test_project_skill_create_calls_manager() -> None:
    """
    Test scenario `test_project_skill_create_calls_manager`.
    
    Purpose:
    - Implement `test_project_skill_create_calls_manager` within this module's workflow.
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

    _ensure_gateway_path()
    from skills.project_skill import ProjectManagementSkill
    from bot import state

    skill = ProjectManagementSkill()
    ctx = _make_context()
    created_names: list[str] = []

    class _DummyManager:
        """
        DummyManager.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyManager`.
        """

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

            created_names.append(name)
            return {
                "id": "proj-123",
                "name": name,
                "display_name": name,
                "status": "ideation",
                "bootstrap_ok": True,
                "bootstrap_summary": "ok",
                "local_path": "/projects/TestBot",
            }

    original = state._project_manager
    try:
        state._project_manager = _DummyManager()
        result = await skill.execute("project_create", {"name": "TestBot"}, ctx)
        assert "TestBot" in result
        assert created_names == ["TestBot"]
    finally:
        state._project_manager = original


@pytest.mark.asyncio
async def test_project_skill_add_idea_no_active_project() -> None:
    """
    Test scenario `test_project_skill_add_idea_no_active_project`.
    
    Purpose:
    - Implement `test_project_skill_add_idea_no_active_project` within this module's workflow.
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

    _ensure_gateway_path()
    from skills.project_skill import ProjectManagementSkill
    from bot import state

    skill = ProjectManagementSkill()
    ctx = _make_context()

    class _DummyManager:
        """
        DummyManager.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyManager`.
        """

        async def add_idea(self, project_id: str, idea: str) -> int:
            """
            Add idea.
            
            Purpose:
            - Implement `add_idea` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `project_id`: input used by this function to compute or route work.
            - `idea`: input used by this function to compute or route work.
            
            Returns:
            - Return value typed as `int` when available; otherwise side effects only.
            """

            return 1

    original_pm = state._project_manager
    try:
        state._project_manager = _DummyManager()
        result = await skill.execute("project_add_idea", {"idea": "build a thing"}, ctx)
        # No active project → should report an error
        assert "no active project" in result.lower() or "error" in result.lower()
    finally:
        state._project_manager = original_pm
