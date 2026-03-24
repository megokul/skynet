from __future__ import annotations

from typing import Any, Callable

from aiohttp import web


async def handle_status_route(
    request: web.Request,
    *,
    cfg,
    qwen_probe_status: dict[str, dict[str, str]],
    ssh_only_modes: set[str],
    get_ssh_executor: Callable[[], Any],
    get_agent_status: Callable[[], dict[str, Any]],
    is_agent_connected: Callable[[], bool],
    get_telegram_poller_status: Callable[[], dict[str, Any]],
) -> web.Response:
    ssh_exec = get_ssh_executor()
    ssh_ok, ssh_detail = await ssh_exec.health_check()
    ssh_configured = ssh_exec.is_configured()
    mode = cfg.get_str("OPENCLAW_EXECUTION_MODE", "").strip().lower()
    force_ssh = mode in ssh_only_modes
    diagnostics = ssh_exec.get_diagnostics()
    agent_status = get_agent_status()
    poller_status = get_telegram_poller_status()
    live_e2e_policy = cfg.get_live_e2e_policy()
    qwen_policy = cfg.get_qwen_execution_policy()
    qwen_provider = dict(qwen_policy.get("provider") or {})
    execution_mode_effective = "ssh_tunnel" if force_ssh else "agent_preferred"
    websocket_health_ok = bool(agent_status.get("websocket_health_ok", False))
    if force_ssh:
        primary_transport_mode = "ssh_fallback"
    elif websocket_health_ok:
        primary_transport_mode = "websocket_primary"
    elif ssh_configured:
        primary_transport_mode = "ssh_fallback"
    else:
        primary_transport_mode = "unavailable"
    return web.json_response(
        {
            "primary_transport_mode": primary_transport_mode,
            "agent_connected": is_agent_connected(),
            "worker_id": str(agent_status.get("worker_id") or ""),
            "agent_last_hello_at": str(agent_status.get("agent_last_hello_at") or ""),
            "agent_last_heartbeat_at": str(agent_status.get("agent_last_heartbeat_at") or ""),
            "websocket_health_ok": websocket_health_ok,
            "websocket_error_category": str(agent_status.get("websocket_error_category") or ""),
            "websocket_failure_streak": int(agent_status.get("websocket_failure_streak", 0) or 0),
            "fallback_ready": bool(ssh_configured and ssh_ok),
            "fallback_last_reason": str(agent_status.get("fallback_last_reason") or ""),
            "websocket_log_mirror_enabled": bool(agent_status.get("websocket_log_mirror_enabled", False)),
            "websocket_log_mirror_loop_bound": bool(agent_status.get("websocket_log_mirror_loop_bound", False)),
            "websocket_log_mirror_last_send_at": str(agent_status.get("websocket_log_mirror_last_send_at") or ""),
            "websocket_log_mirror_last_ack_at": str(agent_status.get("websocket_log_mirror_last_ack_at") or ""),
            "websocket_log_mirror_last_error": str(agent_status.get("websocket_log_mirror_last_error") or ""),
            "ssh_fallback_enabled": ssh_configured,
            "ssh_fallback_healthy": ssh_ok,
            "ssh_fallback_target": ssh_detail,
            "execution_mode": execution_mode_effective,
            "execution_mode_effective": execution_mode_effective,
            "ssh_health_ok": bool(diagnostics.get("ssh_health_ok", ssh_ok)),
            "ssh_error_category": str(diagnostics.get("ssh_error_category", "")),
            "ssh_failure_streak": int(diagnostics.get("ssh_failure_streak", 0)),
            "ssh_circuit_open_until": int(diagnostics.get("ssh_circuit_open_until", 0)),
            "ssh_endpoint": str(diagnostics.get("ssh_endpoint", f"{ssh_exec.host}:{ssh_exec.port}")),
            "worker_capabilities": list(agent_status.get("worker_capabilities") or []),
            "coding_agents": dict(agent_status.get("coding_agents") or {}),
            "build_revision": str(getattr(cfg, "BUILD_REVISION", "") or ""),
            "qwen_auth_type": str(qwen_policy.get("auth_type") or ""),
            "qwen_provider_profile": str(qwen_provider.get("profile_name") or ""),
            "qwen_planner_model": str(dict(qwen_policy.get("planner") or {}).get("model") or ""),
            "qwen_coding_model": str(dict(qwen_policy.get("coding") or {}).get("model") or ""),
            "qwen_planner_capability_required": bool(qwen_provider.get("planner_capability_required", True)),
            "qwen_coding_capability_required": bool(qwen_provider.get("coding_capability_required", True)),
            "qwen_planner_ready_probe": dict(qwen_probe_status.get("planner_ready") or {}),
            "qwen_plan_generation_probe": dict(qwen_probe_status.get("plan_generation") or {}),
            "live_e2e_active": bool(live_e2e_policy.get("active", False)),
            "live_e2e_flow": str(live_e2e_policy.get("flow") or ""),
            "live_e2e_allow_fallback": bool(live_e2e_policy.get("allow_fallback", False)),
            "live_e2e_effective_coding_stage_chain": list(live_e2e_policy.get("effective_coding_stage_chain") or []),
            **poller_status,
        }
    )
