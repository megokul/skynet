from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from bot.handlers import coding_stop_cleanup


@pytest.mark.asyncio
async def test_stop_requested_during_action_cancels_remote_session() -> None:
    trace_events: list[str] = []

    async def _emit_runtime_trace_async(event: str, **_kwargs):
        trace_events.append(event)

    deps = coding_stop_cleanup.StopCleanupDeps(
        cfg=types.SimpleNamespace(
            CONTROL_LOOP_DEFAULT_WORKER_ID="worker-primary",
            RUNTIME_TRACE_STOP_CLEANUP_EVENTS=True,
            RUNTIME_TRACE_REMOTE_PROBE_TIMEOUT_SECONDS=8,
        ),
        send_action=AsyncMock(
            return_value={
                "status": "ok",
                "result": {
                    "returncode": 0,
                    "process_tree": [{"pid": 1}],
                    "prompt_file": {"exists": False},
                    "artifact_snapshot": [],
                    "artifact_count": 0,
                    "remote_pid": "999",
                    "cleanup_status": "killed",
                    "orphaned": False,
                },
            }
        ),
        emit_runtime_trace_async=_emit_runtime_trace_async,
        build_debug_bundle=lambda **kwargs: kwargs,
        action_inner_result=lambda result: result.get("result", {}),
        runtime_flow=lambda: "direct",
        tracker_default_transport=lambda: "ssh_first",
        tracker_default_runtime_mode=lambda: "worker_agent",
    )

    with pytest.raises(RuntimeError, match="STOP_REQUESTED"):
        await coding_stop_cleanup.stop_requested_during_action(
            deps=deps,
            db=None,
            action="run_coding_agent",
            params={
                "session_key": "sess-1",
                "project_id": "proj-1",
                "task_id": "42",
                "graph_id": "g-1",
                "node_key": "work_1",
                "node_type": "work",
                "worker_id": "worker-primary",
                "working_dir": "/tmp/project",
                "agent": "codex",
            },
        )

    deps.send_action.assert_awaited_once()
    assert trace_events == [
        "coding.stop.requested",
        "coding.stop.remote_cancel",
        "coding.stop.remote_cancel",
    ]
