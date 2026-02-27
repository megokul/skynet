"""Compatibility shim for disabled legacy flow trace API."""

from __future__ import annotations

from typing import Any


def trace_flow(event: str, **fields: Any) -> None:
    """
    Legacy no-op.

    `core/dev_trace.py` is now the sole trace writer for cognitive traces.
    """
    del event, fields
