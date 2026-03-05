from __future__ import annotations

import pytest

from orchestration.graph import LoopNode, runnable_nodes, validate_acyclic


def test_validate_acyclic_accepts_linear_graph():
    nodes = [
        LoopNode(node_id=1, node_key="work_1", title="w1", node_type="work", owner="codex"),
        LoopNode(node_id=2, node_key="critic_1", title="c1", node_type="critic", owner="codex", deps=["work_1"]),
        LoopNode(node_id=3, node_key="gate_final", title="g", node_type="gate", owner="system", deps=["critic_1"]),
    ]
    validate_acyclic(nodes)


def test_validate_acyclic_rejects_cycle():
    nodes = [
        LoopNode(node_id=1, node_key="a", title="a", node_type="work", owner="codex", deps=["b"]),
        LoopNode(node_id=2, node_key="b", title="b", node_type="critic", owner="codex", deps=["a"]),
    ]
    with pytest.raises(ValueError):
        validate_acyclic(nodes)


def test_runnable_nodes_honor_dependencies():
    nodes = [
        LoopNode(node_id=1, node_key="work_1", title="w1", node_type="work", owner="codex", status="done"),
        LoopNode(node_id=2, node_key="critic_1", title="c1", node_type="critic", owner="codex", deps=["work_1"]),
        LoopNode(node_id=3, node_key="work_2", title="w2", node_type="work", owner="codex", deps=["critic_1"]),
    ]
    runnable = runnable_nodes(nodes)
    assert [node.node_key for node in runnable] == ["critic_1"]

