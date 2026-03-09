"""
CHATHAN Worker — Audit Logger

Append-only JSONL audit trail.  Every inbound request and its outcome
are recorded with a UTC timestamp, the resolved tier, and the result
or rejection reason.

The log file lives under config.AUDIT_LOG_DIR and is created on first write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from agent_config import AUDIT_LOG_DIR, AUDIT_LOG_FILE

logger = logging.getLogger("chathan.audit")

_log_path: str | None = None
_write_lock = asyncio.Lock()


def _ensure_log_dir() -> str:
    """
    Ensure log dir.
    
    Purpose:
    - Implement `_ensure_log_dir` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    global _log_path
    if _log_path is None:
        os.makedirs(AUDIT_LOG_DIR, exist_ok=True)
        _log_path = os.path.join(AUDIT_LOG_DIR, AUDIT_LOG_FILE)
    return _log_path


def _build_entry(
    *,
    request_id: str,
    action: str,
    tier: str,
    params: dict[str, Any] | None,
    outcome: str,
    detail: Any = None,
    duration_ms: float | None = None,
) -> dict[str, Any]:
    """
    Build entry.
    
    Purpose:
    - Implement `_build_entry` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `request_id`: input used by this function to compute or route work.
    - `action`: input used by this function to compute or route work.
    - `tier`: input used by this function to compute or route work.
    - `params`: input used by this function to compute or route work.
    - `outcome`: input used by this function to compute or route work.
    - `detail`: input used by this function to compute or route work.
    - `duration_ms`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
    """

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch": time.time(),
        "request_id": request_id,
        "action": action,
        "tier": tier,
        "params": params,
        "outcome": outcome,
        "detail": detail,
        "duration_ms": duration_ms,
    }


async def log_event(
    *,
    request_id: str,
    action: str,
    tier: str,
    params: dict[str, Any] | None = None,
    outcome: str,
    detail: Any = None,
    duration_ms: float | None = None,
) -> None:
    """Append one audit record to the JSONL log file."""
    entry = _build_entry(
        request_id=request_id,
        action=action,
        tier=tier,
        params=params,
        outcome=outcome,
        detail=detail,
        duration_ms=duration_ms,
    )
    line = json.dumps(entry, default=str) + "\n"
    try:
        path = _ensure_log_dir()
        async with _write_lock:
            # Audit logging is best-effort and must never break action execution.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _append_line, path, line)
    except Exception:
        logger.warning("audit write failed", exc_info=True)
        return

    logger.info(
        "audit | %s | %s | %s | %s",
        request_id,
        action,
        tier,
        outcome,
    )


def _append_line(path: str, line: str) -> None:
    """
    Append line.
    
    Purpose:
    - Implement `_append_line` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `path`: input used by this function to compute or route work.
    - `line`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)

