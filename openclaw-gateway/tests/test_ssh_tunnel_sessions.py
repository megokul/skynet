from __future__ import annotations

import threading
import types

from ssh_tunnel_sessions import (
    get_active_session,
    pop_active_session,
    register_active_session,
    runtime_trace_context,
    trace_fields,
    update_active_session,
)


def _executor_stub():
    return types.SimpleNamespace(
        _trace_local=types.SimpleNamespace(ctx={"trace_id": "t-1", "span_id": "s-1"}),
        _active_sessions={},
        _active_sessions_lock=threading.Lock(),
    )


def test_session_registry_round_trip() -> None:
    executor = _executor_stub()
    register_active_session(executor, "sess-1", stage="codex")
    update_active_session(executor, "sess-1", remote_pid="999")

    session = get_active_session(executor, "sess-1")
    assert session["stage"] == "codex"
    assert session["remote_pid"] == "999"

    popped = pop_active_session(executor, "sess-1")
    assert popped["session_key"] == "sess-1"
    assert get_active_session(executor, "sess-1") == {}


def test_trace_fields_include_runtime_defaults() -> None:
    executor = _executor_stub()
    fields = trace_fields(
        executor,
        runtime_trace_context(executor),
        action_name="run_coding_agent",
        working_dir="/tmp/project",
    )

    assert fields["trace_id"] == "t-1"
    assert fields["action_name"] == "run_coding_agent"
    assert fields["working_dir"] == "/tmp/project"
