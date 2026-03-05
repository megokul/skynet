from __future__ import annotations

from collections import Counter
from typing import Any


def build_pattern_key(*, failure_type: str, critic_code: str = "", node_type: str = "") -> str:
    ft = str(failure_type or "").strip().upper() or "UNKNOWN"
    cc = str(critic_code or "").strip().upper()
    nt = str(node_type or "").strip().lower()
    parts = [ft]
    if cc:
        parts.append(cc)
    if nt:
        parts.append(nt)
    return ":".join(parts)


def summarize_learning_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    failure_counter: Counter[str] = Counter()
    pattern_counter: Counter[str] = Counter()
    for event in events:
        failure = str(event.get("failure_type") or "").strip().upper()
        pattern = str(event.get("pattern_key") or "").strip()
        if failure:
            failure_counter[failure] += 1
        if pattern:
            pattern_counter[pattern] += 1
    return {
        "failure_counts": dict(failure_counter),
        "top_patterns": [{"pattern_key": key, "count": count} for key, count in pattern_counter.most_common(10)],
        "sample_count": len(events),
    }


def build_conservative_prompt_policy(
    *,
    events: list[dict[str, Any]],
    min_samples: int = 5,
    apply_mode: str = "conservative",
) -> dict[str, Any] | None:
    summary = summarize_learning_events(events)
    if int(summary.get("sample_count", 0) or 0) < max(1, int(min_samples)):
        return None
    mode = str(apply_mode or "conservative").strip().lower()
    hints: list[str] = []
    failures = summary.get("failure_counts") or {}
    if isinstance(failures, dict):
        if int(failures.get("CONTRACT_FAILED", 0) or 0) > 0:
            hints.append("Always generate/update skynet_run.json with valid interpreter and relative entrypoint.")
        if int(failures.get("TEST_FAILED", 0) or 0) > 0:
            hints.append("Prioritize writing tests before concluding milestone output.")
        if int(failures.get("GENERATION_FAILED", 0) or 0) > 0:
            hints.append("Return concrete file outputs and avoid generic explanations.")
    if mode == "conservative":
        hints = hints[:2]
    if not hints:
        return None
    return {
        "mode": mode,
        "hints": hints,
        "sample_count": int(summary.get("sample_count", 0) or 0),
    }


def apply_prompt_policy(*, prompt: str, policy: dict[str, Any] | None) -> str:
    if not policy:
        return prompt
    hints = [str(item).strip() for item in (policy.get("hints") or []) if str(item).strip()]
    if not hints:
        return prompt
    hint_block = "\n".join(f"- {item}" for item in hints)
    return f"{prompt}\n\nPolicy hints:\n{hint_block}\n"
