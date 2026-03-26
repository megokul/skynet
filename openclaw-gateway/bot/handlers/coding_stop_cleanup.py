from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class StopCleanupDeps:
    cfg: Any
    send_action: Callable[..., Awaitable[dict[str, Any]]]
    emit_runtime_trace_async: Callable[..., Awaitable[None]]
    build_debug_bundle: Callable[..., dict[str, Any]]
    action_inner_result: Callable[[dict[str, Any]], dict[str, Any]]
    runtime_flow: Callable[[], str]
    tracker_default_transport: Callable[[], str]
    tracker_default_runtime_mode: Callable[[], str]


async def stop_requested_during_action(
    *,
    deps: StopCleanupDeps,
    db,
    action: str,
    params: dict[str, Any],
) -> None:
    session_key = str(params.get("session_key") or "").strip()
    transport_mode = deps.tracker_default_transport()
    runtime_mode = deps.tracker_default_runtime_mode()
    common_trace = {
        "db": db,
        "flow": deps.runtime_flow(),
        "project_id": str(params.get("project_id") or ""),
        "task_id": str(params.get("task_id") or ""),
        "graph_id": str(params.get("graph_id") or ""),
        "node_key": str(params.get("node_key") or ""),
        "node_type": str(params.get("node_type") or ""),
        "phase": "coding_generation",
        "stage": str(params.get("agent") or params.get("stage") or ""),
        "worker_id": str(
            params.get("worker_id")
            or getattr(deps.cfg, "CONTROL_LOOP_DEFAULT_WORKER_ID", "")
            or "worker-primary"
        ),
        "transport": transport_mode,
        "runtime_mode": runtime_mode,
        "working_dir": str(params.get("working_dir") or ""),
        "session_key": session_key,
    }
    await deps.emit_runtime_trace_async(
        event="coding.stop.requested",
        status="start",
        action_name=action,
        **common_trace,
    )
    if session_key and bool(getattr(deps.cfg, "RUNTIME_TRACE_STOP_CLEANUP_EVENTS", True)):
        await deps.emit_runtime_trace_async(
            event="coding.stop.remote_cancel",
            status="start",
            action_name="cancel_runtime_session",
            **common_trace,
        )
        cancel = await deps.send_action(
            "cancel_runtime_session",
            {
                "session_key": session_key,
                "project_id": str(params.get("project_id") or ""),
                "task_id": str(params.get("task_id") or ""),
                "graph_id": str(params.get("graph_id") or ""),
                "node_key": str(params.get("node_key") or ""),
                "node_type": str(params.get("node_type") or ""),
                "worker_id": str(params.get("worker_id") or ""),
                "working_dir": str(params.get("working_dir") or ""),
            },
            timeout=max(
                20,
                int(getattr(deps.cfg, "RUNTIME_TRACE_REMOTE_PROBE_TIMEOUT_SECONDS", 8) or 8) * 3,
            ),
            confirmed=True,
        )
        cancel_inner = deps.action_inner_result(cancel) if cancel.get("status") != "error" else {}
        cancel_fail = cancel.get("status") == "error" or int(cancel_inner.get("returncode", 0) or 0) != 0
        process_tree = list(cancel_inner.get("process_tree") or [])
        prompt_file = dict(cancel_inner.get("prompt_file") or {})
        artifact_snapshot = list(cancel_inner.get("artifact_snapshot") or [])
        artifact_count = int(cancel_inner.get("artifact_count") or 0)
        remote_pid = str(cancel_inner.get("remote_pid") or "")
        cleanup_status = str(cancel_inner.get("cleanup_status") or "")
        await deps.emit_runtime_trace_async(
            event="coding.stop.remote_cancel",
            status="fail" if cancel_fail else "ok",
            level="error" if cancel_fail else "info",
            action_name="cancel_runtime_session",
            remote_pid=remote_pid,
            artifact_count=artifact_count,
            error_code="REMOTE_CANCEL_FAILED" if cancel_fail else "",
            error_message=(
                str(cancel.get("error") or cancel_inner.get("stderr") or "")[:1200]
                if cancel_fail
                else ""
            ),
            debug_bundle=deps.build_debug_bundle(
                failure_class="REMOTE_CANCEL_FAILED" if cancel_fail else "STOP_REQUESTED",
                error_message=str(
                    cancel.get("error") or cancel_inner.get("stderr") or "stop cleanup attempted"
                )[:1200],
                process_tree=process_tree,
                prompt_file=prompt_file,
                artifact_snapshot=artifact_snapshot,
                artifact_count=artifact_count,
                stop_cleanup_status=cleanup_status,
                mitigation_hint=(
                    "Inspect whether the remote wrapper PID and descendants were terminated cleanly."
                ),
            ),
            details={
                "stop_cleanup_status": cleanup_status,
                "process_tree_summary": process_tree[:10],
            },
            **common_trace,
        )
        if cancel_inner.get("orphaned"):
            await deps.emit_runtime_trace_async(
                event="coding.stop.orphan_process.detected",
                status="fail",
                level="error",
                action_name="cancel_runtime_session",
                remote_pid=remote_pid,
                artifact_count=artifact_count,
                error_code="ORPHAN_PROCESS_DETECTED",
                error_message="Remote processes or prompt files remained after stop cleanup.",
                details={
                    "process_tree_summary": process_tree[:10],
                    "prompt_file": prompt_file,
                },
                **common_trace,
            )
    raise RuntimeError("STOP_REQUESTED: session stop requested by user")
