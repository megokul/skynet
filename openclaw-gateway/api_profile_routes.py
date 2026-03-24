from __future__ import annotations

import json

from aiohttp import web


async def handle_get_profile_route(request: web.Request) -> web.Response:
    telegram_user_id = request.match_info.get("telegram_user_id", "").strip()
    if not telegram_user_id.isdigit():
        return web.json_response({"error": "telegram_user_id must be numeric."}, status=400)

    db = request.app.get("idempotency_db")
    if db is None:
        return web.json_response({"error": "Database not initialized."}, status=503)

    try:
        from db import store

        user = await store.get_user_by_telegram_id(db, int(telegram_user_id))
        if not user:
            return web.json_response({"error": "User not found."}, status=404)
        user_id = int(user["id"])
        facts = await store.list_profile_facts(db, user_id=user_id, active_only=True)
        prefs = await store.get_user_preferences(db, user_id=user_id)
        return web.json_response({"user": user, "facts": facts, "preferences": prefs})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_forget_profile_route(request: web.Request) -> web.Response:
    telegram_user_id = request.match_info.get("telegram_user_id", "").strip()
    if not telegram_user_id.isdigit():
        return web.json_response({"error": "telegram_user_id must be numeric."}, status=400)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body."}, status=400)

    key = str(payload.get("key_or_text", "")).strip()
    if not key:
        return web.json_response({"error": "Missing key_or_text."}, status=400)

    db = request.app.get("idempotency_db")
    if db is None:
        return web.json_response({"error": "Database not initialized."}, status=503)

    try:
        from db import store

        user = await store.get_user_by_telegram_id(db, int(telegram_user_id))
        if not user:
            return web.json_response({"error": "User not found."}, status=404)
        removed = await store.forget_profile_facts(
            db,
            user_id=int(user["id"]),
            key_or_text=key,
        )
        await store.add_memory_audit_log(
            db,
            user_id=int(user["id"]),
            action="forget_api",
            target_type="fact",
            target_key=key,
            detail=f"Removed facts: {removed}",
        )
        return web.json_response({"ok": True, "removed": removed})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_set_memory_policy_route(request: web.Request) -> web.Response:
    telegram_user_id = request.match_info.get("telegram_user_id", "").strip()
    if not telegram_user_id.isdigit():
        return web.json_response({"error": "telegram_user_id must be numeric."}, status=400)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body."}, status=400)

    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return web.json_response({"error": "enabled must be boolean."}, status=400)

    db = request.app.get("idempotency_db")
    if db is None:
        return web.json_response({"error": "Database not initialized."}, status=503)

    try:
        from db import store

        user = await store.get_user_by_telegram_id(db, int(telegram_user_id))
        if not user:
            return web.json_response({"error": "User not found."}, status=404)
        user_id = int(user["id"])
        await store.set_user_memory_enabled(db, user_id=user_id, enabled=enabled)
        await store.add_memory_audit_log(
            db,
            user_id=user_id,
            action="memory_policy_api",
            target_type="policy",
            target_key="memory_enabled",
            detail=f"enabled={enabled}",
        )
        return web.json_response({"ok": True, "enabled": enabled})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)
