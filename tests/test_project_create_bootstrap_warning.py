"""Project creation behavior when bootstrap fails."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


@pytest.mark.asyncio
async def test_create_project_rolls_back_when_bootstrap_fails(monkeypatch) -> None:
    """When bootstrap returns ok=False, the project is always rolled back atomically."""
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)

    from db import schema, store
    from orchestrator.project_manager import ProjectManager

    class _DummyRouter:
        """
        DummyRouter.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyRouter`.
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

            raise RuntimeError("not used")

    class _DummyScheduler:
        """
        DummyScheduler.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyScheduler`.
        """

        gateway_url = "http://127.0.0.1:8766"

    db = await schema.init_db(":memory:")
    try:
        pm = ProjectManager(
            db=db,
            router=_DummyRouter(),
            searcher=None,  # type: ignore[arg-type]
            scheduler=_DummyScheduler(),  # type: ignore[arg-type]
            project_base_dir="E:/MyProjects",
        )

        async def _fail_bootstrap(_project):
            """
            Fail bootstrap.
            
            Purpose:
            - Implement `_fail_bootstrap` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `_project`: input used by this function to compute or route work.
            
            Returns:
            - Function-specific value or side effects consumed by upstream callers.
            """

            return ("directory: failed (permanent error)", False)

        monkeypatch.setattr(pm, "_bootstrap_project_workspace", _fail_bootstrap)

        with pytest.raises(ValueError, match="rolled back"):
            await pm.create_project("kundi-vanam")

        # Project row must have been removed by the rollback.
        all_projects = await store.list_projects(db)
        assert all_projects == [], "Expected project row to be rolled back on bootstrap failure"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_create_project_deferred_bootstrap_marks_created(monkeypatch) -> None:
    """
    Test scenario `test_create_project_deferred_bootstrap_marks_created`.
    
    Purpose:
    - Implement `test_create_project_deferred_bootstrap_marks_created` within this module's workflow.
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

    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)

    from db import schema, store
    from orchestrator.project_manager import ProjectManager
    import bot_config as cfg

    class _DummyRouter:
        """
        DummyRouter.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyRouter`.
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

            raise RuntimeError("not used")

    class _DummyScheduler:
        """
        DummyScheduler.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyScheduler`.
        """

        gateway_url = "http://127.0.0.1:8766"

    db = await schema.init_db(":memory:")
    try:
        monkeypatch.setattr(cfg, "AUTO_BOOTSTRAP_STRICT", True)
        pm = ProjectManager(
            db=db,
            router=_DummyRouter(),
            searcher=None,  # type: ignore[arg-type]
            scheduler=_DummyScheduler(),  # type: ignore[arg-type]
            project_base_dir="E:/MyProjects",
        )

        async def _defer_bootstrap(_project):
            """
            Defer bootstrap.
            
            Purpose:
            - Implement `_defer_bootstrap` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `_project`: input used by this function to compute or route work.
            
            Returns:
            - Function-specific value or side effects consumed by upstream callers.
            """

            return ("directory: deferred (SSH action failed: no lines in OPENSSH private key file)", True)

        monkeypatch.setattr(pm, "_bootstrap_project_workspace", _defer_bootstrap)
        created = await pm.create_project("kundi-vellam")

        assert created["name"] == "kundi-vellam"
        assert created["bootstrap_ok"] is True
        assert "deferred" in created["bootstrap_summary"].lower()

        events = await store.get_events(db, created["id"], limit=5)
        assert events
        assert events[0]["event_type"] == "created"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bootstrap_workspace_defers_on_ssh_key_error(monkeypatch) -> None:
    """
    Test scenario `test_bootstrap_workspace_defers_on_ssh_key_error`.
    
    Purpose:
    - Implement `test_bootstrap_workspace_defers_on_ssh_key_error` within this module's workflow.
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

    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)

    from db import schema
    from orchestrator.project_manager import ProjectManager

    class _DummyRouter:
        """
        DummyRouter.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyRouter`.
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

            raise RuntimeError("not used")

    class _DummyScheduler:
        """
        DummyScheduler.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_DummyScheduler`.
        """

        gateway_url = "http://127.0.0.1:8766"

    db = await schema.init_db(":memory:")
    try:
        pm = ProjectManager(
            db=db,
            router=_DummyRouter(),
            searcher=None,  # type: ignore[arg-type]
            scheduler=_DummyScheduler(),  # type: ignore[arg-type]
            project_base_dir="E:/MyProjects",
        )

        async def _run_agent_action(
            _action,
            _params,
            *,
            confirmed,
            retry_on_transient=False,
            max_attempts=2,
        ):
            """
            Run agent action.
            
            Purpose:
            - Implement `_run_agent_action` within this module's workflow.
            - Keep behavior localized so callers have one stable entrypoint.
            
            How it works:
            - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
            - Produces deterministic return data or side effects expected by calling code.
            
            Why this exists:
            - Prevents duplicated logic in upstream orchestration paths.
            - Improves debuggability by centralizing this behavior in one named function.
            
            Parameters:
            - `_action`: input used by this function to compute or route work.
            - `_params`: input used by this function to compute or route work.
            - `confirmed`: input used by this function to compute or route work.
            - `retry_on_transient`: input used by this function to compute or route work.
            - `max_attempts`: input used by this function to compute or route work.
            
            Returns:
            - Function-specific value or side effects consumed by upstream callers.
            """

            del confirmed, retry_on_transient, max_attempts
            return (False, "SSH action failed: no lines in OPENSSH private key file")

        monkeypatch.setattr(pm, "_run_agent_action", _run_agent_action)

        project = {
            "id": "proj-1",
            "name": "demo",
            "display_name": "Demo",
            "local_path": r"E:\MyProjects\demo",
        }
        summary, ok = await pm._bootstrap_project_workspace(project)
        assert ok is True
        assert "directory: deferred" in summary.lower()
    finally:
        await db.close()
