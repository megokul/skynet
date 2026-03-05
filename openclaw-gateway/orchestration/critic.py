from __future__ import annotations

import json
import re
from typing import Any

SEVERITY_ORDER = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def normalize_severity(value: str) -> str:
    sev = str(value or "").strip().lower()
    if sev not in SEVERITY_ORDER:
        return "medium"
    return sev


def parse_critic_response(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty critic response")
    payload: dict[str, Any] | None = None
    try:
        maybe = json.loads(raw)
        if isinstance(maybe, dict):
            payload = maybe
    except Exception:
        payload = None
    if payload is None:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
        if match:
            maybe = json.loads(match.group(1))
            if isinstance(maybe, dict):
                payload = maybe
    if payload is None:
        raise ValueError("critic response is not valid JSON object")

    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        findings = []

    normalized_findings: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        files = finding.get("files", [])
        if not isinstance(files, list):
            files = []
        normalized_findings.append(
            {
                "severity": normalize_severity(str(finding.get("severity", "medium"))),
                "code": str(finding.get("code") or "").strip(),
                "message": str(finding.get("message") or "").strip(),
                "files": [str(item).strip() for item in files if str(item).strip()],
                "suggested_fix": str(finding.get("suggested_fix") or "").strip(),
            }
        )
    passed = bool(payload.get("passed", not normalized_findings))
    return {"passed": passed, "findings": normalized_findings}


def is_blocking(findings: list[dict[str, Any]], threshold: str = "high") -> bool:
    threshold_value = SEVERITY_ORDER.get(normalize_severity(threshold), SEVERITY_ORDER["high"])
    for finding in findings:
        sev = normalize_severity(str(finding.get("severity", "medium")))
        if SEVERITY_ORDER.get(sev, 0) >= threshold_value:
            return True
    return False


def build_review_prompt(
    *,
    project_name: str,
    milestone_text: str,
    files_written: list[str],
    gate_summary: str,
) -> str:
    return (
        "You are a strict code review critic.\n"
        "Return ONLY valid JSON with this schema:\n"
        '{"passed": true|false, "findings":[{"severity":"low|medium|high|critical","code":"ID","message":"...","files":["path"],"suggested_fix":"..."}]}\n'
        "Do not include markdown.\n\n"
        f"Project: {project_name}\n"
        f"Milestone: {milestone_text}\n"
        f"Files written: {', '.join(files_written) if files_written else '(none)'}\n"
        f"Strict-gate summary: {gate_summary or '(none)'}\n"
    )

