"""
SKYNET Gateway — v1 Agent Command Handlers

Extracted from telegram_bot.py to keep the main module navigable.
These handlers are registered in telegram_bot.build_app().

Dependency pattern: helpers and shared state are accessed via a lazy
import of the parent telegram_bot module to avoid circular imports.
"""

from __future__ import annotations

import html
import json
import logging

import bot_config as cfg
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("skynet.telegram.cmd_agent")


def _b():
    """Return the telegram_bot module (cached after first call by Python's import system)."""
    import telegram_bot  # noqa: PLC0415 — lazy to avoid circular import
    return telegram_bot


# ------------------------------------------------------------------
# /start  /help
# ------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    await update.message.reply_text(
        "<b>SKYNET // CHATHAN - AI Project Factory</b>\n\n"
        "<b>Project Management:</b>\n"
        "  /newproject &lt;name&gt; - start a new project\n"
        "  (send text) - natural chat with SKYNET\n"
        "  /idea &lt;text&gt; - add idea to current project\n"
        "  /plan [name] - generate project plan\n"
        "  /projects - list all projects\n"
        "  /status &lt;name&gt; - project status\n"
        "  /pause &lt;name&gt; - pause project\n"
        "  /resume_project &lt;name&gt; - resume project\n"
        "  /cancel &lt;name&gt; - cancel project\n"
        "  /removeproject &lt;name&gt; - permanently remove project record (with Yes/No confirmation)\n"
        "  /quota - AI provider status\n\n"
        "<b>Persona Memory:</b>\n"
        "  /profile - show stored profile and preferences\n"
        "  /forget &lt;fact-or-text&gt; - forget matching stored facts\n"
        "  /no_store - stop storing new memory\n"
        "  /store_on - re-enable memory storage\n\n"
        "<b>SKYNET System:</b>\n"
        "  /agents [project] - list agents\n"
        "  /heartbeat - heartbeat task status\n"
        "  /sentinel - run health checks\n"
        "  /skills - list available skills\n\n"
        "<b>Agent Commands:</b>\n"
        "  /agent_status - agent connection check\n"
        "  /git_status [path]\n"
        "  /run_tests [path]\n"
        "  /lint [path]\n"
        "  /build [path]\n"
        "  /vscode <path> - open folder/file in VS Code on laptop\n"
        "  /check_agents - check codex/claude/cline CLI availability\n"
        "  /run_agent <agent> [path=<dir>] <prompt> - run coding agent\n"
        "  /cline_provider <provider> [model] - switch Cline provider/model\n"
        "  /close_app [name]\n\n"
        "<b>Controls:</b>\n"
        "  /emergency_stop - kill everything\n"
        "  /resume - resume agent\n",
        parse_mode="HTML",
    )


# ------------------------------------------------------------------
# /agent_status
# ------------------------------------------------------------------

async def cmd_agent_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    try:
        result = await _b()._gateway_get("/status")
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


# ------------------------------------------------------------------
# /git_status  /run_tests  /lint  /build
# ------------------------------------------------------------------

async def cmd_git_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    path = _b()._parse_path(context.args)
    await update.message.reply_text(
        f"Running git_status on <code>{html.escape(path)}</code> ...", parse_mode="HTML",
    )
    try:
        result = await _b()._send_action("git_status", {"working_dir": path}, confirmed=True)
        await update.message.reply_text(_b()._format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_run_tests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    path = _b()._parse_path(context.args)
    runner = context.args[1] if context.args and len(context.args) > 1 else "pytest"
    await update.message.reply_text(f"Running tests ({runner}) ...", parse_mode="HTML")
    try:
        result = await _b()._send_action("run_tests", {"working_dir": path, "runner": runner}, confirmed=True)
        await update.message.reply_text(_b()._format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_lint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    path = _b()._parse_path(context.args)
    linter = context.args[1] if context.args and len(context.args) > 1 else "ruff"
    try:
        result = await _b()._send_action("lint_project", {"working_dir": path, "linter": linter}, confirmed=True)
        await update.message.reply_text(_b()._format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    path = _b()._parse_path(context.args)
    tool = context.args[1] if context.args and len(context.args) > 1 else "npm"
    try:
        result = await _b()._send_action("build_project", {"working_dir": path, "build_tool": tool}, confirmed=True)
        await update.message.reply_text(_b()._format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ------------------------------------------------------------------
# /vscode  /check_agents  /run_agent  /cline_provider
# ------------------------------------------------------------------

async def cmd_vscode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /vscode <path>")
        return
    path = " ".join(context.args).strip()
    await _b()._ask_confirm(
        update,
        "open_in_vscode",
        {"path": path},
        f"Path: <code>{html.escape(path)}</code>",
    )


async def cmd_check_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    try:
        result = await _b()._send_action("check_coding_agents", {}, confirmed=True)
        await update.message.reply_text(_b()._format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_run_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
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

    await _b()._ask_confirm(
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
    if not _b()._authorised(update):
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
        result = await _b()._send_action("configure_coding_agent", params, confirmed=True)
        await update.message.reply_text(_b()._format_result(result), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ------------------------------------------------------------------
# /git_commit  /install_deps  /close_app
# ------------------------------------------------------------------

async def cmd_git_commit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /git_commit [path] [message]")
        return
    path = context.args[0]
    message = " ".join(context.args[1:])
    await _b()._ask_confirm(
        update,
        "git_commit",
        {"working_dir": path, "message": message},
        f"Path: <code>{html.escape(path)}</code>\nMessage: <i>{html.escape(message)}</i>",
    )


async def cmd_install_deps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    path = _b()._parse_path(context.args)
    manager = context.args[1] if context.args and len(context.args) > 1 else "pip"
    await _b()._ask_confirm(
        update,
        "install_dependencies",
        {"working_dir": path, "manager": manager},
        f"Path: <code>{html.escape(path)}</code>\nManager: {html.escape(manager)}",
    )


async def cmd_close_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /close_app [name]")
        return
    app_name = context.args[0].lower()
    await _b()._ask_confirm(
        update,
        "close_app",
        {"app": app_name},
        f"Application: <code>{html.escape(app_name)}</code>",
    )


# ------------------------------------------------------------------
# /emergency_stop  /resume
# ------------------------------------------------------------------

async def cmd_emergency_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    b = _b()
    if b._project_manager and b._project_manager.scheduler:
        count = b._project_manager.scheduler.cancel_all()
        if count:
            await update.message.reply_text(f"Cancelled {count} running project(s).")
    try:
        result = await b._gateway_post("/emergency-stop")
        await update.message.reply_text(
            f"EMERGENCY STOP sent.\nResponse: <code>{html.escape(json.dumps(result))}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _b()._authorised(update):
        return
    try:
        result = await _b()._gateway_post("/resume")
        await update.message.reply_text(
            f"Resume sent.\nResponse: <code>{html.escape(json.dumps(result))}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")
