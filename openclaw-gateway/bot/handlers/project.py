"""
SKYNET Bot — Project Creation Flow

ConversationHandler states:
    AWAITING_PROJECT_NAME  (1) — waiting for the user to type a project name
    AWAITING_PROJECT_TYPE  (2) — waiting for the user to tap a project type button
    GATHERING_REQUIREMENTS (3) — Project Specialist AI gathers requirements
    REVIEWING_PLAN         (4) — user reviewing the generated plan

Entry point: user taps "🚀 Start a Project" from the main menu.
Exit:        project saved to DB with approved plan; confirmation sent.
Cancel:      /cancel or /start at any point.
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

from bot.keyboards import (
    CB_PLAN_APPROVE,
    CB_PLAN_CHANGES,
    CB_REQUIREMENTS_DONE,
    CB_START_PROJECT,
    PROJECT_TYPE_LABELS,
    main_menu,
    plan_review,
    project_type,
    requirements_done,
    start_coding,
)

from bot.project_templates import get_template
from bot.state import KEY_DB, KEY_ROUTER
from db.store import create_project, ensure_user

logger = logging.getLogger("skynet.bot.project")

# ── Conversation states ───────────────────────────────────────────────────────
AWAITING_PROJECT_NAME  = 1
AWAITING_PROJECT_TYPE  = 2
GATHERING_REQUIREMENTS = 3
REVIEWING_PLAN         = 4

# Keys inside context.user_data
_NAME_KEY     = "project_name"
_TYPE_KEY     = "project_type"
_REQS_HISTORY = "project_reqs_history"
_PLAN_KEY     = "project_plan"

# Max turns kept in requirements conversation history
_MAX_REQS_TURNS = 30


# ── System prompt ─────────────────────────────────────────────────────────────

def _specialist_prompt(name: str, project_type_label: str, template: dict) -> str:
    questions = "\n".join(f"- {q}" for q in template["questions"])
    return (
        f"You are the Project Specialist for OpenClaw — a sharp, experienced "
        f"software architect.\n"
        f"You are helping the user plan '{name}', a {project_type_label} project.\n\n"
        f"Recommended stack: {template['stack']}\n\n"
        f"Key requirements to cover (ask in order, 1-2 at a time):\n"
        f"{questions}\n\n"
        f"Guidelines:\n"
        f"- Be concise — this is a Telegram chat, not a document\n"
        f"- Ask follow-up questions if answers are vague\n"
        f"- After covering the key questions, tell the user exactly:\n"
        f"  'I have everything I need. Send /plan to generate your project plan.'\n\n"
        f"When generating the plan, use this exact format:\n"
        f"**{name} — Project Plan**\n"
        f"**Overview:** (2-3 sentences)\n"
        f"**Core Features:**\n  - feature 1\n  - feature 2\n"
        f"**Tech Stack:** (specific versions/libraries)\n"
        f"**Project Structure:** (top-level folders)\n"
        f"**Milestones:**\n  1. milestone\n  2. milestone\n"
        f"**Open Questions:** (anything still unclear, or 'None')"
    )


def _trim_history(history: list[dict]) -> list[dict]:
    max_msgs = _MAX_REQS_TURNS * 2
    return history[-max_msgs:] if len(history) > max_msgs else history


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
    """User tapped a project type — hand off to Project Specialist."""
    await update.callback_query.answer()

    cb_data    = update.callback_query.data or ""
    type_label = PROJECT_TYPE_LABELS.get(cb_data, "Other")
    name       = context.user_data.get(_NAME_KEY, "Untitled")
    template   = get_template(type_label)

    context.user_data[_TYPE_KEY] = type_label

    # Opening message from the Project Specialist (seeded into history)
    opening = (
        f"I'm your Project Specialist.\n\n"
        f"Project: <b>{name}</b> — {type_label}\n"
        f"Stack: {template['stack']}\n\n"
        f"{template['questions'][0]}"
    )

    context.user_data[_REQS_HISTORY] = [
        {"role": "assistant", "content": opening},
    ]

    await update.callback_query.message.reply_text(
        opening, parse_mode="HTML", reply_markup=requirements_done(),
    )
    return GATHERING_REQUIREMENTS


async def handle_requirements_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Forward each user message to the Project Specialist LLM."""
    user_text = (update.message.text or "").strip()
    if not user_text:
        return GATHERING_REQUIREMENTS

    router = context.bot_data.get(KEY_ROUTER)
    if router is None:
        await update.message.reply_text("AI router is not available right now.")
        return GATHERING_REQUIREMENTS

    await update.effective_chat.send_action(ChatAction.TYPING)

    name       = context.user_data.get(_NAME_KEY, "Untitled")
    type_label = context.user_data.get(_TYPE_KEY, "Other")
    template   = get_template(type_label)
    history: list[dict] = context.user_data.get(_REQS_HISTORY, [])

    history.append({"role": "user", "content": user_text})
    history = _trim_history(history)

    try:
        response = await router.chat(
            messages=history,
            system=_specialist_prompt(name, type_label, template),
            max_tokens=1024,
            task_type="planning",
        )
        reply = (response.text or "").strip() or "…"
    except Exception:
        logger.exception("Requirements AI call failed")
        await update.message.reply_text(
            "AI is unavailable right now. Please try again."
        )
        return GATHERING_REQUIREMENTS

    history.append({"role": "assistant", "content": reply})
    context.user_data[_REQS_HISTORY] = history

    await update.message.reply_text(reply, reply_markup=requirements_done())
    return GATHERING_REQUIREMENTS


async def requirements_done_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User tapped 'Done — Generate Plan' button during requirements chat."""
    await update.callback_query.answer()
    # Delegate directly to plan generation (same logic as /plan).
    return await _do_generate_plan(update.callback_query.message, context)


async def cmd_generate_plan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User sent /plan — generate the full project plan."""
    return await _do_generate_plan(update.message, context)


async def _do_generate_plan(
    message,  # telegram.Message (from command or callback_query.message)
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Shared logic: call the Project Specialist to generate the plan."""
    router = context.bot_data.get(KEY_ROUTER)
    if router is None:
        await message.reply_text("AI router is not available right now.")
        return GATHERING_REQUIREMENTS

    await message.chat.send_action(ChatAction.TYPING)
    await message.reply_text("Generating your project plan…")

    name       = context.user_data.get(_NAME_KEY, "Untitled")
    type_label = context.user_data.get(_TYPE_KEY, "Other")
    template   = get_template(type_label)
    history: list[dict] = context.user_data.get(_REQS_HISTORY, [])

    history.append({
        "role": "user",
        "content": "Generate the full project plan now based on everything we discussed.",
    })

    try:
        response = await router.chat(
            messages=history,
            system=_specialist_prompt(name, type_label, template),
            max_tokens=2048,
            task_type="planning",
        )
        plan = (response.text or "").strip() or "Could not generate plan."
    except Exception:
        logger.exception("Plan generation AI call failed")
        await message.reply_text("Could not generate the plan. Please try /plan again.")
        return GATHERING_REQUIREMENTS

    context.user_data[_PLAN_KEY] = plan
    history.append({"role": "assistant", "content": plan})
    context.user_data[_REQS_HISTORY] = history

    await message.reply_text(plan, reply_markup=plan_review())
    return REVIEWING_PLAN


async def approve_plan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User approved the plan — save project to DB and offer to start coding."""
    await update.callback_query.answer()

    db      = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user
    name       = context.user_data.get(_NAME_KEY, "Untitled")
    type_label = context.user_data.get(_TYPE_KEY, "Other")
    plan       = context.user_data.get(_PLAN_KEY, "")

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
            description=plan,
        )
    except Exception:
        logger.exception("Failed to save project name=%r type=%r", name, type_label)
        await update.callback_query.message.reply_text(
            "Something went wrong saving the project. Please try again.",
            reply_markup=main_menu(),
        )
        _clear_user_data(context)
        return ConversationHandler.END

    context.user_data["last_project_id"] = project["id"]
    _clear_user_data(context)

    await update.callback_query.message.reply_text(
        f"✅ <b>{project['name']}</b> saved!\n"
        f"Type: {project['project_type']}\n\n"
        "Ready to build it?",
        parse_mode="HTML",
        reply_markup=start_coding(),
    )
    return ConversationHandler.END


async def request_changes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User wants changes — return to requirements chat."""
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Sure — what would you like to change or add?\n"
        "Send /plan again when you're ready for a new version."
    )
    return GATHERING_REQUIREMENTS




async def cancel_project(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Fallback: /cancel or /start exits the conversation."""
    _clear_user_data(context)
    await update.effective_message.reply_text(
        "Project creation cancelled.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clear_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (_NAME_KEY, _TYPE_KEY, _REQS_HISTORY, _PLAN_KEY):
        context.user_data.pop(key, None)



# ── Builder ───────────────────────────────────────────────────────────────────

def build_project_conversation_handler() -> ConversationHandler:
    """
    Wire the full project + specialist flow into a single ConversationHandler.
    Registered at group 0 so it intercepts text before the greeting handler.
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
            GATHERING_REQUIREMENTS: [
                CallbackQueryHandler(requirements_done_handler, pattern=f"^{CB_REQUIREMENTS_DONE}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_requirements_message),
            ],
            REVIEWING_PLAN: [
                CallbackQueryHandler(approve_plan,    pattern=f"^{CB_PLAN_APPROVE}$"),
                CallbackQueryHandler(request_changes, pattern=f"^{CB_PLAN_CHANGES}$"),
            ],
        },
        fallbacks=[
            CommandHandler("plan",   cmd_generate_plan),
            CommandHandler("cancel", cancel_project),
            CommandHandler("start",  cancel_project),
        ],
        allow_reentry=True,
        per_message=False,
    )
