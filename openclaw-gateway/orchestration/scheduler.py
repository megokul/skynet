from __future__ import annotations

from dataclasses import dataclass

from orchestration.graph import LoopNode, critical_path_depth, runnable_nodes


@dataclass
class SchedulerTick:
    runnable: list[LoopNode]
    selected: list[LoopNode]
    deadlock: bool
    reason: str = ""


def _sort_key(node: LoopNode, depths: dict[str, int]) -> tuple[int, int, str]:
    # Higher priority first, then deeper critical path, then FIFO-ish by key.
    return (-int(node.priority), -int(depths.get(node.node_key, 0)), node.node_key)


def pick_batch(
    nodes: list[LoopNode],
    *,
    max_parallel: int,
    deadlock_idle_ticks: int,
    idle_ticks: int,
) -> SchedulerTick:
    runnable = runnable_nodes(nodes)
    if not runnable:
        deadlock = idle_ticks >= max(1, int(deadlock_idle_ticks))
        return SchedulerTick(
            runnable=[],
            selected=[],
            deadlock=deadlock,
            reason="No runnable nodes" if deadlock else "",
        )

    depths = critical_path_depth(nodes)
    ordered = sorted(runnable, key=lambda node: _sort_key(node, depths))

    selected: list[LoopNode] = []
    seen_locks: set[str] = set()
    for node in ordered:
        lock_key = (node.execution_lock or "repo-write").strip().lower()
        if lock_key in seen_locks:
            continue
        selected.append(node)
        seen_locks.add(lock_key)
        if len(selected) >= max(1, int(max_parallel)):
            break

    if not selected and ordered:
        # If all runnable nodes collide on lock, pick one highest ranked to avoid starvation.
        selected = [ordered[0]]

    return SchedulerTick(
        runnable=ordered,
        selected=selected,
        deadlock=False,
    )
