"""
SKYNET Bot — Per-Update Middleware

Registered as TypeHandler(Update, ...) in group -1 so it fires before every
other handler. Ensures the user row exists in the DB without the individual
handlers having to worry about it.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.state import KEY_DB
from db.store import ensure_user

logger = logging.getLogger("skynet.bot.middleware")


async def user_middleware(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Upsert user row on every incoming update."""
    user = update.effective_user
    if user is None:
        return
    db = context.bot_data.get(KEY_DB)
    if db is None:
        return
    try:
        await ensure_user(
            db,
            telegram_user_id=user.id,
            username=user.username    or "",
            first_name=user.first_name or "",
            last_name=user.last_name   or "",
        )
    except Exception:
        logger.exception("user_middleware: ensure_user failed for user_id=%s", user.id)
