from __future__ import annotations

from typing import Any


def validate_completion_contract(
    *,
    contract: dict[str, Any],
    node_rows: list[dict[str, Any]],
    has_valid_run_contract: bool,
    blocking_findings_count: int,
) -> tuple[bool, str]:
    required_nodes = {
        str(item).strip()
        for item in (contract.get("required_nodes") or [])
        if str(item).strip()
    }
    if required_nodes:
        done_nodes = {
            str(row.get("node_key") or "").strip()
            for row in node_rows
            if str(row.get("status") or "").strip() == "done"
        }
        missing = sorted(required_nodes - done_nodes)
        if missing:
            return False, f"Missing required nodes: {', '.join(missing[:6])}"

    required_artifacts = {
        str(item).strip().lower()
        for item in (contract.get("required_artifacts") or [])
        if str(item).strip()
    }
    if "skynet_run.json" in required_artifacts and not has_valid_run_contract:
        return False, "Missing required artifact: skynet_run.json"

    if blocking_findings_count > 0:
        return False, f"Blocking critic findings remain: {blocking_findings_count}"

    return True, "completion contract satisfied"
