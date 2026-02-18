"""
Run SKYNET with Telegram Bot Interface.

This starts the SKYNET core system and connects it to Telegram
for user interaction.

Requirements:
- TELEGRAM_BOT_TOKEN in .env
- TELEGRAM_ALLOWED_USER_ID in .env
- python-telegram-bot package

Usage:
    python run_telegram.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from skynet.main import SkynetApp, setup_logging
from skynet.telegram import SkynetTelegramBot


async def main():
    """Start SKYNET with Telegram interface."""
    # Setup logging
    setup_logging("INFO")
    logger = logging.getLogger("skynet.run_telegram")

    # Load environment
    load_dotenv()

    # Check required environment variables
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")

    if not telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env")
        logger.info("Get a bot token from @BotFather on Telegram")
        return

    if not allowed_user_id:
        logger.error("TELEGRAM_ALLOWED_USER_ID not set in .env")
        logger.info("Set your Telegram user ID (get it from @userinfobot)")
        return

    try:
        allowed_user_id = int(allowed_user_id)
    except ValueError:
        logger.error("TELEGRAM_ALLOWED_USER_ID must be a number")
        return

    logger.info("=" * 70)
    logger.info("SKYNET - Starting with Telegram Interface")
    logger.info("=" * 70)

    # Initialize SKYNET
    logger.info("\n[1/2] Initializing SKYNET Core...")
    app = await SkynetApp.create()

    # Initialize Telegram bot
    logger.info("\n[2/2] Initializing Telegram Bot...")
    bot = SkynetTelegramBot(
        skynet_app=app,
        telegram_token=telegram_token,
        allowed_user_id=allowed_user_id,
    )

    # Start bot
    logger.info("\n" + "=" * 70)
    logger.info("SKYNET - Ready!")
    logger.info("=" * 70)
    logger.info("\nOpen Telegram and send /start to your bot")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 70 + "\n")

    try:
        await bot.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\nShutting down...")
        await bot.stop()
        await app.shutdown()
        logger.info("Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
