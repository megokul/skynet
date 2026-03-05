from __future__ import annotations

import json
import re
from typing import Any


def build_director_prompt(
    *,
    project_name: str,
    project_type: str,
    goal: str,
    constraints: list[str] | None = None,
    memory_snapshot: dict[str, Any] | None = None,
) -> str:
    constraint_lines = "\n".join(f"- {item}" for item in (constraints or []) if str(item).strip())
    memory_blob = json.dumps(memory_snapshot or {}, ensure_ascii=True)
    return (
        "You are the Director agent for a coding orchestration loop.\n"
        "Return ONLY valid JSON with this schema:\n"
        '{"objective":"...",'
        '"scope":"...",'
        '"success_metrics":["..."],'
        '"risk_budget":{"max_repairs":1,"max_runtime_seconds":3600},'
        '"constraints":["..."]}\n'
        "Do not return markdown.\n\n"
        f"Project: {project_name} ({project_type})\n"
        f"Goal:\n{goal}\n\n"
        f"Constraints:\n{constraint_lines or '- none'}\n\n"
        f"Memory snapshot JSON:\n{memory_blob}\n"
    )


def parse_director_contract(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty director response")
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            raise ValueError("director response is not valid JSON")
        parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        raise ValueError("director response must be a JSON object")

    objective = str(parsed.get("objective") or "").strip()
    scope = str(parsed.get("scope") or "").strip()
    success_metrics = parsed.get("success_metrics") or []
    constraints = parsed.get("constraints") or []
    risk_budget = parsed.get("risk_budget") or {}
    if not isinstance(success_metrics, list):
        success_metrics = []
    if not isinstance(constraints, list):
        constraints = []
    if not isinstance(risk_budget, dict):
        risk_budget = {}

    if not objective:
        objective = scope or "Deliver project milestones with strict quality gates."
    if not scope:
        scope = objective

    return {
        "objective": objective,
        "scope": scope,
        "success_metrics": [str(item).strip() for item in success_metrics if str(item).strip()],
        "risk_budget": {
            "max_repairs": int(risk_budget.get("max_repairs", 1) or 1),
            "max_runtime_seconds": int(risk_budget.get("max_runtime_seconds", 3600) or 3600),
        },
        "constraints": [str(item).strip() for item in constraints if str(item).strip()],
    }


def default_director_contract(*, goal: str) -> dict[str, Any]:
    objective = str(goal or "").strip() or "Deliver requested project output."
    return {
        "objective": objective,
        "scope": objective,
        "success_metrics": [
            "All required milestones completed",
            "Strict quality gates pass",
            "Runnable output contract validated",
        ],
        "risk_budget": {"max_repairs": 1, "max_runtime_seconds": 3600},
        "constraints": ["SSH-first execution", "Codex-only stage chain"],
    }
