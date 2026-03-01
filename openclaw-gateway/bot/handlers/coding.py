"""
SKYNET Bot — Coding Orchestration

Handles the full coding loop after a project is saved:
  1. User taps "Start Coding"
  2. Bot asks about GitHub repo / project folder setup (buttons)
  3. User confirms → background asyncio.Task starts
  4. Loop: LLM breaks plan into milestones → user approves each → CLAW worker executes
  5. Progress notifications after each milestone
  6. /status command shows live dashboard

Key design: _coding_loop runs as a background asyncio.Task.
Milestone approvals are signalled via asyncio.Event stored in bot_data.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import config as cfg
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import (
    CB_CODING_GITHUB_SKIP,
    CB_CODING_GITHUB_YES,
    coding_github_setup,
    main_menu,
    milestone_review,
    run_project,
)
from bot.state import KEY_DB, KEY_ROUTER
from db.store import (
    create_task,
    ensure_user,
    get_project,
    list_projects,
    list_tasks,
    update_task_status,
)
from gateway import is_agent_connected, send_action

logger = logging.getLogger("skynet.bot.coding")

# bot_data keys for inter-handler signalling
_MS_EVENT_KEY    = "ms_event_{uid}"
_MS_DECISION_KEY = "ms_decision_{uid}"
_ACTIVE_LOOP_KEY = "coding_loop_{uid}"   # stores the asyncio.Task

# User_data key set by project handler after save
_PROJECT_ID_KEY = "last_project_id"
_CODING_PID_KEY = "coding_project_id"


# ── Entry: Start Coding ───────────────────────────────────────────────────────

async def start_coding_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped 🚀 Start Coding — ask GitHub/folder setup preference."""
    await update.callback_query.answer()

    project_id = context.user_data.get(_PROJECT_ID_KEY)
    if not project_id:
        await update.callback_query.message.reply_text(
            "No active project found. Start a project first.",
            reply_markup=main_menu(),
        )
        return

    context.user_data[_CODING_PID_KEY] = project_id

    await update.callback_query.message.reply_text(
        "Should I set up a GitHub repo and project folder on your laptop?",
        reply_markup=coding_github_setup(),
    )


async def coding_github_choice_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User chose GitHub setup option — spin up the background coding loop."""
    await update.callback_query.answer()

    cb_data    = update.callback_query.data or ""
    project_id = context.user_data.pop(_CODING_PID_KEY, None)
    user_id    = update.effective_user.id
    chat_id    = update.effective_chat.id

    if not project_id:
        await update.callback_query.message.reply_text("Session expired — start over.")
        return

    db = context.bot_data.get(KEY_DB)
    project = await get_project(db, project_id)
    if not project:
        await update.callback_query.message.reply_text("Project not found in database.")
        return

    # If already running, don't double-start.
    loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
    existing = context.bot_data.get(loop_key)
    if existing and not existing.done():
        await update.callback_query.message.reply_text(
            "A coding session is already running for you!"
        )
        return

    do_github = (cb_data == CB_CODING_GITHUB_YES)

    slug = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"

    await update.callback_query.message.reply_text(
        "Starting coding session…\n"
        f"📁 Project folder: <code>{working_dir}</code>\n\n"
        "I'll send you each milestone for approval before executing. "
        "Use /status anytime to check progress.",
        parse_mode="HTML",
    )

    task = asyncio.create_task(
        _coding_loop(context.application, chat_id, user_id, project, do_github)
    )
    context.bot_data[loop_key] = task


# ── Milestone approval callbacks ─────────────────────────────────────────────

async def approve_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped ✅ Run It — signal the coding loop to proceed."""
    await update.callback_query.answer("Running…")
    user_id  = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "approve"
        event.set()
    else:
        await update.callback_query.message.reply_text(
            "No active milestone waiting for approval."
        )


async def skip_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped ⏭ Skip — signal the coding loop to skip this milestone."""
    await update.callback_query.answer("Skipping…")
    user_id   = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "skip"
        event.set()


async def stop_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped 🛑 Stop Session — signal the coding loop to abort."""
    await update.callback_query.answer("Stopping…")
    user_id   = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "stop"
        event.set()
    else:
        await update.callback_query.message.reply_text(
            "No active coding session to stop."
        )


# ── Dashboard ─────────────────────────────────────────────────────────────────

async def dashboard_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/status — show the latest project's task progress."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    db      = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user

    user = await ensure_user(
        db,
        telegram_user_id=tg_user.id,
        username=tg_user.username    or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name   or "",
    )
    projects = await list_projects(db, user_id=user["id"])
    if not projects:
        await update.message.reply_text(
            "No projects yet. Tap 🚀 Start a Project to begin.",
            reply_markup=main_menu(),
        )
        return

    project = projects[0]  # most recent
    tasks   = await list_tasks(db, project_id=project["id"])

    STATUS_EMOJI = {
        "pending": "⏳",
        "running": "⚙️",
        "done":    "✅",
        "failed":  "❌",
    }

    if tasks:
        task_lines = "\n".join(
            f"{STATUS_EMOJI.get(t['status'], '❓')} {t['title']}"
            for t in tasks
        )
    else:
        task_lines = "No tasks yet — coding hasn't started."

    loop_key   = _ACTIVE_LOOP_KEY.format(uid=tg_user.id)
    is_running = (
        loop_key in context.bot_data
        and context.bot_data[loop_key]
        and not context.bot_data[loop_key].done()
    )
    status_note = " | 🔄 Coding in progress" if is_running else ""

    slug        = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"

    text = (
        f"<b>📊 {project['name']}</b> — {project['project_type']}\n"
        f"📁 <code>{working_dir}</code>\n"
        f"Status: {project['status']}{status_note}\n\n"
        f"{task_lines}"
    )

    # Show Run Project button if coding is done and a project_id is stored.
    run_pid = context.bot_data.get(f"run_project_{tg_user.id}")
    if run_pid and not is_running:
        keyboard = run_project()
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:main_menu")],
        ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ── Background coding loop ────────────────────────────────────────────────────

async def _coding_loop(
    app,
    chat_id: int,
    user_id: int,
    project: dict,
    do_github: bool,
) -> None:
    """
    Background task: orchestrate milestone-by-milestone project execution.

    1. (Optional) Set up GitHub repo + project folder on CLAW worker.
    2. Extract milestones from the stored plan via LLM.
    3. For each milestone:
       a. Send to user with ✅ Run It / ⏭ Skip buttons.
       b. Wait up to 1 h for user decision.
       c. If approved: dispatch run_coding_agent to CLAW worker.
       d. Notify user of result.
    4. Send completion message.
    """
    db     = app.bot_data.get(KEY_DB)
    router = app.bot_data.get(KEY_ROUTER)
    slug   = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"

    try:
        # ── Always create the project folder on the worker ────────────────────
        if is_agent_connected():
            try:
                await send_action(
                    "create_directory",
                    {"directory": working_dir},
                    confirmed=True,
                )
            except Exception:
                pass  # Directory may already exist — not fatal.
        else:
            await app.bot.send_message(
                chat_id, "⚠️ Worker not connected — cannot create project folder."
            )
            return

        # ── Optional GitHub setup ──────────────────────────────────────────────
        if do_github:
            await app.bot.send_message(chat_id, "🔧 Setting up GitHub repo and project folder…")
            try:
                # Step 1: init git so gh_create_repo has a repo to push.
                init_result = await send_action(
                    "git_init",
                    {"working_dir": working_dir},
                    confirmed=True,
                )
                if init_result.get("status") == "error":
                    raise RuntimeError(init_result.get("error", "git init failed"))

                # Step 2: create GitHub repo and push.
                gh_result = await send_action(
                    "gh_create_repo",
                    {
                        "working_dir": working_dir,
                        "repo_name":   slug,
                        "description": f"Created by SKYNET — {project['project_type']}",
                        "private":     True,
                    },
                    timeout=120,
                    confirmed=True,
                )
                if gh_result.get("status") == "error":
                    raise RuntimeError(gh_result.get("error", "Unknown error"))
                await app.bot.send_message(chat_id, "✅ GitHub repo created.")
            except Exception as exc:
                await app.bot.send_message(
                    chat_id, f"⚠️ GitHub setup failed: {exc}\nContinuing anyway…"
                )

        # ── Extract milestones from plan ──────────────────────────────────────
        await app.bot.send_message(chat_id, "📋 Breaking the plan into milestones…")
        milestones = await _extract_milestones(router, project)
        total = len(milestones)

        if not milestones:
            await app.bot.send_message(
                chat_id,
                "Could not extract milestones from the plan. "
                "Please refine your plan and try again.",
                reply_markup=main_menu(),
            )
            return

        await app.bot.send_message(
            chat_id, f"Found <b>{total} milestone(s)</b>. Let's go!", parse_mode="HTML"
        )

        # ── Milestone loop ────────────────────────────────────────────────────
        for i, milestone_text in enumerate(milestones, 1):
            # Show milestone to user.
            await app.bot.send_message(
                chat_id,
                f"<b>Milestone {i}/{total}</b>\n\n{milestone_text}",
                parse_mode="HTML",
                reply_markup=milestone_review(),
            )

            # Wait for user decision (up to 1 hour).
            event = asyncio.Event()
            event_key    = _MS_EVENT_KEY.format(uid=user_id)
            decision_key = _MS_DECISION_KEY.format(uid=user_id)
            app.bot_data[event_key] = event
            app.bot_data.pop(decision_key, None)

            try:
                await asyncio.wait_for(event.wait(), timeout=3600)
            except asyncio.TimeoutError:
                await app.bot.send_message(
                    chat_id, f"⏰ Milestone {i} timed out — skipping."
                )
                app.bot_data.pop(event_key, None)
                continue

            app.bot_data.pop(event_key, None)
            decision = app.bot_data.pop(decision_key, "skip")

            if decision == "stop":
                await app.bot.send_message(
                    chat_id,
                    f"🛑 Session stopped at milestone {i}/{total}.\n"
                    "Use /status to review completed milestones.",
                )
                return

            if decision == "skip":
                await app.bot.send_message(chat_id, f"⏭ Milestone {i} skipped.")
                continue

            # Create DB task record.
            short_title = milestone_text[:80].split("\n")[0]
            task_rec = await create_task(
                db,
                project_id=project["id"],
                title=f"Milestone {i}: {short_title}",
                description=milestone_text,
            )
            await update_task_status(db, task_rec["id"], status="running")
            await app.bot.send_message(chat_id, f"⚙️ Executing milestone {i}…")

            # Dispatch to CLAW worker.
            if not is_agent_connected():
                await app.bot.send_message(
                    chat_id, "⚠️ Worker disconnected — cannot execute. Skipping."
                )
                await update_task_status(
                    db, task_rec["id"],
                    status="failed", error_message="Agent not connected",
                )
                continue

            prompt = (
                f"Project: {project['name']} ({project['project_type']})\n"
                f"Working directory: {working_dir}\n\n"
                f"Task:\n{milestone_text}\n\n"
                "Implement this task completely. Write all necessary files, "
                "then run tests if applicable."
            )
            try:
                result = await send_action(
                    "run_coding_agent",
                    {
                        "agent":       "cline",
                        "prompt":      prompt,
                        "working_dir": working_dir,
                    },
                    timeout=1800,
                    confirmed=True,
                )
                # Worker wraps result: {"status": "success"/"error", "result": {...}}
                if result.get("status") == "error":
                    raise RuntimeError(result.get("error", "run_coding_agent failed"))
                inner   = result.get("result", result)
                summary = (inner.get("stdout") or inner.get("stderr") or "")[:500].strip()
                await update_task_status(
                    db, task_rec["id"], status="done", result_summary=summary
                )
                notice = f"✅ Milestone {i} complete!"
                if summary:
                    notice += f"\n\n{summary}"
                await app.bot.send_message(chat_id, notice)

            except Exception as exc:
                err = str(exc)[:300]
                await update_task_status(
                    db, task_rec["id"], status="failed", error_message=err
                )
                await app.bot.send_message(
                    chat_id, f"❌ Milestone {i} failed:\n<code>{err}</code>",
                    parse_mode="HTML",
                )

        # ── Done ──────────────────────────────────────────────────────────────
        app.bot_data[f"run_project_{user_id}"] = project["id"]
        await app.bot.send_message(
            chat_id,
            f"🎉 <b>{project['name']}</b> coding session complete!\n"
            f"📁 <code>{working_dir}</code>\n\n"
            "Use /status to review milestones or run the project now.",
            parse_mode="HTML",
            reply_markup=run_project(),
        )

    except Exception:
        logger.exception("Coding loop crashed for project %s user %s", project["id"], user_id)
        await app.bot.send_message(
            chat_id,
            "An unexpected error occurred in the coding loop. "
            "Use /status to see what was completed.",
            reply_markup=main_menu(),
        )


# ── Run Project ───────────────────────────────────────────────────────────────

async def run_project_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped ▶️ Run Project — execute the project script on the CLAW worker."""
    await update.callback_query.answer()

    user_id = update.effective_user.id
    db      = context.bot_data.get(KEY_DB)

    # Prefer the project from the last coding session; fallback to most recent.
    pid_key    = f"run_project_{user_id}"
    project_id = context.bot_data.get(pid_key)
    project    = None
    if project_id:
        project = await get_project(db, project_id)

    if not project:
        tg_user  = update.effective_user
        user     = await ensure_user(
            db,
            telegram_user_id=tg_user.id,
            username=tg_user.username    or "",
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name   or "",
        )
        projects = await list_projects(db, user_id=user["id"])
        project  = projects[0] if projects else None

    if not project:
        await update.callback_query.message.reply_text(
            "No project found to run.", reply_markup=main_menu()
        )
        return

    if not is_agent_connected():
        await update.callback_query.message.reply_text(
            "⚠️ Worker not connected — can't run the project right now.",
            reply_markup=run_project(),
        )
        return

    slug        = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"
    script      = f"{slug}.py"

    await update.callback_query.message.reply_text(
        f"▶️ Running <code>{script}</code> on your laptop…",
        parse_mode="HTML",
    )

    try:
        result = await send_action(
            "exec_command",
            {"command": f"python {script}", "working_dir": working_dir},
            timeout=60,
            confirmed=True,
        )
        # The gateway wraps the worker's response: {"status": "success", "result": {...}}
        inner     = result.get("result", result)
        stdout    = (inner.get("stdout") or "").strip()
        stderr    = (inner.get("stderr") or "").strip()
        exit_code = inner.get("returncode", inner.get("exit_code", 0))

        output = (stdout or stderr or "(no output)")[:1000]
        status_line = (
            f"✅ Finished (exit {exit_code})"
            if exit_code == 0
            else f"❌ Exited with code {exit_code}"
        )
        await update.callback_query.message.reply_text(
            f"<pre>{output}</pre>\n\n{status_line}",
            parse_mode="HTML",
            reply_markup=run_project(),
        )
    except Exception as exc:
        await update.callback_query.message.reply_text(
            f"❌ Run failed: {str(exc)[:300]}",
            reply_markup=run_project(),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _extract_milestones(router, project: dict) -> list[str]:
    """
    Ask the LLM to extract an ordered list of coding milestones from the plan.
    Returns a list of milestone description strings.
    """
    plan = project.get("description", "")
    if not plan:
        return []

    system = (
        "You are a project planner. Extract the coding milestones from the project plan "
        "as a JSON array of strings. Each element is ONE self-contained coding task "
        "(e.g. 'Set up project structure', 'Implement login endpoint'). "
        "Output ONLY a valid JSON array, no extra text."
    )
    messages = [
        {
            "role": "user",
            "content": f"Project: {project['name']}\n\nPlan:\n{plan}\n\n"
                       "Return the milestones as a JSON array of strings.",
        }
    ]

    try:
        response = await router.chat(
            messages=messages,
            system=system,
            max_tokens=1024,
            task_type="planning",
        )
        raw = (response.text or "").strip()
        # Strip markdown code fences if present.
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        milestones = json.loads(raw)
        if isinstance(milestones, list) and all(isinstance(m, str) for m in milestones):
            return [m.strip() for m in milestones if m.strip()]
    except Exception:
        logger.warning("JSON milestone extraction failed — falling back to line parsing")

    # Fallback: split on numbered list items (1. ... 2. ...)
    return _parse_milestones_fallback(plan)


def _parse_milestones_fallback(plan: str) -> list[str]:
    """Extract numbered list items from free-form plan text."""
    pattern = re.compile(r"^\s*\d+\.\s+(.+)", re.MULTILINE)
    matches = pattern.findall(plan)
    return [m.strip() for m in matches if m.strip()]


def _slugify(name: str) -> str:
    """Convert a project name to a safe directory/repo slug."""
    slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "project"
