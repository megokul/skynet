from __future__ import annotations

import json
import re
from typing import Any

from skynet.prompt_library import render_prompt


def build_architect_prompt(
    *,
    project_name: str,
    goal: str,
    director_contract: dict[str, Any],
    previous_state: dict[str, Any] | None,
    index_summary: list[dict[str, Any]] | None = None,
) -> str:
    return render_prompt(
        "gateway/orchestration/architect_contract.md",
        project_name=project_name,
        goal=goal,
        director_contract_json=json.dumps(director_contract or {}, ensure_ascii=True),
        previous_state_json=json.dumps(previous_state or {}, ensure_ascii=True),
        index_summary_json=json.dumps(index_summary or [], ensure_ascii=True),
    )


def parse_architecture_state(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty architect response")
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            raise ValueError("architect response is not valid JSON")
        parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        raise ValueError("architect response must be a JSON object")

    def _list_of_dicts(key: str) -> list[dict[str, Any]]:
        value = parsed.get(key) or []
        if not isinstance(value, list):
            return []
        out: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                out.append(item)
        return out

    constraints = parsed.get("constraints") or []
    if not isinstance(constraints, list):
        constraints = []

    return {
        "components": _list_of_dicts("components"),
        "interfaces": _list_of_dicts("interfaces"),
        "boundaries": _list_of_dicts("boundaries"),
        "data_flows": _list_of_dicts("data_flows"),
        "constraints": [str(item).strip() for item in constraints if str(item).strip()],
        "adr_summary": str(parsed.get("adr_summary") or "").strip(),
    }


def next_architecture_version(previous_state: dict[str, Any] | None) -> int:
    if not previous_state:
        return 1
    try:
        return max(1, int(previous_state.get("version", 0) or 0) + 1)
    except Exception:
        return 1


def default_architecture_state(*, goal: str) -> dict[str, Any]:
    text = str(goal or "").strip() or "Requested project feature"
    return {
        "components": [{"name": "application", "purpose": text}],
        "interfaces": [{"name": "entrypoint", "contract": "CLI/API runtime entry"}],
        "boundaries": [{"from": "application", "to": "infrastructure", "allowed": True}],
        "data_flows": [],
        "constraints": ["Preserve strict run contract determinism"],
        "adr_summary": "Baseline architecture state created by fallback.",
    }


def evaluate_architecture_contract(
    *,
    findings: list[dict[str, Any]],
    max_violations: int = 0,
) -> tuple[bool, int]:
    violations = 0
    for finding in findings:
        severity = str(finding.get("severity") or "").strip().lower()
        code = str(finding.get("code") or "").strip().lower()
        if "arch" in code or severity in {"high", "critical"}:
            violations += 1
    return violations <= max(0, int(max_violations)), violations
