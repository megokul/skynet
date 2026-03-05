from orchestration.completion import validate_completion_contract


def test_completion_contract_blocks_missing_nodes():
    ok, reason = validate_completion_contract(
        contract={"required_nodes": ["W-1", "C-1"]},
        node_rows=[{"node_key": "W-1", "status": "done"}],
        has_valid_run_contract=True,
        blocking_findings_count=0,
    )
    assert ok is False
    assert "Missing required nodes" in reason


def test_completion_contract_blocks_missing_manifest():
    ok, reason = validate_completion_contract(
        contract={"required_artifacts": ["skynet_run.json"]},
        node_rows=[],
        has_valid_run_contract=False,
        blocking_findings_count=0,
    )
    assert ok is False
    assert "skynet_run.json" in reason


def test_completion_contract_blocks_findings():
    ok, reason = validate_completion_contract(
        contract={},
        node_rows=[],
        has_valid_run_contract=True,
        blocking_findings_count=2,
    )
    assert ok is False
    assert "Blocking critic findings remain" in reason


def test_completion_contract_passes_when_all_conditions_met():
    ok, reason = validate_completion_contract(
        contract={
            "required_nodes": ["W-1", "C-1"],
            "required_artifacts": ["skynet_run.json"],
        },
        node_rows=[
            {"node_key": "W-1", "status": "done"},
            {"node_key": "C-1", "status": "done"},
        ],
        has_valid_run_contract=True,
        blocking_findings_count=0,
    )
    assert ok is True
    assert reason == "completion contract satisfied"
