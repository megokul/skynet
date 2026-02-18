"""Test Telegram bot initialization (without actual Telegram connection)."""

import asyncio
import os
import sys
from pathlib import Path

# Add skynet to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from skynet.main import SkynetApp, setup_logging
from skynet.telegram import SkynetTelegramBot

# Load .env
load_dotenv()


async def main():
    print("=" * 60)
    print("SKYNET Telegram Bot Test (Initialization Only)")
    print("=" * 60)
    print()

    # Setup logging
    setup_logging("INFO")

    # Test 1: Initialize SKYNET
    print("[1] Initializing SKYNET...")
    api_key = os.getenv("GOOGLE_AI_API_KEY")
    if not api_key:
        print("[ERROR] GOOGLE_AI_API_KEY not set in .env")
        return

    app = await SkynetApp.create(api_key=api_key)
    print("[SUCCESS] SKYNET initialized")
    print()

    # Test 2: Initialize Telegram Bot (without starting)
    print("[2] Testing Telegram Bot initialization...")

    # Use dummy values for testing (won't actually connect)
    test_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    test_user_id = 12345678

    try:
        bot = SkynetTelegramBot(
            skynet_app=app,
            telegram_token=test_token,
            allowed_user_id=test_user_id,
        )
        print("[SUCCESS] Telegram bot initialized")
        print(f"  - Token: {test_token[:20]}...")
        print(f"  - Allowed User ID: {test_user_id}")
        print()

        # Test 3: Verify bot has expected methods
        print("[3] Verifying bot methods...")
        methods = ["cmd_start", "cmd_help", "cmd_task", "cmd_status", "cmd_list", "cmd_cancel"]
        for method in methods:
            if hasattr(bot, method):
                print(f"  [OK] {method}")
            else:
                print(f"  [MISSING] {method}")
        print()

        # Test 4: Check pending approvals dictionary
        print("[4] Checking bot state...")
        assert isinstance(bot.pending_approvals, dict), "pending_approvals should be a dict"
        print("  [OK] pending_approvals initialized")
        print()

    except Exception as e:
        print(f"[ERROR] {e}")
        return

    # Test 5: Shutdown
    print("[5] Shutting down...")
    await app.shutdown()
    print("[SUCCESS] Shutdown complete")
    print()

    print("=" * 60)
    print("[SUCCESS] Telegram bot tests passed!")
    print("=" * 60)
    print()
    print("NOTE: This test only checks initialization.")
    print("To test actual Telegram functionality:")
    print("  1. Get a bot token from @BotFather")
    print("  2. Set TELEGRAM_BOT_TOKEN in .env")
    print("  3. Set TELEGRAM_ALLOWED_USER_ID in .env")
    print("  4. Run: python run_telegram.py")
    print()


if __name__ == "__main__":
    asyncio.run(main())
