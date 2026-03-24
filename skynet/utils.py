"""
SKYNET — shared utility helpers.

Centralises small helpers that would otherwise be duplicated across modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return utc_now().isoformat()


def resolve_repo_path(repo_root: Path, value: str, *, default: str = "") -> str:
    """Resolve a configured path against the repository root when it is relative."""
    raw = str(value or default or "").strip()
    if not raw:
        return ""
    expanded = os.path.expanduser(os.path.expandvars(raw))
    path = Path(expanded)
    if path.is_absolute():
        return str(path)
    return str((repo_root / path).resolve())
