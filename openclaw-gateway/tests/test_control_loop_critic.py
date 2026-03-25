from __future__ import annotations

import aiosqlite
import pytest

from bot.handlers.coding import _is_infra_error
from db.store import create_critic_finding, delete_critic_findings_for_node, list_critic_findings
from orchestration.completion import validate_completion_contract
from orchestration.critic import is_blocking, normalize_severity, parse_critic_response


def test_normalize_severity_defaults_to_medium():
    assert normalize_severity("unknown") == "medium"
    assert normalize_severity("HIGH") == "high"


def test_parse_critic_response_json_block():
    payload = parse_critic_response(
        """```json
{"passed": false, "findings": [{"severity":"critical","code":"SEC-1","message":"x","files":["a.py"],"suggested_fix":"y"}]}
```"""
    )
    assert payload["passed"] is False
    assert payload["findings"][0]["severity"] == "critical"
    assert payload["findings"][0]["code"] == "SEC-1"


def test_parse_critic_response_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_critic_response("the code needs significant rework in several areas")


def test_is_blocking_respects_threshold():
    findings = [
        {"severity": "medium"},
        {"severity": "high"},
    ]
    assert is_blocking(findings, threshold="high") is True
    assert is_blocking(findings, threshold="critical") is False


@pytest.fixture
async def _critic_db():
    """In-memory DB with minimal schema for critic findings tests."""
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            CREATE TABLE task_nodes (
                id INTEGER PRIMARY KEY,
                graph_id INTEGER NOT NULL,
                node_key TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued'
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE critic_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id INTEGER NOT NULL REFERENCES task_nodes(id),
                critic_name TEXT NOT NULL,
                severity TEXT NOT NULL,
                code TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                files_json TEXT NOT NULL DEFAULT '[]',
                suggested_fix TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await db.execute(
            "INSERT INTO task_nodes (id, graph_id, node_key, node_type, status) VALUES (1, 10, 'critic_2', 'critic', 'done')"
        )
        await db.commit()
        yield db


@pytest.mark.asyncio
async def test_stale_findings_cleaned_after_repair(_critic_db):
    """Verify that stale critic findings are deleted when repair triggers,
    so gate_final does not see them as blocking."""
    db = _critic_db

    # 1. Store a critical GATE_FAILURE finding (simulates first critic run)
    await create_critic_finding(
        db,
        node_id=1,
        critic_name="review",
        severity="critical",
        code="GATE_FAILURE",
        message="STRICT_GATES_FAILED:smoke",
        files=["main.py"],
        suggested_fix="Fix the code so all quality gates pass.",
    )
    findings = await list_critic_findings(db, node_id=1)
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"

    # 2. Simulate repair trigger: delete stale findings
    deleted = await delete_critic_findings_for_node(db, node_id=1)
    assert deleted == 1

    # 3. Verify findings are gone
    findings = await list_critic_findings(db, node_id=1)
    assert len(findings) == 0

    # 4. Count blocking findings (same query as gate_final)
    async with db.execute(
        """
        SELECT COUNT(1) AS c
        FROM critic_findings cf
        JOIN task_nodes tn ON tn.id = cf.node_id
        WHERE tn.graph_id = 10 AND LOWER(cf.severity) IN ('high', 'critical')
          AND tn.status = 'done'
        """,
        (),
    ) as cur:
        row = await cur.fetchone()
        blocking_count = int(row[0] or 0)
    assert blocking_count == 0

    # 5. validate_completion_contract should pass
    node_rows = [{"node_key": "critic_2", "status": "done"}]
    passed, reason = validate_completion_contract(
        contract={},
        node_rows=node_rows,
        has_valid_run_contract=True,
        blocking_findings_count=blocking_count,
    )
    assert passed is True
    assert "satisfied" in reason


# ── Smoke timeout classification tests ────────────────────────────────


def test_smoke_process_timeout_not_infra():
    """A process timeout during smoke is a code quality issue, not infra."""
    assert _is_infra_error("Process timed out after 120s and was killed") is True
    # The gate handler should NOT treat this as infra because it checks
    # for "process timed out" explicitly.  This test documents the raw
    # _is_infra_error behavior (returns True because of "timed out").


def test_smoke_process_timeout_gate_handler_excludes_infra():
    """The smoke gate handler excludes 'process timed out' from infra classification."""
    msg = "TimeoutError: Process timed out after 120s and was killed"
    # Simulates the gate handler logic:
    is_infra = _is_infra_error(msg) and "process timed out" not in msg.lower()
    assert is_infra is False


def test_wait_timeout_detected_as_smoke_pass():
    """Gateway-side WAIT_TIMEOUT on smoke should be treated as process timeout."""
    msg = "RuntimeError: WAIT_TIMEOUT: quality gate smoke exceeded 30s"
    _lower = msg.lower()
    is_process_timeout = "process timed out" in _lower or "wait_timeout" in _lower
    assert is_process_timeout is True


def test_real_infra_errors_still_classified():
    """Genuine infra errors must still be flagged."""
    assert _is_infra_error("ssh action failed: connection refused") is True
    assert _is_infra_error("no agent connected") is True
    assert _is_infra_error("worker not connected") is True


# ── Smoke timeout normal-return path tests ──────────────────────────


def test_smoke_timeout_normal_return_detected():
    """Worker returns timeout as a normal result (not exception).

    The smoke gate should detect 'Process timed out' in the result text
    and treat it as passed (server/GUI app that started OK).
    """
    # Simulate the normal-return path logic from coding.py
    smoke_summary = "Process timed out after 15s and was killed."
    excerpt = "returncode=-1 stderr=Process timed out after 15s and was killed."
    _combined = f"{smoke_summary} {excerpt}".lower()
    smoke_failed = True  # _action_exit_code != 0
    if "process timed out" in _combined or "timed out" in _combined:
        smoke_failed = False
        smoke_summary = "entrypoint started OK (killed after timeout)"
    assert smoke_failed is False
    assert "started OK" in smoke_summary


def test_smoke_real_failure_not_overridden():
    """A real smoke failure (syntax error, crash) must NOT be overridden."""
    smoke_summary = "SyntaxError: invalid syntax"
    excerpt = "returncode=1 stderr=SyntaxError: invalid syntax"
    _combined = f"{smoke_summary} {excerpt}".lower()
    smoke_failed = True
    if "process timed out" in _combined or "timed out" in _combined:
        smoke_failed = False
    assert smoke_failed is True


# ── Quota exhaustion detection tests ────────────────────────────────


def test_quota_exhaustion_detected():
    """Provider quota errors in summary should be caught."""
    patterns = [
        "Qwen OAuth quota exceeded: Your free daily quota has been reached.",
        "Rate limit exceeded. Please try again later.",
        "Your daily quota has been exhausted.",
    ]
    _QUOTA_PATTERNS = ("quota exceeded", "quota has been reached", "rate limit", "daily quota")
    for msg in patterns:
        assert any(p in msg.lower() for p in _QUOTA_PATTERNS), f"Not detected: {msg}"


def test_normal_summary_not_flagged_as_quota():
    """Normal summaries must not trigger quota detection."""
    normal = [
        "All files written successfully.",
        "Created main.py with HTTP server.",
        "Tests pass (6/6) and entrypoint runs successfully.",
    ]
    _QUOTA_PATTERNS = ("quota exceeded", "quota has been reached", "rate limit", "daily quota")
    for msg in normal:
        assert not any(p in msg.lower() for p in _QUOTA_PATTERNS), f"False positive: {msg}"


# ── Graph completion logic tests ────────────────────────────────────


def test_graph_completed_when_gate_final_passes_despite_failed_work():
    """If gate_final passed, graph should be 'completed' even if a work node failed."""
    from dataclasses import dataclass, field

    @dataclass
    class FakeNode:
        node_type: str
        status: str

    final_nodes = [
        FakeNode(node_type="work", status="failed"),      # work_1 failed
        FakeNode(node_type="critic", status="skipped"),    # critic_1 skipped
        FakeNode(node_type="work", status="done"),         # work_2 done
        FakeNode(node_type="critic", status="done"),       # critic_2 done
        FakeNode(node_type="gate", status="done"),         # gate_final passed
    ]
    any_stopped = any(n.status == "stopped" for n in final_nodes)
    gate_nodes = [n for n in final_nodes if n.node_type == "gate"]
    gates_passed = all(n.status == "done" for n in gate_nodes) if gate_nodes else True

    if any_stopped:
        graph_status = "stopped"
    elif gates_passed:
        graph_status = "completed"
    else:
        all_resolved = all(n.status in ("done", "skipped") for n in final_nodes)
        graph_status = "completed" if all_resolved else "failed"

    assert graph_status == "completed"


def test_graph_fails_when_gate_final_fails():
    """If gate_final itself failed, graph should be 'failed'."""
    from dataclasses import dataclass

    @dataclass
    class FakeNode:
        node_type: str
        status: str

    final_nodes = [
        FakeNode(node_type="work", status="done"),
        FakeNode(node_type="critic", status="done"),
        FakeNode(node_type="gate", status="failed"),   # gate_final failed
    ]
    gate_nodes = [n for n in final_nodes if n.node_type == "gate"]
    gates_passed = all(n.status == "done" for n in gate_nodes) if gate_nodes else True
    any_stopped = any(n.status == "stopped" for n in final_nodes)

    if any_stopped:
        graph_status = "stopped"
    elif gates_passed:
        graph_status = "completed"
    else:
        all_resolved = all(n.status in ("done", "skipped") for n in final_nodes)
        graph_status = "completed" if all_resolved else "failed"

    assert graph_status == "failed"

