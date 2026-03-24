from __future__ import annotations

from typing import Any, Callable


def orchestration_mode(effective_mode: str | None) -> str:
    return str(effective_mode or "legacy").strip().lower()


def use_acp_orchestration(effective_mode: str | None, *, acp_mode_name: str = "acp_first") -> bool:
    return orchestration_mode(effective_mode) == str(acp_mode_name or "acp_first").strip().lower()


def tracker_default_transport(
    *,
    execution_mode: str,
    use_acp: bool,
    websocket_healthy: bool,
) -> str:
    mode = str(execution_mode or "").strip().lower()
    if mode in {"ssh", "ssh_tunnel", "tunnel", "ssh-only"}:
        return "ssh_fallback"
    if use_acp:
        return "acp_local"
    if websocket_healthy:
        return "websocket_primary"
    return "ssh_fallback"


def tracker_default_runtime_mode(*, use_acp: bool, websocket_healthy: bool) -> str:
    if use_acp:
        return "acp"
    if websocket_healthy:
        return "worker_agent"
    return "ssh"


def runtime_transport_label(*, use_acp: bool, default_transport: str) -> str:
    if use_acp:
        return "acp_local"
    return str(default_transport or "ssh_fallback").strip()


def runtime_mode_label(*, use_acp: bool, default_runtime_mode: str) -> str:
    if use_acp:
        return "acp"
    return str(default_runtime_mode or "ssh").strip()


def parse_agent_availability(
    report: str,
    *,
    stage_agent_name: dict[str, str],
    status_line_fn: Callable[[str, str], str],
) -> dict[str, bool]:
    availability: dict[str, bool] = {}
    for stage, agent in stage_agent_name.items():
        line = status_line_fn(report, agent)
        if not line:
            continue
        lowered = line.lower()
        if "unavailable" in lowered:
            availability[stage] = False
        elif "available" in lowered:
            availability[stage] = True
    return availability


def action_inner_result(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("result", result)


def action_exit_code(result: dict[str, Any]) -> int:
    inner = action_inner_result(result)
    return int(inner.get("returncode", inner.get("exit_code", 0)))


def action_error_text(result: dict[str, Any], action: str) -> str:
    if result.get("status") == "error":
        return str(result.get("error") or f"{action} failed").strip()
    inner = action_inner_result(result)
    return str(
        inner.get("stderr")
        or inner.get("stdout")
        or f"{action} failed"
    ).strip()


def action_excerpt(result: dict[str, Any], *, limit: int = 240) -> str:
    inner = action_inner_result(result)
    text = str(inner.get("stderr") or inner.get("stdout") or "").strip()
    if not text:
        text = "(no output)"
    return text[:limit]
