from orchestration.graph import LoopNode
from orchestration.scheduler import pick_batch


def _node(
    key: str,
    *,
    deps: list[str] | None = None,
    priority: int = 100,
    lock: str = "repo-write",
    status: str = "queued",
) -> LoopNode:
    return LoopNode(
        node_id=1,
        node_key=key,
        title=key,
        node_type="work",
        owner="codex",
        deps=list(deps or []),
        priority=priority,
        execution_lock=lock,
        status=status,
    )


def test_scheduler_respects_priority_and_locks():
    nodes = [
        _node("A", priority=300, lock="repo-write"),
        _node("B", priority=200, lock="repo-write"),
        _node("C", priority=100, lock="docker"),
    ]
    tick = pick_batch(nodes, max_parallel=2, deadlock_idle_ticks=3, idle_ticks=0)
    assert [n.node_key for n in tick.selected] == ["A", "C"]


def test_scheduler_honors_dependencies():
    nodes = [
        _node("A", status="done"),
        _node("B", deps=["A"], priority=200, lock="repo-write"),
        _node("C", deps=["B"], priority=300, lock="docker"),
    ]
    tick = pick_batch(nodes, max_parallel=2, deadlock_idle_ticks=3, idle_ticks=0)
    assert [n.node_key for n in tick.selected] == ["B"]


def test_scheduler_deadlock_after_idle_ticks():
    nodes = [
        _node("A", deps=["B"], priority=100),
        _node("B", deps=["A"], priority=100),
    ]
    tick = pick_batch(nodes, max_parallel=2, deadlock_idle_ticks=2, idle_ticks=2)
    assert tick.deadlock is True
    assert "No runnable nodes" in tick.reason
