from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from orchestration.milestone_contracts import (
    build_completion_contract,
    evaluate_milestone_satisfaction,
    normalize_milestone_spec,
    normalize_node_specs,
    parse_planner_task_graph_payload,
)


def test_normalize_node_specs_synthesizes_structured_checks_from_plain_milestones() -> None:
    node_specs = normalize_node_specs(
        milestones=[
            "Implement the main app",
            "Add tests for the core flow",
            "Add skynet_run.json for the real entrypoint",
            "Add README.md with Windows run instructions",
        ]
    )

    assert [spec["node_key"] for spec in node_specs] == ["work_1", "work_2", "work_3", "work_4"]
    assert all(spec["required_for_completion"] is True for spec in node_specs)
    assert {"type": "tests_pass"} in node_specs[1]["satisfaction_checks"]
    assert {"type": "run_contract_valid"} in node_specs[2]["satisfaction_checks"]
    assert {"type": "readme_instructions", "path": "README.md"} in node_specs[3]["satisfaction_checks"]


def test_parse_planner_task_graph_payload_preserves_structured_contract_fields() -> None:
    payload = {
        "nodes": [
            {
                "node_key": "work_1",
                "title": "Implement app",
                "node_type": "work",
                "deliverables": ["app.py"],
                "acceptance": ["App runs"],
                "required_for_completion": True,
                "satisfaction_checks": [{"type": "required_path", "path": "app.py"}],
            },
            {
                "node_key": "work_2",
                "title": "Optional polish",
                "node_type": "work",
                "required_for_completion": False,
                "satisfaction_checks": [{"type": "required_path", "path": "theme.css"}],
            },
        ],
        "success_contract": {"required_artifacts": ["skynet_run.json"]},
    }

    parsed = parse_planner_task_graph_payload(json.dumps(payload))

    assert parsed is not None
    assert parsed["nodes"][0]["deliverables"] == ["app.py"]
    assert parsed["nodes"][0]["required_for_completion"] is True
    assert parsed["nodes"][1]["required_for_completion"] is False
    assert parsed["success_contract"] == {"required_artifacts": ["skynet_run.json"]}


def test_build_completion_contract_requires_all_non_optional_work_nodes() -> None:
    node_specs = [
        {
            "node_key": "work_1",
            "title": "Core app",
            "required_for_completion": True,
        },
        {
            "node_key": "work_2",
            "title": "Optional polish",
            "required_for_completion": False,
        },
    ]

    contract = build_completion_contract(
        base_contract={"required_nodes": ["critic_1"]},
        node_specs=node_specs,
        require_run_contract=True,
    )

    assert set(contract["required_nodes"]) == {"critic_1", "work_1"}
    assert "work_2" not in contract["required_nodes"]
    assert contract["required_artifacts"] == ["skynet_run.json"]


@pytest.mark.asyncio
async def test_later_readme_milestone_can_be_marked_pre_satisfied_without_provider_call() -> None:
    spec = normalize_milestone_spec(
        {
            "node_key": "work_4",
            "title": "Add README.md with Windows execution instructions",
            "deliverables": ["README.md"],
            "acceptance": ["README documents how to run demo.py on Windows"],
            "satisfaction_checks": [{"type": "readme_instructions", "path": "README.md"}],
        },
        index=4,
    )
    assert spec is not None

    provider_call = AsyncMock()

    async def _list_files(_working_dir: str) -> list[str]:
        return ["demo.py", "tests/test_smoke.py", "skynet_run.json", "README.md"]

    async def _read_file(path: str) -> str:
        if path == "README.md":
            return "Run on Windows with: python demo.py"
        return ""

    async def _validate_run_contract(_working_dir: str) -> dict[str, str] | None:
        return {"interpreter": "python", "entrypoint": "demo.py"}

    async def _run_tests(_working_dir: str) -> tuple[bool, str]:
        return True, "3 passed"

    result = await evaluate_milestone_satisfaction(
        spec,
        working_dir="E:/SKYNET-SANDBOX/Projects/demo",
        list_files=_list_files,
        read_file=_read_file,
        validate_run_contract=_validate_run_contract,
        run_tests=_run_tests,
    )
    if not result.satisfied:
        await provider_call()

    assert result.satisfied is True
    assert "README.md" in result.summary
    provider_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_tests_milestone_not_pre_satisfied_when_existing_tests_fail() -> None:
    spec = normalize_milestone_spec(
        {
            "node_key": "work_2",
            "title": "Add tests for the core flow",
            "deliverables": ["tests/test_smoke.py"],
            "satisfaction_checks": [{"type": "tests_pass"}],
        },
        index=2,
    )
    assert spec is not None

    result = await evaluate_milestone_satisfaction(
        spec,
        working_dir="E:/SKYNET-SANDBOX/Projects/demo",
        list_files=AsyncMock(return_value=["tests/test_smoke.py", "demo.py"]),
        read_file=AsyncMock(return_value=""),
        validate_run_contract=AsyncMock(return_value={"interpreter": "python", "entrypoint": "demo.py"}),
        run_tests=AsyncMock(return_value=(False, "1 failed")),
    )

    assert result.satisfied is False
    assert result.failure_reason.startswith("tests_failed:")
