from __future__ import annotations

from typing import Any


def normalize_capabilities(raw: list[str] | None) -> list[str]:
    return [str(item).strip().lower() for item in (raw or []) if str(item).strip()]


def select_worker(
    *,
    workers: list[dict[str, Any]],
    required_capabilities: list[str] | None = None,
    default_worker_id: str = "worker-primary",
    strategy: str = "capability_priority",
) -> tuple[str, str]:
    required = set(normalize_capabilities(required_capabilities))
    if not workers:
        return default_worker_id, "default-no-registry"

    ranked = sorted(
        workers,
        key=lambda row: (-int(row.get("priority", 100) or 100), str(row.get("id") or "")),
    )
    if strategy.strip().lower() != "capability_priority" or not required:
        selected = ranked[0]
        return str(selected.get("id") or default_worker_id), "highest-priority"

    best_match: dict[str, Any] | None = None
    best_score = -1
    for worker in ranked:
        capabilities = set(normalize_capabilities(worker.get("capabilities") or []))
        score = len(required.intersection(capabilities))
        if score > best_score:
            best_match = worker
            best_score = score
    if not best_match:
        return default_worker_id, "default-empty-match"
    if best_score <= 0:
        return str(best_match.get("id") or default_worker_id), "priority-no-capability-match"
    return str(best_match.get("id") or default_worker_id), f"capability-match:{best_score}"
