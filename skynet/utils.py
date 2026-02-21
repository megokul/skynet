"""
SKYNET — shared utility helpers.

Centralises small helpers that would otherwise be duplicated across modules.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return utc_now().isoformat()
