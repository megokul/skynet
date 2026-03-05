from __future__ import annotations

import json
from typing import Any


def _limit_text(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."


def build_context_bundle(
    *,
    objective: str,
    active_node: dict[str, Any],
    last_failure: dict[str, Any] | None,
    required_artifacts: list[str],
    memory_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    index_hits: list[dict[str, Any]],
    max_chars: int,
) -> str:
    required = [str(item).strip() for item in (required_artifacts or []) if str(item).strip()]
    memory_payload = [
        {
            "tier": row.get("tier"),
            "key": row.get("memory_key"),
            "value": row.get("memory_value"),
        }
        for row in (memory_rows or [])[:20]
    ]
    event_payload = [
        {
            "id": row.get("id"),
            "event": row.get("event_type"),
            "status": row.get("status"),
            "node": row.get("node_key"),
            "failure": row.get("failure_type"),
        }
        for row in (event_rows or [])[:30]
    ]
    finding_payload = [
        {
            "severity": row.get("severity"),
            "code": row.get("code"),
            "message": row.get("message"),
            "files": row.get("files_json") or row.get("files"),
        }
        for row in (findings or [])[:15]
    ]
    payload = {
        "objective": objective,
        "active_node": active_node,
        "last_failure": last_failure or {},
        "required_artifacts": required,
        "index_hits": (index_hits or [])[:12],
        "memory": memory_payload,
        "events": event_payload,
        "findings": finding_payload,
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return _limit_text(raw, max(1200, int(max_chars)))
