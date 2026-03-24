from __future__ import annotations

import uuid
from typing import Any


def filter_stage_chain_by_policy(
    stage_chain: list[str],
    *,
    claude_ollama_enabled: bool,
) -> tuple[list[str], list[str]]:
    filtered: list[str] = []
    disabled: list[str] = []
    for stage in stage_chain:
        if stage == "claude_ollama" and not claude_ollama_enabled:
            disabled.append(stage)
            continue
        filtered.append(stage)
    return filtered, disabled


def parse_coding_fallback_chain(
    raw: str,
    *,
    valid_stages: set[str],
    default_chain: list[str] | tuple[str, ...],
) -> list[str]:
    seen: set[str] = set()
    parsed: list[str] = []
    for token in str(raw or "").split(","):
        stage = token.strip().lower()
        if stage == "claude":
            stage = "claude_ollama"
        if not stage or stage in seen or stage not in valid_stages:
            continue
        seen.add(stage)
        parsed.append(stage)
    if parsed:
        return parsed
    return list(default_chain)


def build_coding_stage_chain(
    *,
    project: dict[str, Any] | None,
    live_stage_chain: list[str],
    use_control_loop_v1: bool,
    effective_coding_profile: str,
    include_legacy: bool,
    include_policy_disabled: bool,
    use_acp_orchestration: bool,
    coding_fallback_chain: str,
    openclaw_stage_chain: str,
    claude_ollama_enabled: bool,
    valid_stages: set[str],
    default_chain: list[str] | tuple[str, ...],
    coding_profile_codex_primary: str,
    coding_profile_claude_ollama: str,
) -> list[str]:
    if live_stage_chain:
        if include_policy_disabled:
            return list(live_stage_chain)
        filtered_chain, _ = filter_stage_chain_by_policy(
            list(live_stage_chain),
            claude_ollama_enabled=claude_ollama_enabled,
        )
        return filtered_chain
    if use_control_loop_v1:
        raw_chain = parse_coding_fallback_chain(
            coding_fallback_chain,
            valid_stages=valid_stages,
            default_chain=default_chain,
        )
        if include_policy_disabled:
            return raw_chain
        filtered_chain, _ = filter_stage_chain_by_policy(
            raw_chain,
            claude_ollama_enabled=claude_ollama_enabled,
        )
        return filtered_chain

    raw_chain: list[str] = []
    if effective_coding_profile == coding_profile_codex_primary:
        configured_chain = openclaw_stage_chain if use_acp_orchestration else coding_fallback_chain
        raw_chain = parse_coding_fallback_chain(
            configured_chain,
            valid_stages=valid_stages,
            default_chain=default_chain,
        )
    elif effective_coding_profile == coding_profile_claude_ollama:
        raw_chain = ["claude_ollama"]
    elif include_legacy:
        raw_chain = ["claude_ollama"]
    if include_policy_disabled:
        return raw_chain
    filtered_chain, _ = filter_stage_chain_by_policy(
        raw_chain,
        claude_ollama_enabled=claude_ollama_enabled,
    )
    return filtered_chain


def stage_payload(
    *,
    stage_name: str,
    prompt: str,
    working_dir: str,
    timeout_seconds: int = 1800,
    project_id: str = "",
    task_id: str = "",
    graph_id: str = "",
    node_key: str = "",
    node_type: str = "",
    worker_id: str = "",
    session_key: str = "",
    default_worker_id: str = "worker-primary",
    claude_model: str = "",
    claude_auto_pull: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "working_dir": working_dir,
        "timeout_seconds": timeout_seconds,
        "project_id": project_id,
        "task_id": task_id,
        "graph_id": graph_id,
        "node_key": node_key,
        "node_type": node_type,
        "worker_id": worker_id or default_worker_id,
        "session_key": session_key or uuid.uuid4().hex,
    }
    if stage_name == "codex":
        payload["agent"] = "codex"
        payload["backend"] = "auto"
    elif stage_name == "claude_ollama":
        payload["agent"] = "claude"
        payload["backend"] = "ollama"
        payload["model"] = claude_model
        payload["auto_pull_model"] = claude_auto_pull
    elif stage_name == "cline":
        payload["agent"] = "cline"
        payload["backend"] = "auto"
    elif stage_name == "qwen":
        payload["agent"] = "qwen"
        payload["backend"] = "auto"
        payload["task_mode"] = "coding_implementation"
    else:
        raise ValueError(f"Unsupported coding stage: {stage_name}")
    return payload


def planner_agent_payload(
    *,
    agent: str,
    prompt: str,
    working_dir: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent": str(agent or "codex").strip().lower() or "codex",
        "backend": "auto",
        "prompt": prompt,
        "working_dir": working_dir,
        "timeout_seconds": timeout_seconds,
    }
    if payload["agent"] == "qwen":
        payload["task_mode"] = "plan_generation"
    return payload
