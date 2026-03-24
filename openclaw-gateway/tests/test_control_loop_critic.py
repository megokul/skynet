from __future__ import annotations

import aiosqlite
import pytest

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

