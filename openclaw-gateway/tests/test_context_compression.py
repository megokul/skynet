import json

from orchestration.compression import build_context_bundle


def test_context_bundle_is_bounded_and_contains_required_fields():
    bundle = build_context_bundle(
        objective="Build popup + beep script",
        active_node={"node_key": "W-1", "node_type": "work"},
        last_failure={"failure_type": "TEST_FAILED", "message": "1 failed"},
        required_artifacts=["skynet_run.json", "README.md"],
        memory_rows=[
            {"tier": "repo_facts", "memory_key": "lang", "memory_value": {"python": True}},
        ],
        event_rows=[
            {"id": 1, "event_type": "node.start", "status": "running", "node_key": "W-1", "failure_type": ""},
        ],
        findings=[
            {"severity": "high", "code": "T001", "message": "failing test", "files": ["main.py"]},
        ],
        index_hits=[
            {"path": "main.py", "symbol": "run", "line_no": 10},
        ],
        max_chars=600,
    )
    assert len(bundle) <= 600
    payload = json.loads(bundle)
    assert payload["objective"] == "Build popup + beep script"
    assert "skynet_run.json" in payload["required_artifacts"]
    assert payload["active_node"]["node_key"] == "W-1"


def test_context_bundle_truncates_when_needed():
    very_large = "x" * 5000
    bundle = build_context_bundle(
        objective=very_large,
        active_node={"node_key": "N"},
        last_failure=None,
        required_artifacts=[],
        memory_rows=[],
        event_rows=[],
        findings=[],
        index_hits=[],
        max_chars=1200,
    )
    assert len(bundle) <= 1200
