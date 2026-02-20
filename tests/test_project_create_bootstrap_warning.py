"""Project creation behavior when bootstrap fails."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


@pytest.mark.asyncio
async def test_create_project_keeps_row_when_bootstrap_fails_non_strict(monkeypatch) -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)

    from db import schema, store
    from orchestrator.project_manager import ProjectManager
    import bot_config as cfg

    class _DummyRouter:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("not used")

    class _DummyScheduler:
        gateway_url = "http://127.0.0.1:8766"

    db = await schema.init_db(":memory:")
    try:
        monkeypatch.setattr(cfg, "AUTO_BOOTSTRAP_STRICT", False)
        pm = ProjectManager(
            db=db,
            router=_DummyRouter(),
            searcher=None,  # type: ignore[arg-type]
            scheduler=_DummyScheduler(),  # type: ignore[arg-type]
            project_base_dir="E:/MyProjects",
        )

        async def _fail_bootstrap(_project):
            return ("directory: failed (SSH unavailable)", False)

        monkeypatch.setattr(pm, "_bootstrap_project_workspace", _fail_bootstrap)
        created = await pm.create_project("kundi-vanam")

        assert created["name"] == "kundi-vanam"
        assert created["bootstrap_ok"] is False
        assert "SSH unavailable" in created["bootstrap_summary"]

        reloaded = await store.get_project(db, created["id"])
        assert reloaded is not None

        events = await store.get_events(db, created["id"], limit=5)
        assert events
        assert events[0]["event_type"] == "created_with_warnings"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_create_project_deferred_bootstrap_marks_created(monkeypatch) -> None:
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)

    from db import schema, store
    from orchestrator.project_manager import ProjectManager
    import bot_config as cfg

    class _DummyRouter:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("not used")

    class _DummyScheduler:
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
    repo_root = Path(__file__).parent.parent
    gateway_root = str(repo_root / "openclaw-gateway")
    if gateway_root not in sys.path:
        sys.path.insert(0, gateway_root)

    from db import schema
    from orchestrator.project_manager import ProjectManager

    class _DummyRouter:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("not used")

    class _DummyScheduler:
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
