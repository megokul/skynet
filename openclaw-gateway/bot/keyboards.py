"""
SKYNET Bot — Keyboard Layouts

Single source of truth for every InlineKeyboardMarkup and callback data string.
Add new keyboards here; import constants elsewhere to avoid magic strings.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ── Callback data constants ───────────────────────────────────────────────────
# Format: "<namespace>:<action>"  — keeps patterns unambiguous.
CB_START_PROJECT = "menu:start_project"
CB_WEATHER       = "menu:weather"
CB_REMINDER      = "menu:reminder"
CB_WEB_SEARCH    = "menu:web_search"
CB_CHAT          = "menu:chat"

CB_MY_PROJECTS   = "nav:my_projects"
CB_MAIN_MENU     = "nav:main_menu"

CB_TYPE_WEB      = "type:web_app"
CB_TYPE_MOBILE   = "type:mobile"
CB_TYPE_CLI      = "type:cli"
CB_TYPE_LIBRARY  = "type:library"
CB_TYPE_BOT      = "type:bot"
CB_TYPE_OTHER    = "type:other"

CB_PLAN_APPROVE  = "plan:approve"
CB_PLAN_CHANGES  = "plan:changes"

CB_GITHUB_YES    = "github:yes"
CB_GITHUB_NO     = "github:no"

# Human-readable label for each project type callback
PROJECT_TYPE_LABELS: dict[str, str] = {
    CB_TYPE_WEB:     "Web App",
    CB_TYPE_MOBILE:  "Mobile",
    CB_TYPE_CLI:     "CLI",
    CB_TYPE_LIBRARY: "Library",
    CB_TYPE_BOT:     "Bot",
    CB_TYPE_OTHER:   "Other",
}


# ── Keyboard builders ─────────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    """Top-level feature menu — shown on every greeting."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start a Project", callback_data=CB_START_PROJECT)],
        [
            InlineKeyboardButton("🌤 Weather",    callback_data=CB_WEATHER),
            InlineKeyboardButton("⏰ Reminder",   callback_data=CB_REMINDER),
        ],
        [
            InlineKeyboardButton("🔍 Web Search", callback_data=CB_WEB_SEARCH),
            InlineKeyboardButton("💬 Chat",        callback_data=CB_CHAT),
        ],
    ])


def project_type() -> InlineKeyboardMarkup:
    """Project type selection — shown during project creation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Web App",  callback_data=CB_TYPE_WEB),
            InlineKeyboardButton("📱 Mobile",   callback_data=CB_TYPE_MOBILE),
        ],
        [
            InlineKeyboardButton("🖥 CLI",      callback_data=CB_TYPE_CLI),
            InlineKeyboardButton("📦 Library",  callback_data=CB_TYPE_LIBRARY),
        ],
        [
            InlineKeyboardButton("🤖 Bot",      callback_data=CB_TYPE_BOT),
            InlineKeyboardButton("❓ Other",    callback_data=CB_TYPE_OTHER),
        ],
    ])


def after_project_created() -> InlineKeyboardMarkup:
    """Shown after a project is successfully saved."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 My Projects", callback_data=CB_MY_PROJECTS),
            InlineKeyboardButton("🏠 Main Menu",   callback_data=CB_MAIN_MENU),
        ],
    ])


def back_to_main() -> InlineKeyboardMarkup:
    """Minimal back button used on 'Coming soon' and error screens."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data=CB_MAIN_MENU)],
    ])


def plan_review() -> InlineKeyboardMarkup:
    """Shown after the Project Specialist generates a plan."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve",           callback_data=CB_PLAN_APPROVE),
            InlineKeyboardButton("✏️ Request Changes",   callback_data=CB_PLAN_CHANGES),
        ],
    ])


def github_choice() -> InlineKeyboardMarkup:
    """Ask the user whether to create a GitHub repo."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, create repo", callback_data=CB_GITHUB_YES),
            InlineKeyboardButton("⬛ No thanks",         callback_data=CB_GITHUB_NO),
        ],
    ])
