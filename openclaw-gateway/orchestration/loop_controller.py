from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiosqlite

from db.store import (
    create_critic_finding,
    create_task_graph,
    create_task_node,
    increment_task_node_attempt,
    list_graph_nodes,
    update_task_graph_counters,
    update_task_graph_status,
    update_task_node_deps,
    update_task_node_status,
)
from orchestration.failure import (
    FAIL_BUDGET,
    FAIL_CRITIC_PARSE,
    FAIL_DEADLOCK,
    FAIL_ENVIRONMENT,
    FAIL_STRICT_GATE,
    classify_generation_error,
    is_repairable,
)
from orchestration.graph import LoopNode, validate_acyclic
from orchestration.memory import LoopMemory, TIER_TASK_STATE
from orchestration.scheduler import pick_batch
from orchestration.trace import emit_trace_event

WorkExecutor = Callable[[LoopNode], Awaitable[dict[str, Any]]]
CriticExecutor = Callable[[LoopNode], Awaitable[dict[str, Any]]]
GateExecutor = Callable[[LoopNode], Awaitable[dict[str, Any]]]
NodeEventHook = Callable[[LoopNode, str, dict[str, Any]], Awaitable[None]]


class ClosedLoopController:
    def __init__(
        self,
        *,
        db: aiosqlite.Connection,
        project_id: str,
        goal: str,
        milestones: list[str],
        repair_retries: int = 1,
        max_parallel_nodes: int = 1,
        memory_enabled: bool = True,
        max_iterations: int = 40,
        max_runtime_seconds: int = 3600,
        max_repairs: int = 1,
        max_tokens: int = 250000,
        deadlock_idle_ticks: int = 3,
        success_contract: dict[str, Any] | None = None,
        node_specs: list[dict[str, Any]] | None = None,
    ) -> None:
        self._db = db
        self._project_id = project_id
        self._goal = goal
        self._milestones = [m.strip() for m in milestones if str(m).strip()]
        self._repair_retries = max(0, int(repair_retries))
        self._max_parallel_nodes = max(1, int(max_parallel_nodes))
        self._memory = LoopMemory(db, project_id) if memory_enabled else None
        self._max_iterations = max(1, int(max_iterations))
        self._max_runtime_seconds = max(60, int(max_runtime_seconds))
        self._max_repairs = max(0, int(max_repairs))
        self._max_tokens = max(1, int(max_tokens))
        self._deadlock_idle_ticks = max(1, int(deadlock_idle_ticks))
        self._success_contract = success_contract or {}
        self._node_specs = [spec for spec in (node_specs or []) if isinstance(spec, dict)]
        self._graph_id: int | None = None
        self._started_ts = time.time()
        self._idle_ticks = 0

    @property
    def graph_id(self) -> int | None:
        return self._graph_id

    async def bootstrap(self, *, planner_summary: str = "") -> int:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        graph = await create_task_graph(
            self._db,
            project_id=self._project_id,
            goal=self._goal,
            status="active",
            planner_summary=planner_summary,
            max_iterations=self._max_iterations,
            max_runtime_seconds=self._max_runtime_seconds,
            max_repairs=self._max_repairs,
            max_tokens=self._max_tokens,
            success_contract=self._success_contract,
            started_at=now,
        )
        self._graph_id = int(graph["id"])

        work_specs: list[dict[str, Any]] = []
        for idx, spec in enumerate(self._node_specs, start=1):
            node_type = str(spec.get("node_type") or "").strip().lower()
            title = str(spec.get("title") or "").strip()
            if node_type and node_type != "work":
                continue
            if not title:
                continue
            work_key = str(spec.get("node_key") or "").strip() or f"work_{idx}"
            deps = [str(dep).strip() for dep in (spec.get("deps") or []) if str(dep).strip()]
            work_specs.append(
                {
                    "node_key": work_key,
                    "title": title,
                    "deps": deps,
                    "priority": int(spec.get("priority") or 200),
                    "tools_required": list(spec.get("tools_required") or []),
                    "acceptance": list(spec.get("acceptance") or []),
                    "risk": dict(spec.get("risk") or {}),
                    "risk_level": str(spec.get("risk_level") or (spec.get("risk") or {}).get("level") or "medium"),
                    "owner": str(spec.get("owner") or "codex"),
                }
            )
        if not work_specs:
            for index, milestone in enumerate(self._milestones, start=1):
                work_specs.append(
                    {
                        "node_key": f"work_{index}",
                        "title": milestone,
                        "deps": [],
                        "priority": 200,
                        "tools_required": [],
                        "acceptance": [],
                        "risk": {},
                        "owner": "codex",
                    }
                )

        known_work_keys = {str(spec.get("node_key")) for spec in work_specs}
        critic_keys: list[str] = []
        for index, spec in enumerate(work_specs, start=1):
            work_key = str(spec.get("node_key") or f"work_{index}")
            critic_key = f"critic_{index}"
            work_deps = [
                dep for dep in [str(item).strip() for item in (spec.get("deps") or []) if str(item).strip()]
                if dep in known_work_keys
            ]
            milestone_text = str(spec.get("title") or "").strip()
            await create_task_node(
                self._db,
                graph_id=self._graph_id,
                node_key=work_key,
                title=milestone_text or f"Milestone {index}",
                node_type="work",
                owner=str(spec.get("owner") or "codex"),
                worker_id=str(spec.get("worker_id") or "").strip(),
                deps=work_deps,
                inputs={
                    "milestone_text": milestone_text,
                    "index": index,
                    "tools_required": list(spec.get("tools_required") or []),
                    "acceptance": list(spec.get("acceptance") or []),
                    "risk": dict(spec.get("risk") or {}),
                },
                retry_budget=0,
                tools_required=list(spec.get("tools_required") or []),
                risk_level=str(spec.get("risk_level") or (spec.get("risk") or {}).get("level") or "medium"),
                priority=int(spec.get("priority") or 200),
                execution_lock="repo-write",
            )
            await create_task_node(
                self._db,
                graph_id=self._graph_id,
                node_key=critic_key,
                title=f"Critic {index}",
                node_type="critic",
                owner="codex",
                deps=[work_key],
                inputs={"milestone_text": milestone_text, "work_node_key": work_key},
                retry_budget=self._repair_retries,
                tools_required=[],
                risk_level="medium",
                priority=150,
                execution_lock="repo-write",
            )
            critic_keys.append(critic_key)

        await create_task_node(
            self._db,
            graph_id=self._graph_id,
            node_key="gate_final",
            title="Final gate",
            node_type="gate",
            owner="system",
            deps=critic_keys,
            retry_budget=0,
            tools_required=[],
            risk_level="low",
            priority=120,
            execution_lock="repo-write",
        )
        return self._graph_id

    async def run(
        self,
        *,
        execute_work: WorkExecutor,
        execute_critic: CriticExecutor,
        execute_gate: GateExecutor,
        on_node_event: NodeEventHook | None = None,
    ) -> dict[str, Any]:
        if not isinstance(self._graph_id, int):
            raise RuntimeError("ClosedLoopController.bootstrap() must be called before run()")

        async def _emit(node: LoopNode, event: str, payload: dict[str, Any] | None = None) -> None:
            data = payload or {}
            if on_node_event is not None:
                await on_node_event(node, event, data)
            if self._memory is not None:
                await self._memory.put(
                    tier=TIER_TASK_STATE,
                    key=node.node_key,
                    value={
                        "event": event,
                        "node_type": node.node_type,
                        "status": node.status,
                        "attempt_count": node.attempt_count,
                        "payload": data,
                    },
                    source_node_id=node.node_id,
                )
            await emit_trace_event(
                self._db,
                graph_id=int(self._graph_id or 0),
                node_id=node.node_id,
                node_key=node.node_key,
                event_type=event,
                status=str(node.status or ""),
                agent=str(node.owner or ""),
                stage=str(data.get("stage") or ""),
                failure_type=str(data.get("failure_type") or ""),
                details=data,
            )

        async def _fail_graph_from_executor_exception(
            *,
            node: LoopNode,
            exc: Exception,
            failure_type: str,
        ) -> dict[str, Any]:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            error_text = (str(exc).strip() or type(exc).__name__)[:600]
            failed_status = "failed_infra" if failure_type == FAIL_ENVIRONMENT else "failed"
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status=failed_status,
                error_message=error_text,
                failure_type=failure_type,
                finished_at=now,
            )
            node.status = failed_status
            await _emit(
                node,
                failed_status,
                {
                    "error": error_text,
                    "failure_type": failure_type,
                    "source": "executor_exception",
                },
            )
            await update_task_graph_status(
                self._db,
                graph_id=int(self._graph_id or 0),
                status="failed",
                finished_at=now,
            )
            return {
                "status": "failed",
                "graph_id": self._graph_id,
                "error": error_text,
                "failure_type": failure_type,
            }

        while True:
            elapsed = int(max(0, time.time() - self._started_ts))
            if elapsed > self._max_runtime_seconds:
                await update_task_graph_status(
                    self._db,
                    graph_id=int(self._graph_id or 0),
                    status="failed",
                    finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                )
                return {
                    "status": "failed",
                    "graph_id": self._graph_id,
                    "error": FAIL_BUDGET,
                    "failure_type": FAIL_BUDGET,
                }
            graph_row = await self._get_graph_row()
            if graph_row:
                if int(graph_row.get("iteration_count", 0) or 0) >= self._max_iterations:
                    await update_task_graph_status(
                        self._db,
                        graph_id=int(self._graph_id or 0),
                        status="failed",
                        finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                    )
                    return {
                        "status": "failed",
                        "graph_id": self._graph_id,
                        "error": FAIL_BUDGET,
                        "failure_type": FAIL_BUDGET,
                    }
                if int(graph_row.get("tokens_used", 0) or 0) >= self._max_tokens:
                    await update_task_graph_status(
                        self._db,
                        graph_id=int(self._graph_id or 0),
                        status="failed",
                        finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                    )
                    return {
                        "status": "failed",
                        "graph_id": self._graph_id,
                        "error": FAIL_BUDGET,
                        "failure_type": FAIL_BUDGET,
                    }

            node_rows = await list_graph_nodes(self._db, graph_id=int(self._graph_id or 0))
            nodes = [self._row_to_node(row) for row in node_rows]
            validate_acyclic(nodes)

            queued = [node for node in nodes if node.status == "queued"]
            if not queued:
                break

            tick = pick_batch(
                nodes,
                max_parallel=self._max_parallel_nodes,
                deadlock_idle_ticks=self._deadlock_idle_ticks,
                idle_ticks=self._idle_ticks,
            )
            if tick.deadlock:
                await update_task_graph_status(
                    self._db,
                    graph_id=int(self._graph_id or 0),
                    status="failed",
                    finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                )
                await emit_trace_event(
                    self._db,
                    graph_id=int(self._graph_id or 0),
                    node_id=None,
                    node_key="",
                    event_type="deadlock",
                    status="failed",
                    failure_type=FAIL_DEADLOCK,
                    details={"reason": tick.reason, "queued_count": len(queued)},
                )
                return {
                    "status": "failed",
                    "graph_id": self._graph_id,
                    "error": FAIL_DEADLOCK,
                    "failure_type": FAIL_DEADLOCK,
                }

            if not tick.selected:
                self._idle_ticks += 1
                continue
            self._idle_ticks = 0
            await update_task_graph_counters(
                self._db,
                graph_id=int(self._graph_id or 0),
                iteration_delta=1,
            )

            for node in tick.selected:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                await update_task_node_status(
                    self._db,
                    node_id=node.node_id,
                    status="running",
                    started_at=now,
                )
                await increment_task_node_attempt(self._db, node_id=node.node_id)
                node.attempt_count += 1
                node.status = "running"
                await _emit(node, "running", {"stage": node.node_type})

                if node.node_type in {"work", "repair"}:
                    try:
                        result = await execute_work(node)
                    except Exception as exc:
                        failure_type = classify_generation_error(str(exc))
                        return await _fail_graph_from_executor_exception(
                            node=node,
                            exc=exc,
                            failure_type=failure_type,
                        )
                    await self._handle_work_result(node=node, result=result, emit=_emit)
                    await update_task_graph_counters(
                        self._db,
                        graph_id=int(self._graph_id or 0),
                        tokens_delta=int(result.get("tokens_used", 0) or 0),
                    )
                    continue

                if node.node_type == "critic":
                    try:
                        result = await execute_critic(node)
                    except Exception as exc:
                        return await _fail_graph_from_executor_exception(
                            node=node,
                            exc=exc,
                            failure_type=FAIL_CRITIC_PARSE,
                        )
                    handled = await self._handle_critic_result(node=node, result=result, emit=_emit)
                    await update_task_graph_counters(
                        self._db,
                        graph_id=int(self._graph_id or 0),
                        tokens_delta=int(result.get("tokens_used", 0) or 0),
                    )
                    if handled == "repair_created":
                        await update_task_graph_counters(
                            self._db,
                            graph_id=int(self._graph_id or 0),
                            repair_delta=1,
                        )
                    continue

                if node.node_type == "gate":
                    try:
                        result = await execute_gate(node)
                    except Exception as exc:
                        return await _fail_graph_from_executor_exception(
                            node=node,
                            exc=exc,
                            failure_type=FAIL_STRICT_GATE,
                        )
                    await self._handle_gate_result(node=node, result=result, emit=_emit)
                    continue

                await update_task_node_status(
                    self._db,
                    node_id=node.node_id,
                    status="failed",
                    error_message=f"Unknown node_type '{node.node_type}'",
                    failure_type=FAIL_ENVIRONMENT,
                    finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                )
                await _emit(node, "failed", {"reason": "unknown_node_type", "failure_type": FAIL_ENVIRONMENT})

        final_nodes = [self._row_to_node(row) for row in await list_graph_nodes(self._db, graph_id=int(self._graph_id or 0))]
        all_done = all(node.status == "done" for node in final_nodes)
        any_stopped = any(node.status == "stopped" for node in final_nodes)
        graph_status = "completed" if all_done else ("stopped" if any_stopped else "failed")
        await update_task_graph_status(
            self._db,
            graph_id=int(self._graph_id or 0),
            status=graph_status,
            finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        )
        return {"status": graph_status, "graph_id": self._graph_id}

    async def _handle_work_result(
        self,
        *,
        node: LoopNode,
        result: dict[str, Any],
        emit: NodeEventHook,
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        if bool(result.get("stopped")):
            summary = str(result.get("summary") or "stopped by user").strip()
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status="stopped",
                result_summary=summary,
                finished_at=now,
            )
            node.status = "stopped"
            await emit(node, "stopped", {"summary": summary})
            return
        if bool(result.get("skipped")):
            summary = str(result.get("summary") or "skipped by user").strip()
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status="skipped",
                result_summary=summary,
                finished_at=now,
            )
            node.status = "skipped"
            await emit(node, "skipped", {"summary": summary})
            return
        ok = bool(result.get("ok"))
        summary = str(result.get("summary") or "").strip()
        error = str(result.get("error") or "").strip()
        if ok:
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status="done",
                result_summary=summary,
                finished_at=now,
            )
            node.status = "done"
            await emit(node, "done", {"summary": summary})
            return
        failure_type = str(result.get("failure_type") or "").strip().upper()
        if not failure_type:
            failure_type = FAIL_ENVIRONMENT if bool(result.get("infra_failed")) else classify_generation_error(error or summary)
        failed_status = "failed_infra" if failure_type == FAIL_ENVIRONMENT else "failed"
        await update_task_node_status(
            self._db,
            node_id=node.node_id,
            status=failed_status,
            result_summary=summary,
            error_message=error or summary,
            failure_type=failure_type,
            finished_at=now,
        )
        node.status = failed_status
        await emit(
            node,
            failed_status,
            {"summary": summary, "error": error, "failure_type": failure_type},
        )

    async def _handle_critic_result(
        self,
        *,
        node: LoopNode,
        result: dict[str, Any],
        emit: NodeEventHook,
    ) -> str:
        parse_error = bool(result.get("parse_error"))
        findings = result.get("findings", [])
        if not isinstance(findings, list):
            findings = []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            await create_critic_finding(
                self._db,
                node_id=node.node_id,
                critic_name=str(result.get("critic_name") or "review"),
                severity=str(finding.get("severity") or "medium"),
                code=str(finding.get("code") or ""),
                message=str(finding.get("message") or ""),
                files=[str(item) for item in (finding.get("files") or []) if str(item).strip()],
                suggested_fix=str(finding.get("suggested_fix") or ""),
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        if parse_error:
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status="failed",
                error_message=str(result.get("error") or "CRITIC_PARSE_ERROR"),
                failure_type=FAIL_CRITIC_PARSE,
                finished_at=now,
            )
            node.status = "failed"
            await emit(
                node,
                "failed",
                {"error": str(result.get("error") or "CRITIC_PARSE_ERROR"), "failure_type": FAIL_CRITIC_PARSE},
            )
            return "failed"

        blocking = bool(result.get("blocking"))
        if not blocking and bool(result.get("ok", True)):
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status="done",
                result_summary=str(result.get("summary") or "critic passed"),
                finished_at=now,
            )
            node.status = "done"
            await emit(node, "done", {"summary": str(result.get("summary") or "critic passed")})
            return "done"

        graph_row = await self._get_graph_row()
        repair_count = int(graph_row.get("repair_count", 0) or 0) if graph_row else 0
        can_repair = (
            node.attempt_count <= node.retry_budget
            and repair_count < self._max_repairs
            and is_repairable(str(result.get("failure_type") or "STRICT_GATE_FAILED"))
        )
        if can_repair:
            repair_key = f"repair_{node.node_key}_{node.attempt_count}"
            current_deps = list(node.deps)
            await create_task_node(
                self._db,
                graph_id=int(self._graph_id or 0),
                node_key=repair_key,
                title=f"Repair for {node.node_key}",
                node_type="repair",
                owner="codex",
                worker_id=node.worker_id,
                deps=current_deps,
                inputs={
                    "critic_node_key": node.node_key,
                    "findings": findings,
                },
                retry_budget=0,
                tools_required=list(node.tools_required),
                risk_level=node.risk_level,
                priority=max(120, int(node.priority) + 10),
                execution_lock="repo-write",
            )
            current_deps.append(repair_key)
            await update_task_node_deps(self._db, node_id=node.node_id, deps=current_deps)
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status="queued",
                result_summary="Waiting for repair node",
                error_message="",
                failure_type="",
            )
            node.status = "queued"
            await emit(node, "needs_changes", {"repair_node_key": repair_key})
            return "repair_created"

        await update_task_node_status(
            self._db,
            node_id=node.node_id,
            status="failed",
            error_message=str(result.get("summary") or "critic blocked"),
            failure_type=str(result.get("failure_type") or "STRICT_GATE_FAILED"),
            finished_at=now,
        )
        node.status = "failed"
        await emit(
            node,
            "failed",
            {
                "summary": str(result.get("summary") or "critic blocked"),
                "failure_type": str(result.get("failure_type") or "STRICT_GATE_FAILED"),
            },
        )
        return "failed"

    async def _handle_gate_result(
        self,
        *,
        node: LoopNode,
        result: dict[str, Any],
        emit: NodeEventHook,
    ) -> None:
        ok = bool(result.get("ok"))
        summary = str(result.get("summary") or "").strip()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        if ok:
            await update_task_node_status(
                self._db,
                node_id=node.node_id,
                status="done",
                result_summary=summary or "gate passed",
                finished_at=now,
            )
            node.status = "done"
            await emit(node, "done", {"summary": summary or "gate passed"})
            return
        await update_task_node_status(
            self._db,
            node_id=node.node_id,
            status="failed",
            error_message=summary or "gate failed",
            failure_type=str(result.get("failure_type") or "STRICT_GATE_FAILED"),
            finished_at=now,
        )
        node.status = "failed"
        await emit(
            node,
            "failed",
            {
                "summary": summary or "gate failed",
                "failure_type": str(result.get("failure_type") or "STRICT_GATE_FAILED"),
            },
        )

    async def _get_graph_row(self) -> dict[str, Any] | None:
        async with self._db.execute(
            "SELECT * FROM task_graphs WHERE id = ? LIMIT 1",
            (int(self._graph_id or 0),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def _row_to_node(row: dict[str, Any]) -> LoopNode:
        deps_raw = row.get("deps_json") or "[]"
        inputs_raw = row.get("inputs_json") or "{}"
        try:
            deps = json.loads(str(deps_raw))
            if not isinstance(deps, list):
                deps = []
        except Exception:
            deps = []
        tools_raw = row.get("tools_required_json") or "[]"
        try:
            tools_required = json.loads(str(tools_raw))
            if not isinstance(tools_required, list):
                tools_required = []
        except Exception:
            tools_required = []
        try:
            payload = json.loads(str(inputs_raw))
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        return LoopNode(
            node_id=int(row["id"]),
            node_key=str(row.get("node_key") or ""),
            title=str(row.get("title") or ""),
            node_type=str(row.get("node_type") or ""),
            owner=str(row.get("owner") or ""),
            worker_id=str(row.get("worker_id") or ""),
            deps=[str(item).strip() for item in deps if str(item).strip()],
            tools_required=[str(item).strip() for item in tools_required if str(item).strip()],
            risk_level=str(row.get("risk_level") or "medium"),
            priority=int(row.get("priority", 100) or 100),
            execution_lock=str(row.get("execution_lock") or "repo-write"),
            retry_budget=int(row.get("retry_budget", 0) or 0),
            attempt_count=int(row.get("attempt_count", 0) or 0),
            status=str(row.get("status") or "queued"),
            failure_type=str(row.get("failure_type") or ""),
            result_summary=str(row.get("result_summary") or ""),
            error_message=str(row.get("error_message") or ""),
            started_at=str(row.get("started_at") or ""),
            finished_at=str(row.get("finished_at") or ""),
            created_at=str(row.get("created_at") or ""),
            payload=payload,
        )
