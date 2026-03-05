import pytest

from db.schema import init_db
from db.store import create_task_graph
from orchestration.trace import emit_trace_event, format_timeline_lines, load_trace_timeline


@pytest.mark.asyncio
async def test_trace_emit_and_load_timeline():
    db = await init_db(":memory:")
    try:
        graph = await create_task_graph(
            db,
            project_id="p1",
            goal="test",
            status="running",
            planner_summary="",
        )
        await emit_trace_event(
            db,
            graph_id=int(graph["id"]),
            node_id=None,
            node_key="W-1",
            event_type="node.start",
            status="running",
            failure_type="",
            details={"stage": "codex"},
        )
        await emit_trace_event(
            db,
            graph_id=int(graph["id"]),
            node_id=None,
            node_key="W-1",
            event_type="node.fail",
            status="failed",
            failure_type="TEST_FAILED",
            details={"message": "pytest failed"},
        )
        rows = await load_trace_timeline(db, graph_id=int(graph["id"]), limit=10)
        assert len(rows) == 2
        lines = format_timeline_lines(rows)
        assert any("node.start" in line for line in lines)
        assert any("TEST_FAILED" in line for line in lines)
    finally:
        await db.close()
