"""
Shared fixtures and sys.path bootstrap for SKYNET handler tests.
"""
import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Add the gateway package root so all bot.* / db.* / gateway imports resolve.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Gateway and worker test suites both expose a bare ``config`` module.
# Drop any previously imported non-gateway variant before gateway modules load.
_config_path = os.path.join(ROOT, "config.py")
_config_spec = importlib.util.spec_from_file_location("config", _config_path)
if _config_spec is None or _config_spec.loader is None:
    raise RuntimeError(f"Unable to load gateway config from {_config_path}")
_config_module = importlib.util.module_from_spec(_config_spec)
_config_spec.loader.exec_module(_config_module)
sys.modules["config"] = _config_module

# Provide required env vars before any config-dependent module is imported.
os.environ.setdefault("TELEGRAM_BOT_TOKEN",          "1234567890:test_token_for_ci")
os.environ.setdefault("SKYNET_AUTH_TOKEN",            "test-auth-token")
os.environ.setdefault("OPENCLAW_PROJECT_BASE_DIR",    "E:/SKYNET-SANDBOX/Projects")
os.environ.setdefault("SKYNET_ORCHESTRATION_MODE",    "legacy")
os.environ.setdefault("SKYNET_OPENCLAW_AGENT_HOSTING", "ec2_control")
os.environ.setdefault("SKYNET_E2E_LIVE",              "0")

# ── Shared mock-builder helpers (imported by test modules) ──────────────────

from unittest.mock import AsyncMock, MagicMock


def make_callback_update(data: str, *, user_id: int = 42, chat_id: int = 100):
    """Minimal Update mock for a button-tap (callback_query)."""
    update = MagicMock()
    update.effective_user.id         = user_id
    update.effective_user.username   = "skynet_test"
    update.effective_user.first_name = "Test"
    update.effective_user.last_name  = "User"
    update.effective_chat.id         = chat_id
    update.callback_query.data       = data
    update.callback_query.answer     = AsyncMock()
    update.callback_query.message.reply_text    = AsyncMock()
    update.callback_query.message.chat.send_action = AsyncMock()
    return update


def make_message_update(text: str, *, user_id: int = 42, chat_id: int = 100):
    """Minimal Update mock for a text message."""
    update = MagicMock()
    update.effective_user.id         = user_id
    update.effective_user.username   = "skynet_test"
    update.effective_user.first_name = "Test"
    update.effective_user.last_name  = "User"
    update.effective_chat.id         = chat_id
    update.message.text              = text
    update.message.reply_text        = AsyncMock()
    update.effective_message         = update.message
    update.effective_chat.send_action = AsyncMock()
    return update


def make_context(*, user_data=None, bot_data=None):
    """Minimal context mock."""
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot_data  = bot_data  if bot_data  is not None else {}
    return ctx
