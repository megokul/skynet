"""
SKYNET Bot package.

The single public API is build_app() which wires all handlers and
returns a configured telegram.ext.Application ready to start polling.
"""
from __future__ import annotations

import config as cfg
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from bot.handlers.greeting import GREETING_RE, greeting_handler
from bot.handlers.menu import (
    coming_soon,
    main_menu_handler,
    my_projects_handler,
)
from bot.handlers.chat import build_chat_conversation_handler
from bot.handlers.project import build_project_conversation_handler
from bot.keyboards import (
    CB_MAIN_MENU,
    CB_MY_PROJECTS,
    CB_REMINDER,
    CB_WEB_SEARCH,
    CB_WEATHER,
)
from bot.middleware import user_middleware
from bot.state import KEY_DB, KEY_ROUTER


def build_app(db, router) -> Application:
    """
    Build and return the fully-wired Telegram Application.

    Handler group assignment:
      -1  TypeHandler  — user_middleware fires first on every update
       0  ConversationHandlers — project creation + chat; intercept text mid-flow
       1  Command + Greeting — /start, /help, greeting text
       2  CallbackQueryHandler — all button callbacks
    """
    app = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Inject shared resources so all handlers can reach them via context.bot_data.
    app.bot_data[KEY_DB]     = db
    app.bot_data[KEY_ROUTER] = router   # available for the Chat feature

    # ── Group -1: middleware ──────────────────────────────────────────────────
    app.add_handler(TypeHandler(Update, user_middleware), group=-1)

    # ── Group 0: ConversationHandlers (intercept text before greeting handler) ──
    app.add_handler(build_project_conversation_handler(), group=0)
    app.add_handler(build_chat_conversation_handler(),   group=0)

    # ── Group 1: greetings ────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", greeting_handler), group=1)
    app.add_handler(CommandHandler("help",  greeting_handler), group=1)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(GREETING_RE),
            greeting_handler,
        ),
        group=1,
    )

    # ── Group 2: button callbacks ──────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(coming_soon,         pattern=f"^{CB_WEATHER}$"),    group=2)
    app.add_handler(CallbackQueryHandler(coming_soon,         pattern=f"^{CB_REMINDER}$"),   group=2)
    app.add_handler(CallbackQueryHandler(coming_soon,         pattern=f"^{CB_WEB_SEARCH}$"), group=2)
    app.add_handler(CallbackQueryHandler(my_projects_handler, pattern=f"^{CB_MY_PROJECTS}$"),group=2)
    app.add_handler(CallbackQueryHandler(main_menu_handler,   pattern=f"^{CB_MAIN_MENU}$"),  group=2)

    return app
