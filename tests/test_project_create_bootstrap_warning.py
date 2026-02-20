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
