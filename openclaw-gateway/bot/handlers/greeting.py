"""
SKYNET Bot — Greeting Handler

Fires on /start, /help, and any plain-text message that looks like a greeting.
Responds with the main menu keyboard.
"""
from __future__ import annotations

import re

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu

# Matches common greetings at the start of a message (case-insensitive).
# Extend this set as needed — it's intentionally broad.
GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|hiya|hola|yo|sup|howdy|greetings|start|help|"
    r"good\s+morning|good\s+afternoon|good\s+evening|"
    r"مرحبا|سلام|مرحبًا)\b",
    re.IGNORECASE,
)


def is_greeting(text: str) -> bool:
    return bool(GREETING_RE.match(text or ""))


async def greeting_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    name = (user.first_name if user else None) or "there"
    await update.effective_message.reply_text(
        f"Hello {name}! What can I do for you?",
        reply_markup=main_menu(),
    )
