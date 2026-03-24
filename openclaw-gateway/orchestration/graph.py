from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TERMINAL_STATES = {"done", "failed", "failed_infra", "stopped", "skipped"}


@dataclass
class LoopNode:
    node_id: int
    node_key: str
    title: str
    node_type: str
    owner: str
    worker_id: str = ""
    deps: list[str] = field(default_factory=list)
    tools_required: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    priority: int = 100
    execution_lock: str = "repo-write"
    retry_budget: int = 0
    attempt_count: int = 0
    status: str = "queued"
    failure_type: str = ""
    result_summary: str = ""
    error_message: str = ""
    started_at: str = ""
    finished_at: str = ""
    created_at: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES


def validate_acyclic(nodes: list[LoopNode]) -> None:
    node_map = {node.node_key: node for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def _walk(key: str) -> None:
        if key in visited:
            return
        if key in visiting:
            raise ValueError(f"Cycle detected at node '{key}'")
        visiting.add(key)
        node = node_map.get(key)
        if node is not None:
            for dep in node.deps:
                if dep in node_map:
                    _walk(dep)
        visiting.remove(key)
        visited.add(key)

    for key in node_map:
        _walk(key)


def runnable_nodes(nodes: list[LoopNode]) -> list[LoopNode]:
    node_map = {node.node_key: node for node in nodes}
    out: list[LoopNode] = []
    for node in nodes:
        if node.status != "queued":
            continue
        deps_ready = True
        for dep in node.deps:
            dep_node = node_map.get(dep)
            if dep_node is None:
                continue
            if dep_node.status not in ("done", "skipped"):
                deps_ready = False
                break
        if deps_ready:
            out.append(node)
    return out


def critical_path_depth(nodes: list[LoopNode]) -> dict[str, int]:
    node_map = {node.node_key: node for node in nodes}
    memo: dict[str, int] = {}

    def _depth(key: str) -> int:
        if key in memo:
            return memo[key]
        node = node_map.get(key)
        if node is None:
            memo[key] = 0
            return 0
        max_dep = 0
        for dep in node.deps:
            dep_depth = _depth(dep)
            if dep_depth > max_dep:
                max_dep = dep_depth
        memo[key] = max_dep + 1
        return memo[key]

    for key in node_map:
        _depth(key)
    return memo
