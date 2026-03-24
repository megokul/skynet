from __future__ import annotations

import datetime as dt


QWEN_PROBE_STATUS: dict[str, dict[str, str]] = {
    "planner_ready": {
        "status": "unknown",
        "output_contract": "",
        "updated_at": "",
        "detail": "",
    },
    "plan_generation": {
        "status": "unknown",
        "output_contract": "",
        "updated_at": "",
        "detail": "",
    },
}

SSH_ONLY_MODES = {"ssh", "ssh_tunnel", "tunnel", "ssh-only"}


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def record_qwen_probe_result(
    *,
    probe_kind: str,
    status: str,
    output_contract: str = "",
    detail: str = "",
) -> None:
    key = str(probe_kind or "").strip().lower()
    if key not in QWEN_PROBE_STATUS:
        return
    QWEN_PROBE_STATUS[key] = {
        "status": str(status or "").strip() or "unknown",
        "output_contract": str(output_contract or "").strip(),
        "updated_at": utc_now_iso(),
        "detail": str(detail or "").strip()[:240],
    }


def maybe_record_qwen_probe(
    action: str,
    params: dict[str, object],
    result: dict[str, object],
) -> None:
    if str(action or "").strip().lower() != "run_coding_agent":
        return
    agent = str((params or {}).get("agent") or "").strip().lower()
    if agent != "qwen":
        return
    task_id = str((params or {}).get("task_id") or "").strip().lower()
    if not task_id.startswith("preflight-qwen-"):
        return
    probe_kind = task_id.removeprefix("preflight-qwen-")
    inner = result.get("result", result) if isinstance(result, dict) else {}
    output_contract = str(inner.get("output_contract") or "").strip()
    returncode = int(inner.get("returncode", inner.get("exit_code", 0)) or 0)
    status = "ok" if returncode == 0 and output_contract == "ok" else "fail"
    detail = str(inner.get("stderr") or inner.get("assistant_text") or inner.get("stdout") or "").strip()
    record_qwen_probe_result(
        probe_kind=probe_kind,
        status=status,
        output_contract=output_contract,
        detail=detail,
    )
