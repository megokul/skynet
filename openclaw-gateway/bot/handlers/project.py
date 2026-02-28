"""
SKYNET Bot — Project Creation Flow

ConversationHandler states:
    AWAITING_PROJECT_NAME  (1) — waiting for the user to type a project name
    AWAITING_PROJECT_TYPE  (2) — waiting for the user to tap a project type button

Entry point: user taps "🚀 Start a Project" from the main menu.
Exit:        project row created in DB; confirmation message sent.
Cancel:      /cancel or /start at any point.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.keyboards import (
    CB_START_PROJECT,
    PROJECT_TYPE_LABELS,
    after_project_created,
    main_menu,
    project_type,
)
from bot.state import KEY_DB
from db.store import create_project, ensure_user

logger = logging.getLogger("skynet.bot.project")

# ── Conversation states ───────────────────────────────────────────────────────
AWAITING_PROJECT_NAME = 1
AWAITING_PROJECT_TYPE = 2

# Key used inside context.user_data during the conversation
_NAME_KEY = "project_name"


# ── Handlers ──────────────────────────────────────────────────────────────────

async def ask_project_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Entry: user tapped 'Start a Project'."""
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "What should we call this project?"
    )
    return AWAITING_PROJECT_NAME


async def receive_project_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User typed the project name."""
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Please give the project a name.")
        return AWAITING_PROJECT_NAME

    context.user_data[_NAME_KEY] = name
    await update.message.reply_text(
        f"What type of project is <b>{name}</b>?",
        parse_mode="HTML",
        reply_markup=project_type(),
    )
    return AWAITING_PROJECT_TYPE


async def receive_project_type(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User tapped a project type button."""
    await update.callback_query.answer()

    cb_data = update.callback_query.data or ""
    type_label = PROJECT_TYPE_LABELS.get(cb_data, "Other")
    name = context.user_data.pop(_NAME_KEY, "Untitled")

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
        project = await create_project(
            db,
            user_id=user["id"],
            name=name,
            project_type=type_label,
        )
        await update.callback_query.message.reply_text(
            f"Project <b>{project['name']}</b> created!\n"
            f"Type: {project['project_type']}",
            parse_mode="HTML",
            reply_markup=after_project_created(),
        )
    except Exception:
        logger.exception("Failed to create project name=%r type=%r", name, type_label)
        await update.callback_query.message.reply_text(
            "Something went wrong saving the project. Please try again.",
            reply_markup=main_menu(),
        )

    return ConversationHandler.END


async def cancel_project(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Fallback: /cancel or /start exits the conversation."""
    context.user_data.pop(_NAME_KEY, None)
    await update.effective_message.reply_text(
        "Project creation cancelled.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# ── Builder ───────────────────────────────────────────────────────────────────

def build_project_conversation_handler() -> ConversationHandler:
    """
    Wire the project creation flow into a single ConversationHandler.

    Registered at group 0 so it intercepts text messages before the greeting
    handler (group 1) when the user is mid-conversation.
    """
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ask_project_name, pattern=f"^{CB_START_PROJECT}$"),
        ],
        states={
            AWAITING_PROJECT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_project_name),
            ],
            AWAITING_PROJECT_TYPE: [
                CallbackQueryHandler(receive_project_type, pattern=r"^type:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_project),
            CommandHandler("start",  cancel_project),
        ],
        allow_reentry=True,
        # PTB 21+: per_message=False means one conversation per user/chat (default).
        # per_message=True would be needed only for parallel inline menus.
        per_message=False,
    )
