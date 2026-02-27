"""
bot/commands.py -- All cmd_* handlers, handle_callback, handle_text, build_app,
                   on_project_progress, and request_worker_approval.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import inspect
import json
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import bot_config as cfg
from . import state
from .helpers import (
    _action_result_ok,
    _ask_confirm,
    _ask_remove_project_confirmation,
    _authorised,
    _extract_json_object,
    _format_result,
    _gateway_get,
    _gateway_post,
    _join_project_path,
    _notify_styled,
    _parse_path,
    _project_display,
    _send_action,
    _send_to_user,
    _spawn_background_task,
    _truncate_for_notice,
)
from .memory import (
    _capture_profile_memory,
    _ensure_memory_user,
    _forget_profile_target,
    _format_profile_summary,
    _is_no_store_once_message,
    _maybe_handle_memory_text_command,
    _set_memory_enabled_for_user,
)
from .nl_intent import (
    _is_pure_greeting,
    _resolve_project,
)

logger = logging.getLogger("skynet.telegram")
_CONV_START_NEW = "conv:start_new"
_CONV_CONTINUE = "conv:continue"
_CONV_SWITCH_PREFIX = "conv:switch:"


async def on_project_progress(project_id: str, event_type: str, summary: str) -> None:
    """Called by the orchestrator to send progress updates to Telegram."""
    level_map = {
        "started": "progress",
        "task_started": "progress",
        "task_completed": "success",
        "milestone_started": "progress",
        "milestone_review": "info",
        "testing": "info",
        "completed": "success",
        "error": "error",
        "paused": "warning",
        "resumed": "progress",
        "cancelled": "warning",
    }
    title = f"Project Event: {event_type}"
    await _notify_styled(level_map.get(event_type, "info"), title, summary, project=project_id)


async def _ensure_memory_user_row(update: Update) -> dict:
    """
    Ensure memory user row.
    
    Purpose:
    - Implement `_ensure_memory_user_row` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict` when available; otherwise side effects only.
    """

    from db import store

    if not state._project_manager:
        raise RuntimeError("Project manager is not initialized.")

    user = update.effective_user
    if not user:
        raise RuntimeError("User context is unavailable.")

    return await store.ensure_user(
        state._project_manager.db,
        telegram_user_id=int(user.id),
        username=str(getattr(user, "username", "") or ""),
        first_name=str(getattr(user, "first_name", "") or ""),
        last_name=str(getattr(user, "last_name", "") or ""),
    )


async def _create_new_conversation(update: Update, *, title: str | None = None) -> dict:
    """
    Create new conversation.
    
    Purpose:
    - Implement `_create_new_conversation` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `title`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `dict` when available; otherwise side effects only.
    """

    from db import store

    user_row = await _ensure_memory_user_row(update)
    conv_title = (title or "").strip() or datetime.now(timezone.utc).strftime("Conversation %Y-%m-%d %H:%M")
    created = await store.create_conversation_session(
        state._project_manager.db,
        user_id=int(user_row["id"]),
        title=conv_title,
    )
    await store.set_user_active_conversation(
        state._project_manager.db,
        user_id=int(user_row["id"]),
        conversation_id=created["conversation_id"],
    )
    return created


async def _build_conversation_picker_markup(update: Update) -> InlineKeyboardMarkup:
    """
    Build conversation picker markup.
    
    Purpose:
    - Implement `_build_conversation_picker_markup` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `InlineKeyboardMarkup` when available; otherwise side effects only.
    """

    from db import store

    user_row = await _ensure_memory_user_row(update)
    rows = await store.list_conversation_sessions(
        state._project_manager.db,
        user_id=int(user_row["id"]),
        limit=12,
    )
    buttons: list[list[InlineKeyboardButton]] = []
    for row in rows:
        title = str(row.get("title") or "Conversation").strip() or "Conversation"
        updated = str(row.get("updated_at") or "").strip()
        label = title
        if updated:
            label = f"{title} - {updated[:16].replace('T', ' ')}"
        callback_data = f"{_CONV_SWITCH_PREFIX}{row['conversation_id']}"
        buttons.append([InlineKeyboardButton(label[:64], callback_data=callback_data)])

    if not buttons:
        buttons.append([InlineKeyboardButton("Start New Conversation", callback_data=_CONV_START_NEW)])
    return InlineKeyboardMarkup(buttons)


# ------------------------------------------------------------------
# Approval request (called by the orchestrator worker for git_push etc.)
# ------------------------------------------------------------------

async def request_worker_approval(
    project_id: str, action: str, params: dict,
) -> bool:
    """
    Called by the worker when it needs individual Telegram approval
    (e.g., for git_push, gh_create_repo).

    Sends an Approve/Deny message to Telegram and blocks until the
    user responds.
    """
    if cfg.AUTO_APPROVE_GIT_ACTIONS and action in {"git_push", "gh_create_repo"}:
        await _send_to_user(
            f"[AUTO-APPROVED] {html.escape(action)} for project {html.escape(project_id)}",
        )
        return True

    state._approval_counter += 1
    key = f"wa{state._approval_counter}"

    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state._pending_approvals[key] = future

    param_summary = "\n".join(f"  {k}: <code>{html.escape(str(v))}</code>" for k, v in params.items())
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Approve", callback_data=f"wapprove:{key}"),
        InlineKeyboardButton("Deny", callback_data=f"wdeny:{key}"),
    ]])
    await state._bot_app.bot.send_message(
        chat_id=cfg.ALLOWED_USER_ID,
        text=(
            f"<b>APPROVAL NEEDED</b> -- {html.escape(action)}\n"
            f"{param_summary}\n\n"
            f"Approve this action?"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    try:
        return await asyncio.wait_for(future, timeout=300)
    except asyncio.TimeoutError:
        state._pending_approvals.pop(key, None)
        await _send_to_user(f"<b>TIMEOUT</b> -- {html.escape(action)} approval expired.")
        return False


# ------------------------------------------------------------------
# Callback handler for inline buttons
# ------------------------------------------------------------------


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle callback.
    
    Purpose:
    - Implement `handle_callback` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    query = update.callback_query
    user = update.effective_user
    if not user or user.id != cfg.ALLOWED_USER_ID:
        await query.answer("Unauthorized.")
        return
    await query.answer()
    data = query.data or ""

    if data == _CONV_START_NEW:
        try:
            created = await _create_new_conversation(update)
            await query.edit_message_text(
                (
                    f"Started new conversation: <b>{html.escape(created.get('title') or 'Conversation')}</b>\n"
                    "Context is now isolated to this conversation."
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            await query.edit_message_text(f"Error: {exc}")
        return

    if data == _CONV_CONTINUE:
        try:
            markup = await _build_conversation_picker_markup(update)
            await query.edit_message_text(
                "Select a conversation to continue:",
                reply_markup=markup,
            )
        except Exception as exc:
            await query.edit_message_text(f"Error: {exc}")
        return

    if data.startswith(_CONV_SWITCH_PREFIX):
        from db import store

        conversation_id = data[len(_CONV_SWITCH_PREFIX):]
        try:
            user_row = await _ensure_memory_user_row(update)
            target = await store.get_conversation_session(
                state._project_manager.db,
                conversation_id=conversation_id,
            )
            if not target or int(target.get("user_id") or -1) != int(user_row["id"]):
                await query.edit_message_text("Conversation not found.")
                return
            await store.set_user_active_conversation(
                state._project_manager.db,
                user_id=int(user_row["id"]),
                conversation_id=conversation_id,
            )
            await query.edit_message_text(
                f"Switched to conversation: <b>{html.escape(str(target.get('title') or 'Conversation'))}</b>",
                parse_mode="HTML",
            )
        except Exception as exc:
            await query.edit_message_text(f"Error: {exc}")
        return

    # --- v1 CONFIRM action approval ---
    if data.startswith("approve:"):
        key = data[8:]
        pending = state._pending_confirms.pop(key, None)
        if not pending:
            await query.edit_message_text("Action expired or already handled.")
            return
        await query.edit_message_text(
            f"<b>APPROVED</b> -- executing {html.escape(pending['action'])} ...",
            parse_mode="HTML",
        )
        try:
            result = await _send_action(pending["action"], pending["params"], confirmed=True)
            await query.message.reply_text(_format_result(result), parse_mode="HTML")
        except Exception as exc:
            await query.message.reply_text(f"Error: {exc}")

    elif data.startswith("deny:"):
        key = data[5:]
        pending = state._pending_confirms.pop(key, None)
        action_name = pending["action"] if pending else "unknown"
        await query.edit_message_text(
            f"<b>DENIED</b> -- {html.escape(action_name)} was not executed.",
            parse_mode="HTML",
        )

    # --- Worker approval (git_push, gh_create_repo) ---
    elif data.startswith("wapprove:"):
        key = data[9:]
        future = state._pending_approvals.pop(key, None)
        if future and not future.done():
            future.set_result(True)
        await query.edit_message_text("<b>APPROVED</b>", parse_mode="HTML")

    elif data.startswith("wdeny:"):
        key = data[6:]
        future = state._pending_approvals.pop(key, None)
        if future and not future.done():
            future.set_result(False)
        await query.edit_message_text("<b>DENIED</b>", parse_mode="HTML")

    # --- Plan approval ---
    elif data.startswith("approve_plan:"):
        project_id = data[13:]
        try:
            await state._project_manager.approve_plan(project_id)
            await state._project_manager.start_execution(project_id)
            if state._conversation_engine:
                await state._conversation_engine.clear_pending_action_for_user(str(user.id))
            elif state._orchestrator:
                await state._orchestrator.clear_pending_action_for_user(str(user.id))
            await query.edit_message_text(
                "<b>Plan APPROVED</b> -- coding started!", parse_mode="HTML",
            )
        except Exception as exc:
            await query.edit_message_text(f"Error: {exc}")

    elif data.startswith("cancel_plan:"):
        project_id = data[12:]
        try:
            await state._project_manager.cancel_project(project_id)
            if state._conversation_engine:
                await state._conversation_engine.clear_pending_action_for_user(str(user.id))
            elif state._orchestrator:
                await state._orchestrator.clear_pending_action_for_user(str(user.id))
            await query.edit_message_text("<b>Plan CANCELLED</b>", parse_mode="HTML")
        except Exception as exc:
            await query.edit_message_text(f"Error: {exc}")

    elif data.startswith("confirm_remove_project:"):
        key = data[len("confirm_remove_project:"):]
        pending = state._pending_project_removals.pop(key, None)
        if not pending:
            await query.edit_message_text("Removal request expired or already handled.")
            return

        project_id = pending.get("project_id", "")
        display_name = pending.get("display_name", "project")
        try:
            removed = await state._project_manager.remove_project(project_id)
            local_path = str(removed.get("local_path") or pending.get("local_path") or "").strip()
            note = (
                f"\nWorkspace files kept at: <code>{html.escape(local_path)}</code>"
                if local_path else ""
            )
            await query.edit_message_text(
                f"<b>Removed</b> project <b>{html.escape(display_name)}</b>.{note}",
                parse_mode="HTML",
            )
        except Exception as exc:
            await query.edit_message_text(f"Error removing project: {exc}")

    elif data.startswith("cancel_remove_project:"):
        key = data[len("cancel_remove_project:"):]
        pending = state._pending_project_removals.pop(key, None)
        display_name = html.escape(pending.get("display_name", "project")) if pending else "project"
        await query.edit_message_text(
            f"Deletion cancelled for <b>{display_name}</b>.",
            parse_mode="HTML",
        )


# ------------------------------------------------------------------
# v2 Project commands
# ------------------------------------------------------------------


async def cmd_newproject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd newproject.
    
    Purpose:
    - Implement `cmd_newproject` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /newproject <name>\nExample: /newproject habit-tracker")
        return
    name = " ".join(context.args)
    await _create_project_from_name(update, name)


async def cmd_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd idea.
    
    Purpose:
    - Implement `cmd_idea` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /idea <text>")
        return
    await _capture_idea(update, " ".join(context.args).strip())


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd plan.
    
    Purpose:
    - Implement `cmd_plan` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return

    # Find the project to plan.
    if context.args:
        from db import store
        project = await store.get_project_by_name(state._project_manager.db, context.args[0])
    else:
        project = await state._project_manager.get_ideation_project()

    if not project:
        await update.message.reply_text("No project found in ideation status. Use /newproject first.")
        return

    project_name = _project_display(project)
    await update.message.reply_text(
        (
            f"Plan generation queued for <b>{html.escape(project_name)}</b>.\n"
            "I will post styled progress updates in chat."
        ),
        parse_mode="HTML",
    )

    async def _bg_cmd_plan() -> None:
        """
        Bg cmd plan.
        
        Purpose:
        - Implement `_bg_cmd_plan` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        await _notify_styled(
            "progress",
            "Plan Generation",
            "Started from /plan command.",
            project=project_name,
        )
        try:
            plan = await state._project_manager.generate_plan(project["id"])
            milestones = plan.get("milestones", [])
            top = [str(ms.get("name", "")).strip() for ms in milestones if ms.get("name")]
            top_text = ", ".join(top[:4]) if top else "No milestones listed."

            if cfg.AUTO_APPROVE_AND_START:
                await state._project_manager.approve_plan(project["id"])
                await state._project_manager.start_execution(project["id"])
                await _notify_styled(
                    "success",
                    "Plan Generation",
                    (
                        "Plan generated and auto-started.\n"
                        f"Summary: {plan.get('summary', '')}\n"
                        f"Top milestones: {top_text}"
                    ),
                    project=project_name,
                )
                return

            await _notify_styled(
                "success",
                "Plan Generation",
                (
                    "Plan generated and waiting for approval.\n"
                    f"Summary: {plan.get('summary', '')}\n"
                    f"Top milestones: {top_text}"
                ),
                project=project_name,
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Approve", callback_data=f"approve_plan:{project['id']}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel_plan:{project['id']}"),
            ]])
            if state._bot_app and state._bot_app.bot:
                await state._bot_app.bot.send_message(
                    chat_id=cfg.ALLOWED_USER_ID,
                    text=f"<b>Plan approval needed</b> for <b>{html.escape(project_name)}</b>.",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
        except Exception as exc:
            await _notify_styled("error", "Plan Generation", f"Failed: {exc}", project=project_name)

    _spawn_background_task(
        _bg_cmd_plan(),
        tag=f"cmd-plan-{project['id']}-{uuid.uuid4().hex[:8]}",
    )


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd projects.
    
    Purpose:
    - Implement `cmd_projects` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        projects = await state._project_manager.list_projects()
        if not projects:
            await update.message.reply_text("No projects yet. Use /newproject to start one.")
            return

        status_icons = {
            "ideation": "ðŸ’¡", "planning": "ðŸ“", "approved": "âœ…",
            "coding": "âš™ï¸", "testing": "ðŸ§ª", "completed": "ðŸŽ‰",
            "paused": "â¸ï¸", "failed": "âŒ", "cancelled": "ðŸ›‘",
        }
        lines = ["<b>Projects:</b>\n"]
        for p in projects:
            icon = status_icons.get(p["status"], "ðŸ“‹")
            lines.append(
                f"{icon} <b>{html.escape(p['display_name'])}</b> â€” {p['status']}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_project_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd project status.
    
    Purpose:
    - Implement `cmd_project_status` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /status <project-name>")
        return

    from db import store
    project = await store.get_project_by_name(state._project_manager.db, context.args[0])
    if not project:
        # Fall back to agent status if not a project name.
        try:
            result = await _gateway_get("/status")
            connected = result.get("agent_connected", False)
            icon = "CONNECTED" if connected else "NOT CONNECTED"
            await update.message.reply_text(f"Agent: <b>{icon}</b>", parse_mode="HTML")
        except Exception as exc:
            await update.message.reply_text(f"Gateway unreachable: {exc}")
        return

    try:
        status = await state._project_manager.get_status(project["id"])
        p = status["project"]
        lines = [
            f"<b>{html.escape(p['display_name'])}</b>",
            f"Status: {p['status']}",
            f"Progress: {status['progress']} ({status['percent']}%)",
        ]
        if status["current_task"]:
            lines.append(f"Current: {html.escape(status['current_task'])}")
        if p.get("github_repo"):
            lines.append(f"GitHub: {html.escape(p['github_repo'])}")
        if status["recent_events"]:
            lines.append("\n<b>Recent:</b>")
            for e in status["recent_events"][:5]:
                lines.append(f"  {html.escape(e['summary'])}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd pause.
    
    Purpose:
    - Implement `cmd_pause` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /pause <project-name>")
        return
    from db import store
    project = await store.get_project_by_name(state._project_manager.db, context.args[0])
    if not project:
        await update.message.reply_text("Project not found.")
        return
    try:
        await state._project_manager.pause_project(project["id"])
        await update.message.reply_text(f"Paused: <b>{html.escape(project['display_name'])}</b>", parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_resume_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd resume project.
    
    Purpose:
    - Implement `cmd_resume_project` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /resume_project <project-name>")
        return
    from db import store
    project = await store.get_project_by_name(state._project_manager.db, context.args[0])
    if not project:
        await update.message.reply_text("Project not found.")
        return
    try:
        await state._project_manager.resume_project(project["id"])
        await update.message.reply_text(f"Resumed: <b>{html.escape(project['display_name'])}</b>", parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd cancel.
    
    Purpose:
    - Implement `cmd_cancel` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /cancel <project-name>")
        return
    from db import store
    project = await store.get_project_by_name(state._project_manager.db, context.args[0])
    if not project:
        await update.message.reply_text("Project not found.")
        return
    try:
        await state._project_manager.cancel_project(project["id"])
        await update.message.reply_text(f"Cancelled: <b>{html.escape(project['display_name'])}</b>", parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_remove_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd remove project.
    
    Purpose:
    - Implement `cmd_remove_project` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    project_ref = " ".join(context.args).strip() if context.args else ""
    project, error = await _resolve_project(project_ref or None)
    if error:
        usage = "Usage: /removeproject <project-name>"
        if not project_ref:
            await update.message.reply_text(f"{usage}\nOr mention the project in natural language.")
        else:
            await update.message.reply_text(error)
        return
    await _ask_remove_project_confirmation(update, project)



async def cmd_quota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd quota.
    
    Purpose:
    - Implement `cmd_quota` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not state._provider_router:
        await update.message.reply_text("AI providers not configured.")
        return
    try:
        summary = await state._provider_router.get_quota_summary()
        lines = ["<b>AI Provider Quota:</b>\n"]
        for p in summary:
            status = "âœ…" if p["available"] else "âŒ"
            limit = p["daily_limit"] or "âˆž"
            lines.append(
                f"{status} <b>{html.escape(p['provider'])}</b> ({p['model']})\n"
                f"    {p['daily_used']}/{limit} requests today"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ------------------------------------------------------------------
# Persona memory commands
# ------------------------------------------------------------------


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd profile.
    
    Purpose:
    - Implement `cmd_profile` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        summary = await _format_profile_summary(update)
        await update.message.reply_text(summary, parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd forget.
    
    Purpose:
    - Implement `cmd_forget` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /forget <fact key or text>")
        return
    target = " ".join(context.args).strip()
    try:
        await update.message.reply_text(await _forget_profile_target(update, target))
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_no_store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd no store.
    
    Purpose:
    - Implement `cmd_no_store` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        await update.message.reply_text(
            await _set_memory_enabled_for_user(
                update,
                enabled=False,
                reason="Disabled by user command.",
            )
        )
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_store_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd store on.
    
    Purpose:
    - Implement `cmd_store_on` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        await update.message.reply_text(
            await _set_memory_enabled_for_user(
                update,
                enabled=True,
                reason="Enabled by user command.",
            )
        )
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd start.
    
    Purpose:
    - Implement `cmd_start` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Continue Conversation", callback_data=_CONV_CONTINUE)],
        [InlineKeyboardButton("Start New Conversation", callback_data=_CONV_START_NEW)],
    ])
    await update.message.reply_text(
        "<b>Select an option:</b>\n"
        "Use explicit conversations to keep context isolated.\n\n"
        "You can still use slash commands for direct actions (/newproject, /plan, /status, etc.).",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def cmd_agent_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd agent status.
    
    Purpose:
    - Implement `cmd_agent_status` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        result = await _gateway_get("/status")
        execution_mode = str(result.get("execution_mode", "")).strip().lower()
        if execution_mode == "ssh_tunnel":
            ssh_enabled = result.get("ssh_fallback_enabled", False)
            ssh_healthy = result.get("ssh_fallback_healthy", False)
            ssh_target = result.get("ssh_fallback_target", "")
            if ssh_enabled:
                status = "SSH Tunnel Ready" if ssh_healthy else "SSH Tunnel Configured (unreachable)"
                msg = f"Execution: <b>{status}</b>\nMode: <code>ssh_tunnel (forced)</code>"
                if ssh_target:
                    msg += f"\nTarget: <code>{html.escape(str(ssh_target))}</code>"
                await update.message.reply_text(msg, parse_mode="HTML")
                return

        connected = result.get("agent_connected", False)
        if connected:
            await update.message.reply_text("Execution: <b>Worker Connected</b>", parse_mode="HTML")
            return

        ssh_enabled = result.get("ssh_fallback_enabled", False)
        ssh_healthy = result.get("ssh_fallback_healthy", False)
        ssh_target = result.get("ssh_fallback_target", "")
        if ssh_enabled:
            status = "SSH Tunnel Ready" if ssh_healthy else "SSH Tunnel Configured (unreachable)"
            msg = f"Execution: <b>{status}</b>"
            if ssh_target:
                msg += f"\nTarget: <code>{html.escape(str(ssh_target))}</code>"
            await update.message.reply_text(msg, parse_mode="HTML")
            return

        await update.message.reply_text("Execution: <b>No worker and no SSH fallback</b>", parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Gateway unreachable: {exc}")



async def cmd_git_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd git status.
    
    Purpose:
    - Implement `cmd_git_status` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    path = _parse_path(context.args)
    await update.message.reply_text(f"Running git_status on <code>{html.escape(path)}</code> ...", parse_mode="HTML")
    try:
        result = await _send_action("git_status", {"working_dir": path}, confirmed=True)
        await update.message.reply_text(_format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_run_tests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd run tests.
    
    Purpose:
    - Implement `cmd_run_tests` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    path = _parse_path(context.args)
    runner = context.args[1] if context.args and len(context.args) > 1 else "pytest"
    await update.message.reply_text(f"Running tests ({runner}) ...", parse_mode="HTML")
    try:
        result = await _send_action("run_tests", {"working_dir": path, "runner": runner}, confirmed=True)
        await update.message.reply_text(_format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_lint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd lint.
    
    Purpose:
    - Implement `cmd_lint` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    path = _parse_path(context.args)
    linter = context.args[1] if context.args and len(context.args) > 1 else "ruff"
    try:
        result = await _send_action("lint_project", {"working_dir": path, "linter": linter}, confirmed=True)
        await update.message.reply_text(_format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd build.
    
    Purpose:
    - Implement `cmd_build` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    path = _parse_path(context.args)
    tool = context.args[1] if context.args and len(context.args) > 1 else "npm"
    try:
        result = await _send_action("build_project", {"working_dir": path, "build_tool": tool}, confirmed=True)
        await update.message.reply_text(_format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_vscode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd vscode.
    
    Purpose:
    - Implement `cmd_vscode` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /vscode <path>")
        return
    path = " ".join(context.args).strip()
    await _ask_confirm(
        update,
        "open_in_vscode",
        {"path": path},
        f"Path: <code>{html.escape(path)}</code>",
    )


async def cmd_check_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd check agents.
    
    Purpose:
    - Implement `cmd_check_agents` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        result = await _send_action("check_coding_agents", {}, confirmed=True)
        await update.message.reply_text(_format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_run_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd run agent.
    
    Purpose:
    - Implement `cmd_run_agent` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /run_agent <codex|claude|cline> [path=<dir>] <prompt>",
        )
        return

    agent = context.args[0].strip().lower()
    if agent not in {"codex", "claude", "cline"}:
        await update.message.reply_text("Agent must be one of: codex, claude, cline")
        return

    working_dir = cfg.PROJECT_BASE_DIR or cfg.DEFAULT_WORKING_DIR
    prompt_start_index = 1
    if len(context.args) >= 3 and context.args[1].startswith("path="):
        working_dir = context.args[1][len("path="):].strip() or working_dir
        prompt_start_index = 2

    prompt = " ".join(context.args[prompt_start_index:]).strip()
    if not prompt:
        await update.message.reply_text(
            "Usage: /run_agent <codex|claude|cline> [path=<dir>] <prompt>",
        )
        return

    await _ask_confirm(
        update,
        "run_coding_agent",
        {"agent": agent, "prompt": prompt, "working_dir": working_dir},
        (
            f"Agent: <code>{html.escape(agent)}</code>\n"
            f"Path: <code>{html.escape(working_dir)}</code>\n"
            f"Prompt: <i>{html.escape(prompt)}</i>"
        ),
    )


async def cmd_cline_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd cline provider.
    
    Purpose:
    - Implement `cmd_cline_provider` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /cline_provider <gemini|deepseek|groq|openrouter|openai|anthropic> [model]",
        )
        return
    provider = context.args[0].strip().lower()
    if provider not in {"gemini", "deepseek", "groq", "openrouter", "openai", "anthropic"}:
        await update.message.reply_text(
            "Provider must be one of: gemini, deepseek, groq, openrouter, openai, anthropic.",
        )
        return
    model = " ".join(context.args[1:]).strip()
    params = {"agent": "cline", "provider": provider}
    if model:
        params["model"] = model
    try:
        result = await _send_action("configure_coding_agent", params, confirmed=True)
        await update.message.reply_text(_format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_git_commit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd git commit.
    
    Purpose:
    - Implement `cmd_git_commit` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(f"Usage: /git_commit [path] [message]")
        return
    path = context.args[0]
    message = " ".join(context.args[1:])
    await _ask_confirm(update, "git_commit", {"working_dir": path, "message": message},
                       f"Path: <code>{html.escape(path)}</code>\nMessage: <i>{html.escape(message)}</i>")


async def cmd_install_deps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd install deps.
    
    Purpose:
    - Implement `cmd_install_deps` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    path = _parse_path(context.args)
    manager = context.args[1] if context.args and len(context.args) > 1 else "pip"
    await _ask_confirm(update, "install_dependencies", {"working_dir": path, "manager": manager},
                       f"Path: <code>{html.escape(path)}</code>\nManager: {html.escape(manager)}")


async def cmd_close_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd close app.
    
    Purpose:
    - Implement `cmd_close_app` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /close_app [name]")
        return
    app_name = context.args[0].lower()
    await _ask_confirm(update, "close_app", {"app": app_name},
                       f"Application: <code>{html.escape(app_name)}</code>")


async def cmd_emergency_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd emergency stop.
    
    Purpose:
    - Implement `cmd_emergency_stop` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    # Cancel all running projects.
    if state._project_manager and state._project_manager.scheduler:
        count = state._project_manager.scheduler.cancel_all()
        if count:
            await update.message.reply_text(f"Cancelled {count} running project(s).")
    try:
        result = await _gateway_post("/emergency-stop")
        await update.message.reply_text(
            f"EMERGENCY STOP sent.\nResponse: <code>{html.escape(json.dumps(result))}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd resume.
    
    Purpose:
    - Implement `cmd_resume` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        result = await _gateway_post("/resume")
        await update.message.reply_text(
            f"Resume sent.\nResponse: <code>{html.escape(json.dumps(result))}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ------------------------------------------------------------------
# SKYNET system commands
# ------------------------------------------------------------------


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd agents.
    
    Purpose:
    - Implement `cmd_agents` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        from db import store
        from agents.roles import AGENT_CONFIGS
        if context.args:
            project = await store.get_project_by_name(state._project_manager.db, context.args[0])
            if not project:
                await update.message.reply_text("Project not found.")
                return
            agents = await store.list_agents(state._project_manager.db, project["id"])
            if not agents:
                await update.message.reply_text("No agents spawned for this project yet.")
                return
            lines = [f"<b>Agents for {html.escape(project['display_name'])}:</b>\n"]
            for a in agents:
                lines.append(
                    f"  {a['role']} â€” {a['status']} "
                    f"({a.get('tasks_completed', 0)} tasks)"
                )
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        else:
            lines = ["<b>Available Agent Roles:</b>\n"]
            for role, cfg_data in AGENT_CONFIGS.items():
                lines.append(f"  <b>{role}</b> â€” {html.escape(cfg_data['description'])}")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_heartbeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd heartbeat.
    
    Purpose:
    - Implement `cmd_heartbeat` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not state._heartbeat:
        await update.message.reply_text("Heartbeat scheduler not configured.")
        return
    status = state._heartbeat.get_status()
    if not status:
        await update.message.reply_text("No heartbeat tasks registered.")
        return
    lines = [
        f"<b>SKYNET Heartbeat</b> ({'running' if state._heartbeat.is_running else 'stopped'})\n",
    ]
    for t in status:
        enabled = "ON" if t["enabled"] else "OFF"
        next_in = int(t.get("next_run_in", 0))
        lines.append(
            f"  [{enabled}] <b>{html.escape(t['name'])}</b>\n"
            f"    {html.escape(t['description'])}\n"
            f"    Every {t['interval_seconds']}s | Runs: {t['run_count']} | Next: {next_in}s"
        )
        if t.get("last_error"):
            lines.append(f"    Last error: {html.escape(t['last_error'])}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_sentinel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd sentinel.
    
    Purpose:
    - Implement `cmd_sentinel` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    if not state._sentinel:
        await update.message.reply_text("Sentinel not configured.")
        return
    await update.message.reply_text("Running SKYNET Sentinel health checks...")
    try:
        statuses = await state._sentinel.run_all_checks()
        report = state._sentinel.format_report(statuses)
        await update.message.reply_text(
            f"<pre>{html.escape(report)}</pre>", parse_mode="HTML",
        )
    except Exception as exc:
        await update.message.reply_text(f"Sentinel error: {exc}")


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cmd skills.
    
    Purpose:
    - Implement `cmd_skills` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    try:
        if not state._skill_registry:
            await update.message.reply_text("Skill registry is not configured.")
            return
        rows = state._skill_registry.list_skills()
        if not rows:
            await update.message.reply_text("No skills are currently loaded.")
            return

        lines = ["<b>SKYNET Skills:</b>\n"]
        for row in sorted(rows, key=lambda r: (r.get("kind", "tool"), r["name"])):
            kind = row.get("kind", "tool")
            roles = ", ".join(row.get("allowed_roles", ["all"]))
            description = row.get("description", "")
            if kind == "prompt":
                src = row.get("source", "")
                lines.append(
                    f"  <b>{html.escape(row['name'])}</b> - {html.escape(description)}\n"
                    f"    Kind: prompt-only | Roles: {html.escape(roles)}\n"
                    f"    Source: <code>{html.escape(src)}</code>"
                )
            else:
                lines.append(
                    f"  <b>{html.escape(row['name'])}</b> - {html.escape(description)}\n"
                    f"    Kind: tools | Roles: {html.escape(roles)}"
                )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ------------------------------------------------------------------
# Plain text handler â€” natural conversation + intent extraction
# ------------------------------------------------------------------


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text.
    
    Purpose:
    - Implement `handle_text` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `update`: input used by this function to compute or route work.
    - `context`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    if not _authorised(update):
        return
    text = update.message.text.strip()
    if not text:
        return
    lowered = text.lower()

    if lowered in {"switch conversation", "switch conv", "continue conversation"}:
        try:
            markup = await _build_conversation_picker_markup(update)
            await update.message.reply_text(
                "Select a conversation to continue:",
                reply_markup=markup,
            )
        except Exception as exc:
            await update.message.reply_text(f"Error: {exc}")
        return

    if lowered in {"new conversation", "start new conversation"}:
        try:
            created = await _create_new_conversation(update)
            await update.message.reply_text(
                f"Started new conversation: <b>{html.escape(created.get('title') or 'Conversation')}</b>",
                parse_mode="HTML",
            )
        except Exception as exc:
            await update.message.reply_text(f"Error: {exc}")
        return

    # 1. Memory commands (/remember, /forget, etc.)
    if await _maybe_handle_memory_text_command(update, text):
        return

    # 2. Profile capture (background, non-blocking)
    # Skip for pure greetings to avoid unnecessary model calls on trivial turns.
    skip_store = _is_no_store_once_message(text)
    if not _is_pure_greeting(text):
        _spawn_background_task(
            _capture_profile_memory(
                update,
                text,
                skip_store=skip_store,
                persist_message=not bool(state._orchestrator),
            ),
            tag="profile-capture",
        )

    # 3. Route through conversation engine (Igris commander architecture).
    if state._conversation_engine:
        user = update.effective_user
        profile = {
            "username": getattr(user, "username", "") if user else "",
            "first_name": getattr(user, "first_name", "") if user else "",
            "last_name": getattr(user, "last_name", "") if user else "",
        }
        frame = inspect.currentframe()
        source_line = frame.f_lineno if frame is not None else 0
        del frame
        result = await state._conversation_engine.process_user_message(
            telegram_user_id=int(user.id),
            text=text,
            user_profile=profile,
            entrypoint="handle_text()",
            trace_origin={
                "source_file": "openclaw-gateway/bot/commands.py",
                "source_line": source_line,
                "source_function": "handle_text",
                "original_message": update.message.text,
                "normalized_message": text,
            },
        )
        if result.text:
            await update.message.reply_text(result.text)
        return

    # Backward compatibility fallback.
    if state._orchestrator:
        reply = await state._orchestrator.handle(update, text)
        if reply:
            await update.message.reply_text(reply)
        return

    await update.message.reply_text("Conversation engine is not configured.")


# ------------------------------------------------------------------
# Build the Telegram Application
# ------------------------------------------------------------------


def build_app() -> Application:
    """Create and configure the Telegram bot application."""
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Add it to your .env file or environment before starting the bot."
        )
    if not cfg.ALLOWED_USER_ID:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_USER_ID is not set. "
            "Add it to your .env file or environment before starting the bot."
        )

    app = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # v2 project commands.
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("newproject", cmd_newproject))
    app.add_handler(CommandHandler("idea", cmd_idea))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("status", cmd_project_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume_project", cmd_resume_project))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("removeproject", cmd_remove_project))
    app.add_handler(CommandHandler("quota", cmd_quota))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("no_store", cmd_no_store))
    app.add_handler(CommandHandler("store_on", cmd_store_on))

    # SKYNET system commands.
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("heartbeat", cmd_heartbeat))
    app.add_handler(CommandHandler("sentinel", cmd_sentinel))
    app.add_handler(CommandHandler("skills", cmd_skills))

    # v1 agent commands.
    app.add_handler(CommandHandler("agent_status", cmd_agent_status))
    app.add_handler(CommandHandler("git_status", cmd_git_status))
    app.add_handler(CommandHandler("run_tests", cmd_run_tests))
    app.add_handler(CommandHandler("lint", cmd_lint))
    app.add_handler(CommandHandler("build", cmd_build))
    app.add_handler(CommandHandler("vscode", cmd_vscode))
    app.add_handler(CommandHandler("check_agents", cmd_check_agents))
    app.add_handler(CommandHandler("run_agent", cmd_run_agent))
    app.add_handler(CommandHandler("cline_provider", cmd_cline_provider))
    app.add_handler(CommandHandler("git_commit", cmd_git_commit))
    app.add_handler(CommandHandler("install_deps", cmd_install_deps))
    app.add_handler(CommandHandler("close_app", cmd_close_app))
    app.add_handler(CommandHandler("emergency_stop", cmd_emergency_stop))
    app.add_handler(CommandHandler("resume", cmd_resume))

    # Inline buttons.
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Plain text â†’ idea capture.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    state._bot_app = app
    return app




