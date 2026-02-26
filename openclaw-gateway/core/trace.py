"""
Structured JSON control-flow logging utilities.

These events power machine filtering/search in `skynet-control-flow.log` and
complement the human-readable step trace in `core/tracing.py`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


_flow_logger = logging.getLogger("skynet.flow")


def trace_flow(event: str, **fields: Any) -> None:
    """
    Emit one structured control-flow event.

    This is the machine-readable stream (`skynet-control-flow.log`) used for
    filtering/search and incident triage. For human narrative traces see
    `core/tracing.py` (`skynet.trace.log`).
    """
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    if fields:
        payload.update({k: _sanitize(v) for k, v in fields.items()})
    try:
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        encoded = json.dumps(
            {
                "ts": payload["ts"],
                "event": event,
                "error": "trace-serialization-failed",
            },
            ensure_ascii=True,
        )
    _flow_logger.info(encoded)


def _sanitize(value: Any) -> Any:
    """
    Normalize nested values for stable JSON logging.

    We cap string length to keep high-volume logs bounded.
    """
    if isinstance(value, str):
        text = value.replace("\r", " ").replace("\n", "\\n")
        if len(text) > 500:
            return text[:500] + "...<truncated>"
        return text
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    return value
