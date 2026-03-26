from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from orchestration.milestone_contracts import normalize_node_specs
from skynet.prompt_library import render_prompt


@dataclass(frozen=True)
class MilestoneExtractionDeps:
    cfg: Any
    logger: Any
    send_action: Callable[..., Awaitable[dict[str, Any]]]
    get_openclaw_runner: Callable[[], Any]
    use_acp_orchestration: Callable[[], bool]
    planner_primary_agent: Callable[[], str]
    planner_worker_agents: Callable[[], set[str]]
    planner_acp_agents: Callable[[], set[str]]
    control_loop_router_fallback_enabled: Callable[[], bool]
    live_e2e_runtime_policy: Callable[[], dict[str, Any]]
    planner_agent_payload: Callable[..., dict[str, Any]]
    action_error_text: Callable[[dict[str, Any], str], str]
    action_exit_code: Callable[[dict[str, Any]], int]
    action_inner_result: Callable[[dict[str, Any]], dict[str, Any]]
    action_excerpt: Callable[[dict[str, Any]], str]
    parse_milestones_fallback: Callable[[str], list[str]]
    parse_planner_task_graph_payload: Callable[[str], dict[str, Any] | None]
    parse_json_string_list: Callable[[str], list[str]]


def _reset_loop_graph_hints(project: dict[str, Any]) -> None:
    project["_loop_success_contract"] = {}
    project["_loop_execution_strategy"] = {}
    project["_loop_parallel_lanes"] = []
    project["_loop_risk_assessment"] = []
    project["_loop_node_specs"] = []


def _apply_structured_milestones(project: dict[str, Any], milestones: list[str]) -> list[str]:
    _reset_loop_graph_hints(project)
    project["_loop_node_specs"] = normalize_node_specs(milestones=milestones)
    return milestones


def _deterministic_fallback(
    *,
    project: dict[str, Any],
    parse_milestones_fallback: Callable[[str], list[str]],
) -> list[str]:
    plan_text = str(project.get("description") or "").strip()
    from_numbered = parse_milestones_fallback(plan_text)
    if from_numbered:
        return from_numbered

    bullets: list[str] = []
    for line in plan_text.splitlines():
        match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if not match:
            continue
        item = match.group(1).strip()
        if not item:
            continue
        lowered = item.lower()
        if lowered in {"none", "n/a"} or lowered.startswith("original user requirements"):
            continue
        bullets.append(item)
    if bullets:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in bullets:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:4]

    project_name = str(project.get("name") or "project").strip() or "project"
    return [
        f"Implement core functionality for {project_name} from approved requirements",
        "Add tests plus skynet_run.json and verify successful run output",
    ]


async def extract_milestones_router(
    *,
    logger,
    router,
    project: dict[str, Any],
    parse_milestones_fallback: Callable[[str], list[str]],
) -> list[str]:
    plan = project.get("description", "")
    if not plan:
        return []

    system = render_prompt("gateway/planning/milestone_extract_system.md")
    messages = [
        {
            "role": "user",
            "content": render_prompt(
                "gateway/planning/milestone_extract_user.md",
                project_name=project["name"],
                plan=plan,
            ),
        }
    ]

    try:
        response = await router.chat(
            messages=messages,
            system=system,
            max_tokens=1024,
            task_type="planning",
        )
        raw = (response.text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        milestones = json.loads(raw)
        if isinstance(milestones, list) and all(isinstance(m, str) for m in milestones):
            return _apply_structured_milestones(
                project,
                [m.strip() for m in milestones if m.strip()],
            )
    except Exception:
        logger.warning("JSON milestone extraction failed; falling back to line parsing")

    fallback = parse_milestones_fallback(plan)
    if fallback:
        return _apply_structured_milestones(project, fallback)

    logger.warning("No milestones found in plan text; generating from project info")
    try:
        gen_system = render_prompt("gateway/planning/milestone_generate_system.md")
        gen_messages = [
            {
                "role": "user",
                "content": render_prompt(
                    "gateway/planning/milestone_generate_user.md",
                    project_name=project["name"],
                    project_type=project.get("project_type", "Other"),
                    plan_excerpt=plan[:500],
                ),
            }
        ]
        response = await router.chat(
            messages=gen_messages,
            system=gen_system,
            max_tokens=512,
            task_type="planning",
        )
        raw = (response.text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        milestones = json.loads(raw)
        if isinstance(milestones, list) and all(isinstance(m, str) for m in milestones):
            return _apply_structured_milestones(
                project,
                [m.strip() for m in milestones if m.strip()],
            )
    except Exception:
        logger.warning("Last-resort milestone generation also failed")

    return []


async def extract_milestones_codex_then_router(
    *,
    deps: MilestoneExtractionDeps,
    router,
    project: dict[str, Any],
    working_dir: str,
) -> list[str]:
    planner_agent = deps.planner_primary_agent()
    allow_router_fallback = deps.control_loop_router_fallback_enabled()
    if planner_agent not in deps.planner_worker_agents():
        if deps.live_e2e_runtime_policy():
            raise RuntimeError(
                "Milestone planner fallback is disabled and the primary agent "
                f"'{planner_agent}' is not eligible."
            )
        if allow_router_fallback:
            return await extract_milestones_router(
                logger=deps.logger,
                router=router,
                project=project,
                parse_milestones_fallback=deps.parse_milestones_fallback,
            )
        fallback = _deterministic_fallback(
            project=project,
            parse_milestones_fallback=deps.parse_milestones_fallback,
        )
        return _apply_structured_milestones(project, fallback)

    plan = project.get("description", "")
    if not plan:
        return []

    direct_milestones = deps.parse_milestones_fallback(str(plan))
    if direct_milestones:
        return _apply_structured_milestones(project, direct_milestones)

    prompt = render_prompt(
        "gateway/planning/milestone_graph.md",
        project_name=project["name"],
        plan=plan,
    )
    timeout = max(30, int(getattr(deps.cfg, "MILESTONE_CODEX_TIMEOUT_SECONDS", 120) or 120))

    try:
        if deps.use_acp_orchestration() and planner_agent in deps.planner_acp_agents():
            runner = deps.get_openclaw_runner()
            session = await runner.start_session(
                phase="milestone_extraction",
                project_id=str(project.get("id") or ""),
                task_id=None,
                stage=planner_agent,
                runtime=str(getattr(deps.cfg, "OPENCLAW_RUNTIME", "acp") or "acp"),
                queue_mode="soft",
            )
            run_result = await runner.run_prompt(
                session_id=str(session.get("session_id") or ""),
                prompt=prompt,
                timeout_seconds=timeout,
                stage=planner_agent,
                backend="native",
            )
            if int(run_result.get("returncode", 1) or 1) != 0:
                raise RuntimeError(
                    str(run_result.get("stderr") or run_result.get("stdout") or f"{planner_agent} failed")
                )
            output = str(run_result.get("stdout") or "").strip()
        else:
            await deps.send_action(
                "create_directory",
                {"directory": working_dir},
                timeout=20,
                confirmed=True,
            )
            result = await deps.send_action(
                "run_coding_agent",
                deps.planner_agent_payload(
                    agent=planner_agent,
                    prompt=prompt,
                    working_dir=working_dir,
                    timeout_seconds=timeout,
                ),
                timeout=timeout,
                confirmed=True,
            )
            if result.get("status") == "error":
                raise RuntimeError(deps.action_error_text(result, "run_coding_agent"))
            if deps.action_exit_code(result) != 0:
                raise RuntimeError(deps.action_excerpt(result))
            output = str(deps.action_inner_result(result).get("stdout") or "").strip()

        parsed_graph = deps.parse_planner_task_graph_payload(output)
        if parsed_graph:
            project["_loop_success_contract"] = parsed_graph.get("success_contract") or {}
            project["_loop_execution_strategy"] = parsed_graph.get("execution_strategy") or {}
            project["_loop_parallel_lanes"] = parsed_graph.get("parallel_lanes") or []
            project["_loop_risk_assessment"] = parsed_graph.get("risk_assessment") or []
            project["_loop_node_specs"] = parsed_graph.get("nodes") or []
            return [str(item).strip() for item in (parsed_graph.get("milestones") or []) if str(item).strip()]
        parsed_list = deps.parse_json_string_list(output)
        if parsed_list:
            return _apply_structured_milestones(project, parsed_list)
        fallback = _deterministic_fallback(
            project=project,
            parse_milestones_fallback=deps.parse_milestones_fallback,
        )
        _apply_structured_milestones(project, fallback)
        deps.logger.warning(
            "milestone.primary.codex_invalid_json project_id=%s fallback_count=%s",
            project.get("id"),
            len(fallback),
        )
        return fallback
    except Exception as exc:
        deps.logger.warning(
            "milestone.primary.failover project_id=%s stage=%s error=%s",
            project.get("id"),
            planner_agent,
            str(exc)[:220],
        )
        fallback = _deterministic_fallback(
            project=project,
            parse_milestones_fallback=deps.parse_milestones_fallback,
        )
        if fallback:
            _apply_structured_milestones(project, fallback)
            deps.logger.warning(
                "milestone.primary.local_fallback project_id=%s fallback_count=%s",
                project.get("id"),
                len(fallback),
            )
            return fallback
        if allow_router_fallback:
            return await extract_milestones_router(
                logger=deps.logger,
                router=router,
                project=project,
                parse_milestones_fallback=deps.parse_milestones_fallback,
            )
        raise


async def extract_milestones_with_heartbeat(
    *,
    cfg,
    extract_milestones: Callable[..., Awaitable[list[str]]],
    router,
    project: dict[str, Any],
    working_dir: str,
    app,
    chat_id: int,
    stop_request_cache_key: str,
    heartbeat_hook: Callable[[int], Awaitable[None]] | None = None,
) -> list[str]:
    heartbeat = max(1, int(getattr(cfg, "MILESTONE_EXTRACTION_HEARTBEAT_SECONDS", 20) or 20))
    max_wait = max(heartbeat, int(getattr(cfg, "MILESTONE_EXTRACTION_MAX_WAIT_SECONDS", 180) or 180))
    pending = asyncio.create_task(
        extract_milestones(
            router,
            project,
            working_dir=working_dir,
        )
    )
    elapsed = 0
    try:
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(pending), timeout=heartbeat)
            except asyncio.TimeoutError:
                elapsed += heartbeat
                if app.bot_data.get(stop_request_cache_key):
                    raise RuntimeError("STOP_REQUESTED: session stop requested by user")
                if elapsed >= max_wait:
                    raise RuntimeError(f"MILESTONE_EXTRACTION_TIMEOUT: exceeded {max_wait}s")
                await app.bot.send_message(
                    chat_id,
                    f"\u23f3 Still breaking the plan into milestones ({elapsed}s elapsed)...",
                )
                if heartbeat_hook is not None:
                    await heartbeat_hook(elapsed)
    finally:
        if not pending.done():
            pending.cancel()
