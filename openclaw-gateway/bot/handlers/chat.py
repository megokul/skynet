"""
SKYNET Bot — Chat Handler

Free-form conversational AI backed by the provider router.

Flow:
  User taps "💬 Chat"
    → bot sends a welcome + hint
    → every text message is forwarded to the AI with full history
    → /done, /cancel, or /start exits back to the main menu

History is stored in context.user_data["chat_history"] as a standard
OpenAI-format messages list. It is capped at MAX_HISTORY_TURNS to avoid
exceeding provider token limits. History is cleared on exit so the next
chat session starts fresh.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.keyboards import CB_CHAT, back_to_main, main_menu
from bot.state import KEY_ROUTER

logger = logging.getLogger("skynet.bot.chat")

# ── Constants ─────────────────────────────────────────────────────────────────
CHATTING = 1

_HISTORY_KEY   = "chat_history"
MAX_HISTORY_TURNS = 20   # keep last 20 user+assistant pairs (40 messages)

_SYSTEM_PROMPT = (
    "You are Jarvis, a sharp and capable personal assistant. "
    "Be concise and direct — no fluff, no filler. "
    "When the user wants to build something, remind them they can use "
    "'Start a Project' from the main menu. "
    "Today you are running inside a Telegram bot."
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trim_history(history: list[dict]) -> list[dict]:
    """Keep only the most recent MAX_HISTORY_TURNS turns."""
    max_msgs = MAX_HISTORY_TURNS * 2
    return history[-max_msgs:] if len(history) > max_msgs else history


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Entry: user tapped '💬 Chat'."""
    await update.callback_query.answer()
    context.user_data[_HISTORY_KEY] = []
    await update.callback_query.message.reply_text(
        "Chat mode — I'm listening.\n"
        "Type anything. Send /done to return to the main menu.",
    )
    return CHATTING


async def handle_chat_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Forward every user message to the AI and reply with the response."""
    user_text = (update.message.text or "").strip()
    if not user_text:
        return CHATTING

    router = context.bot_data.get(KEY_ROUTER)
    if router is None:
        await update.message.reply_text("AI router is not available right now.")
        return CHATTING

    # Show typing indicator while the AI thinks.
    await update.effective_chat.send_action(ChatAction.TYPING)

    history: list[dict] = context.user_data.get(_HISTORY_KEY, [])
    history.append({"role": "user", "content": user_text})
    history = _trim_history(history)

    try:
        response = await router.chat(
            messages=history,
            system=_SYSTEM_PROMPT,
            max_tokens=1024,
            task_type="general",
        )
        reply = (response.text or "").strip() or "…"
    except Exception:
        logger.exception("Chat AI call failed")
        await update.message.reply_text(
            "All AI providers are currently unavailable. Please try again later.",
            reply_markup=back_to_main(),
        )
        return CHATTING

    history.append({"role": "assistant", "content": reply})
    context.user_data[_HISTORY_KEY] = history

    await update.message.reply_text(reply)
    return CHATTING


async def end_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Exit chat mode cleanly via /done, /cancel, or /start."""
    context.user_data.pop(_HISTORY_KEY, None)
    await update.effective_message.reply_text(
        "Chat ended.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# ── Builder ───────────────────────────────────────────────────────────────────

def build_chat_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_chat, pattern=f"^{CB_CHAT}$"),
        ],
        states={
            CHATTING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message),
            ],
        },
        fallbacks=[
            CommandHandler("done",   end_chat),
            CommandHandler("cancel", end_chat),
            CommandHandler("start",  end_chat),
        ],
        allow_reentry=True,
        per_message=False,
    )
