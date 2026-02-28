"""
SKYNET Bot — Menu & Navigation Callback Handlers

Handles:
  - Navigation buttons: My Projects, Main Menu
  - Coming-soon stubs: Weather, Reminder, Web Search, Chat
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import back_to_main, main_menu
from bot.state import KEY_DB
from db.store import ensure_user, list_projects

logger = logging.getLogger("skynet.bot.menu")


async def coming_soon(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Placeholder for features not yet implemented."""
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Coming soon ⚙️",
        reply_markup=back_to_main(),
    )


async def my_projects_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show the user's project list."""
    await update.callback_query.answer()
    db = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user

    try:
        user = await ensure_user(
            db,
            telegram_user_id=tg_user.id,
            username=tg_user.username    or "",
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name   or "",
        )
        projects = await list_projects(db, user_id=user["id"])
    except Exception:
        logger.exception("my_projects_handler: failed for user_id=%s", tg_user.id)
        await update.callback_query.message.reply_text(
            "Could not load projects. Please try again.",
            reply_markup=back_to_main(),
        )
        return

    if not projects:
        text = "You have no projects yet."
    else:
        lines = [
            f"• <b>{p['name']}</b> — {p['project_type']} | {p['status']}"
            for p in projects
        ]
        text = "Your projects:\n\n" + "\n".join(lines)

    await update.callback_query.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_to_main(),
    )


async def main_menu_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Return to the main menu."""
    await update.callback_query.answer()
    user = update.effective_user
    name = (user.first_name if user else None) or "there"
    await update.callback_query.message.reply_text(
        f"Hello {name}! What can I do for you?",
        reply_markup=main_menu(),
    )
