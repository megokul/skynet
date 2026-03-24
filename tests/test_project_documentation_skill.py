from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


def _load_skill_module():
    repo_root = Path(__file__).parent.parent
    module_path = repo_root / "skills" / "skynet-project-documentation" / "handler.py"
    spec = importlib.util.spec_from_file_location("skynet_project_documentation_handler", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load skills/skynet-project-documentation/handler.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_control_plane_client_reads_use_supported_paths(monkeypatch) -> None:
    skill = _load_skill_module()
    calls: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True}

    def _fake_get(url: str, **_: object) -> _Response:
        calls.append(url)
        return _Response()

    monkeypatch.setattr(skill, "requests", SimpleNamespace(get=_fake_get))
    client = skill.ControlPlaneClient(skill.ControlPlaneConfig(base_url="http://control-plane"))

    client.list_tasks()
    client.list_file_ownership()

    assert calls == [
        "http://control-plane/v1/tasks",
        "http://control-plane/v1/files/ownership",
    ]


def test_finalize_plan_enqueues_only_schema_compatible_tasks(monkeypatch, tmp_path: Path) -> None:
    skill = _load_skill_module()
    created = skill.create_project("Demo Project", project_id="proj-docs", root_dir=str(tmp_path))
    project_dir = Path(created["project_dir"])
    plan_path = project_dir / "planning" / "task_plan.md"
    plan_path.write_text(
        (
            "STATUS: FINALIZED\n\n"
            "# Project Plan\n\n"
            "### TASK-001: Write application file\n"
            "Dependencies:\n"
            "Outputs:\n"
            "  - src/app.py\n\n"
            "### TASK-002: Document the change\n"
            "Dependencies: TASK-001\n"
            "Outputs:\n"
            "  - docs/notes.md\n"
        ),
        encoding="utf-8",
    )
    skill.write_yaml(
        project_dir / "control" / "TASK_GRAPH.yaml",
        {
            "tasks": {
                "TASK-001": {
                    "title": "Write application file",
                    "dependencies": [],
                    "outputs": ["src/app.py"],
                    "action": "file_write",
                    "params": {
                        "path": "src/app.py",
                        "content": "print('hello')\n",
                    },
                    "priority": 5,
                    "required_files": ["src/app.py"],
                },
                "TASK-002": {
                    "title": "Document the change",
                    "dependencies": ["TASK-001"],
                    "outputs": ["docs/notes.md"],
                },
            }
        },
    )

    payloads: list[dict[str, object]] = []

    class _FakeClient:
        def __init__(self, _cfg) -> None:
            return None

        def enqueue_task(self, payload: dict[str, object]) -> dict[str, object]:
            payloads.append(payload)
            return {"task": {"id": payload["task_id"]}}

    monkeypatch.setattr(skill, "ControlPlaneClient", _FakeClient)

    result = skill.finalize_plan_and_enqueue(str(project_dir), gateway_hint="gateway-1")

    assert result["ok"] is True
    assert len(result["enqueued"]) == 1
    assert result["skipped"] == [
        {
            "task_id": "TASK-002",
            "reason": "missing control-plane action metadata in control/TASK_GRAPH.yaml",
        }
    ]
    assert payloads == [
        {
            "action": "file_write",
            "params": {
                "path": "src/app.py",
                "content": "print('hello')\n",
                "project_id": "proj-docs",
            },
            "task_id": "TASK-001",
            "priority": 5,
            "dependencies": [],
            "required_files": ["src/app.py"],
            "gateway_id": "gateway-1",
        }
    ]


def test_sync_progress_filters_client_side_and_uses_task_graph(monkeypatch, tmp_path: Path) -> None:
    skill = _load_skill_module()
    created = skill.create_project("Progress Demo", project_id="proj-progress", root_dir=str(tmp_path))
    project_dir = Path(created["project_dir"])
    skill.write_yaml(
        project_dir / "control" / "TASK_GRAPH.yaml",
        {
            "tasks": {
                "TASK-001": {
                    "title": "Bootstrap",
                    "dependencies": [],
                    "outputs": ["src/app.py"],
                },
                "TASK-002": {
                    "title": "Refactor",
                    "dependencies": ["TASK-001"],
                    "outputs": ["src/app.py"],
                    "required_files": ["src/app.py"],
                },
                "TASK-003": {
                    "title": "Write docs",
                    "dependencies": ["TASK-001"],
                    "outputs": ["docs/notes.md"],
                    "required_files": ["src/app.py"],
                },
            }
        },
    )

    class _FakeClient:
        def __init__(self, _cfg) -> None:
            return None

        def list_tasks(self) -> dict[str, object]:
            return {
                "tasks": [
                    {
                        "id": "TASK-001",
                        "status": "succeeded",
                        "dependencies": [],
                        "updated_at": "2026-03-12T10:00:00+00:00",
                    },
                    {
                        "id": "TASK-002",
                        "status": "running",
                        "locked_by": "worker-1",
                        "locked_at": "2026-03-12T10:05:00+00:00",
                        "dependencies": ["TASK-001"],
                        "required_files": ["src/app.py"],
                        "params": {"project_id": "proj-progress"},
                        "updated_at": "2026-03-12T10:05:00+00:00",
                    },
                    {
                        "id": "OTHER-001",
                        "status": "running",
                        "locked_by": "worker-2",
                        "params": {"project_id": "other-project"},
                    },
                ]
            }

        def list_file_ownership(self) -> dict[str, object]:
            return {
                "ownership": [
                    {
                        "file_path": "src/app.py",
                        "owning_task": "TASK-002",
                        "claim_token": "claim-1",
                        "claimed_at": "2026-03-12T10:05:00+00:00",
                    },
                    {
                        "file_path": "src/other.py",
                        "owning_task": "OTHER-001",
                        "claim_token": "claim-2",
                        "claimed_at": "2026-03-12T10:06:00+00:00",
                    },
                ]
            }

    monkeypatch.setattr(skill, "ControlPlaneClient", _FakeClient)

    result = skill.sync_progress(str(project_dir))
    ledger = skill.read_yaml(project_dir / "control" / "EXECUTION_LEDGER.yaml")
    next_actions = skill.read_yaml(project_dir / "control" / "NEXT_ACTIONS.yaml")

    assert result == {
        "ok": True,
        "project_id": "proj-progress",
        "total": 3,
        "completed": 1,
        "active": 1,
        "pending": 1,
        "eligible_next": ["TASK-003"],
    }
    assert ledger["summary"] == {
        "total_tasks": 3,
        "completed_tasks": 1,
        "active_tasks": 1,
        "pending_tasks": 1,
    }
    assert ledger["blockers"] == [
        {
            "type": "file_ownership",
            "file_path": "src/app.py",
            "owning_task": "TASK-002",
            "claimed_at": "2026-03-12T10:05:00+00:00",
        }
    ]
    assert next_actions["next_actions"] == [
        {
            "task_id": "TASK-003",
            "title": "Write docs",
            "priority": "high",
            "eligible": True,
            "dependencies_satisfied": True,
            "safe_to_start": False,
            "blocking_files": ["src/app.py"],
        }
    ]
