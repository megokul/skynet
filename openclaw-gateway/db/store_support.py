from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite


logger = logging.getLogger("skynet.db.store")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def dump_json(value: Any, default: str = "{}", *, context: str = "store_json") -> str:
    if value is None:
        return default
    try:
        return json.dumps(value, ensure_ascii=True)
    except Exception as exc:
        logger.warning("store.json_encode_fallback context=%s error=%s", context, str(exc)[:220])
        return default


def load_json_dict(
    value: Any,
    *,
    context: str,
    default: dict[str, Any] | None = None,
    warn: bool = True,
) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return dict(default or {})
    try:
        data = json.loads(str(value))
    except Exception as exc:
        if warn:
            logger.warning("store.json_decode_object_fallback context=%s error=%s", context, str(exc)[:220])
        return dict(default or {})
    if isinstance(data, dict):
        return data
    if warn:
        logger.warning("store.json_decode_object_type_mismatch context=%s type=%s", context, type(data).__name__)
    return dict(default or {})


def load_json_list(
    value: Any,
    *,
    context: str,
    default: list[Any] | None = None,
    warn: bool = True,
) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return list(default or [])
    try:
        data = json.loads(str(value))
    except Exception as exc:
        if warn:
            logger.warning("store.json_decode_list_fallback context=%s error=%s", context, str(exc)[:220])
        return list(default or [])
    if isinstance(data, list):
        return data
    if warn:
        logger.warning("store.json_decode_list_type_mismatch context=%s type=%s", context, type(data).__name__)
    return list(default or [])
