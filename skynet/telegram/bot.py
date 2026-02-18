"""
SKYNET — Telegram Bot

Provides a user interface for SKYNET task orchestration via Telegram.

Commands:
- /start - Welcome message and help
- /task <description> - Create a new task
- /status [job_id] - Check job status
- /list - List recent jobs
- /cancel <job_id> - Cancel a job
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

if TYPE_CHECKING:
    from skynet.main import SkynetApp

logger = logging.getLogger("skynet.telegram.bot")


class SkynetTelegramBot:
    """
    Telegram bot interface for SKYNET.

    Provides commands for task management and displays plans for approval.

    Example:
        bot = SkynetTelegramBot(app, telegram_token, allowed_user_id)
        await bot.start()
    """

    def __init__(
        self,
        skynet_app: SkynetApp,
        telegram_token: str,
        allowed_user_id: int,
    ):
        """
        Initialize Telegram bot.

        Args:
            skynet_app: SKYNET application instance
            telegram_token: Telegram bot token
            allowed_user_id: Telegram user ID allowed to use the bot
        """
        self.app = skynet_app
        self.telegram_token = telegram_token
        self.allowed_user_id = allowed_user_id
        self.telegram_app: Application | None = None

        # Store pending approvals: job_id -> (user_id, chat_id)
        self.pending_approvals: dict[str, tuple[int, int]] = {}

        # Conversation history for context
        self.conversation_history: list[dict[str, str]] = []

        # SKYNET Personality
        self.personality = """You are SKYNET, an autonomous task orchestration AI assistant.

Personality traits:
- Professional yet friendly and approachable
- Confident in your capabilities but not arrogant
- Helpful and proactive
- Slightly playful with tech references
- Safety-conscious (you always validate risky operations)
- You speak naturally and conversationally

Your capabilities:
- You can execute tasks on the user's computer safely
- You use AI to plan complex multi-step operations
- You validate and approve tasks based on risk level
- You have access to multiple execution providers (local, docker, SSH, etc.)

Communication style:
- Use natural language, not robotic
- Be concise but informative
- Use emojis occasionally for friendliness
- Ask clarifying questions when needed
- Offer helpful suggestions

When users chat with you:
- Respond conversationally
- If they describe a task, offer to help execute it
- If they ask about your capabilities, explain what you can do
- If they seem unsure, guide them
- Keep responses concise (2-3 sentences usually)
"""

        logger.info(f"Telegram bot initialized (allowed_user: {allowed_user_id})")

    def _is_authorized(self, update: Update) -> bool:
        """Check if user is authorized."""
        user = update.effective_user
        if user and user.id == self.allowed_user_id:
            return True
        logger.warning(f"Unauthorized access attempt from user {user.id if user else 'unknown'}")
        return False

    # Command Handlers
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not self._is_authorized(update):
            await update.message.reply_text("Unauthorized access.")
            return

        help_text = """🤖 *SKYNET - Autonomous Task Orchestration*

I can help you execute tasks safely with AI-powered planning and approval workflows.

*Commands:*
• /task - Create a new task
• /status - Check job status
• /list - List your recent jobs
• /cancel - Cancel a job
• /help - Show this message

*Example:*
/task Check git status and list modified files

Just chat with me naturally, or use /task for specific jobs. I'll generate a plan and execute it after approval!
"""
        await update.message.reply_text(help_text)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        await self.cmd_start(update, context)

    async def cmd_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /task command."""
        if not self._is_authorized(update):
            await update.message.reply_text("Unauthorized access.")
            return

        # Get task description from command args
        if not context.args:
            await update.message.reply_text(
                "Usage: /task <description>\n\n"
                "Example: /task Check git status and list modified files"
            )
            return

        user_intent = " ".join(context.args)

        try:
            # Create task
            await update.message.reply_text(f"Creating task: {user_intent}...")
            job_id = await self.app.create_task(user_intent, project_id="telegram")

            # Generate plan
            await update.message.reply_text("Generating plan...")
            plan = await self.app.generate_plan(job_id)

            # Get job status for approval info
            status = await self.app.get_status(job_id)

            # Format plan for display
            plan_text = self._format_plan(plan, status)

            # Send plan with approval buttons if needed
            if status["approval_required"]:
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{job_id}"),
                        InlineKeyboardButton("❌ Deny", callback_data=f"deny:{job_id}"),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    plan_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                )

                # Store for approval tracking
                self.pending_approvals[job_id] = (update.effective_user.id, update.effective_chat.id)

            else:
                # Auto-approve READ_ONLY tasks
                await update.message.reply_text(plan_text, parse_mode="Markdown")
                await self.app.approve_plan(job_id)
                await update.message.reply_text(
                    f"✅ Auto-approved (READ_ONLY)\n"
                    f"Job ID: `{job_id}`\n"
                    f"Status: QUEUED",
                    parse_mode="Markdown",
                )

        except Exception as e:
            logger.error(f"Error in cmd_task: {e}")
            await update.message.reply_text(f"Error: {str(e)}")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not self._is_authorized(update):
            await update.message.reply_text("Unauthorized access.")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /status <job_id>\n\n"
                "Example: /status job_abc123"
            )
            return

        job_id = context.args[0]

        try:
            status = await self.app.get_status(job_id)
            status_text = self._format_status(status)
            await update.message.reply_text(status_text, parse_mode="Markdown")

        except ValueError as e:
            await update.message.reply_text(f"Error: {str(e)}")
        except Exception as e:
            logger.error(f"Error in cmd_status: {e}")
            await update.message.reply_text(f"Error: {str(e)}")

    async def cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /list command."""
        if not self._is_authorized(update):
            await update.message.reply_text("Unauthorized access.")
            return

        try:
            jobs = await self.app.list_jobs(project_id="telegram")

            if not jobs:
                await update.message.reply_text("No jobs found.")
                return

            # Show last 10 jobs
            jobs_text = "**Recent Jobs:**\n\n"
            for job in jobs[:10]:
                jobs_text += f"• `{job['id']}` - {job['status']}\n"
                jobs_text += f"  {job['user_intent'][:50]}...\n\n"

            await update.message.reply_text(jobs_text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error in cmd_list: {e}")
            await update.message.reply_text(f"Error: {str(e)}")

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel command."""
        if not self._is_authorized(update):
            await update.message.reply_text("Unauthorized access.")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /cancel <job_id>\n\n"
                "Example: /cancel job_abc123"
            )
            return

        job_id = context.args[0]

        try:
            await self.app.cancel_job(job_id)
            await update.message.reply_text(f"✅ Job {job_id} cancelled")

        except ValueError as e:
            await update.message.reply_text(f"Error: {str(e)}")
        except Exception as e:
            logger.error(f"Error in cmd_cancel: {e}")
            await update.message.reply_text(f"Error: {str(e)}")

    async def handle_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle conversational messages (non-commands)."""
        if not self._is_authorized(update):
            await update.message.reply_text("Unauthorized access.")
            return

        user_message = update.message.text
        logger.info(f"Received message: {user_message}")

        # Add to conversation history
        self.conversation_history.append({"role": "user", "message": user_message})

        # Keep only last 10 messages for context
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]

        try:
            # Generate AI response using Gemini
            response = await self._generate_ai_response(user_message)
            await update.message.reply_text(response)

            # Add response to history
            self.conversation_history.append({"role": "assistant", "message": response})

        except Exception as e:
            logger.error(f"Error in conversation: {e}")
            await update.message.reply_text(
                "Sorry, I encountered an error. Try using a command like /help or /task instead!"
            )

    async def _generate_ai_response(self, user_message: str) -> str:
        """Generate conversational response using Gemini AI."""
        import os
        from google import genai
        from google.genai import types

        # Configure API key from environment
        api_key = os.getenv("GOOGLE_AI_API_KEY")
        if not api_key:
            return "Sorry, I'm having trouble connecting to my AI backend. Please check the configuration!"

        # Initialize client (same as Planner)
        client = genai.Client(api_key=api_key)

        # Build context from recent conversation
        context = ""
        for msg in self.conversation_history[-6:]:  # Last 3 exchanges
            role = "User" if msg["role"] == "user" else "SKYNET"
            context += f"{role}: {msg['message']}\n"

        # Create prompt with personality and context
        prompt = f"""{self.personality}

Recent conversation:
{context}

User's latest message: {user_message}

Respond as SKYNET. If the user is describing a task they want done, offer to help by suggesting they use the /task command with their request. Keep your response natural, friendly, and concise (2-4 sentences max).

Your response:"""

        # Generate response using the same approach as Planner
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=1024,
                temperature=0.8,
            ),
        )

        return response.text.strip()

    # Callback Handler
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button callbacks."""
        query = update.callback_query
        await query.answer()

        if not self._is_authorized(update):
            await query.edit_message_text("Unauthorized access.")
            return

        # Parse callback data: "approve:job_id" or "deny:job_id"
        action, job_id = query.data.split(":", 1)

        try:
            if action == "approve":
                await self.app.approve_plan(job_id)
                await query.edit_message_text(
                    f"{query.message.text}\n\n"
                    f"✅ **APPROVED**\n"
                    f"Job ID: `{job_id}`\n"
                    f"Status: QUEUED",
                    parse_mode="Markdown",
                )

                # Remove from pending approvals
                if job_id in self.pending_approvals:
                    del self.pending_approvals[job_id]

            elif action == "deny":
                await self.app.deny_plan(job_id, reason="User denied via Telegram")
                await query.edit_message_text(
                    f"{query.message.text}\n\n"
                    f"❌ **DENIED**\n"
                    f"Job ID: `{job_id}`\n"
                    f"Status: CANCELLED",
                    parse_mode="Markdown",
                )

                # Remove from pending approvals
                if job_id in self.pending_approvals:
                    del self.pending_approvals[job_id]

        except Exception as e:
            logger.error(f"Error in handle_callback: {e}")
            await query.edit_message_text(f"Error: {str(e)}")

    # Helper Methods
    def _format_plan(self, plan: dict, status: dict) -> str:
        """Format plan for Telegram display."""
        text = f"**📋 Plan Generated**\n\n"
        text += f"**Intent:** {status['user_intent']}\n\n"
        text += f"**Summary:** {plan.get('summary', 'N/A')}\n\n"
        text += f"**Risk Level:** {status['risk_level']}\n"
        text += f"**Approval Required:** {'Yes' if status['approval_required'] else 'No'}\n\n"

        text += f"**Steps ({len(plan.get('steps', []))}):**\n"
        for i, step in enumerate(plan.get("steps", []), 1):
            title = step.get("title", "Unknown")
            risk = step.get("risk_level", "N/A")
            text += f"{i}. {title} `[{risk}]`\n"

        if plan.get("expected_artifacts"):
            text += f"\n**Expected Artifacts:**\n"
            for artifact in plan["expected_artifacts"]:
                text += f"• {artifact}\n"

        return text

    def _format_status(self, status: dict) -> str:
        """Format job status for Telegram display."""
        text = f"**Job Status**\n\n"
        text += f"**Job ID:** `{status['id']}`\n"
        text += f"**Status:** {status['status']}\n"
        text += f"**Intent:** {status['user_intent']}\n\n"
        text += f"**Risk Level:** {status['risk_level']}\n"
        text += f"**Created:** {status['created_at']}\n"

        if status.get("approved_at"):
            text += f"**Approved:** {status['approved_at']}\n"
        if status.get("queued_at"):
            text += f"**Queued:** {status['queued_at']}\n"
        if status.get("error_message"):
            text += f"\n**Error:** {status['error_message']}\n"

        return text

    # Lifecycle Methods
    async def start(self) -> None:
        """Start the Telegram bot."""
        logger.info("Starting Telegram bot...")

        # Build application
        self.telegram_app = Application.builder().token(self.telegram_token).build()

        # Add command handlers
        self.telegram_app.add_handler(CommandHandler("start", self.cmd_start))
        self.telegram_app.add_handler(CommandHandler("help", self.cmd_help))
        self.telegram_app.add_handler(CommandHandler("task", self.cmd_task))
        self.telegram_app.add_handler(CommandHandler("status", self.cmd_status))
        self.telegram_app.add_handler(CommandHandler("list", self.cmd_list))
        self.telegram_app.add_handler(CommandHandler("cancel", self.cmd_cancel))

        # Add callback handler for inline buttons
        self.telegram_app.add_handler(CallbackQueryHandler(self.handle_callback))

        # Add message handler for conversational AI (must be last, catches all non-command text)
        self.telegram_app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_conversation)
        )

        # Start polling
        logger.info("Telegram bot ready")
        await self.telegram_app.initialize()
        await self.telegram_app.start()
        await self.telegram_app.updater.start_polling()

        logger.info("Telegram bot started successfully")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self.telegram_app:
            logger.info("Stopping Telegram bot...")
            await self.telegram_app.updater.stop()
            await self.telegram_app.stop()
            await self.telegram_app.shutdown()
            logger.info("Telegram bot stopped")

    async def run_forever(self) -> None:
        """Run the bot until stopped."""
        await self.start()
        # Keep running
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            await self.stop()
