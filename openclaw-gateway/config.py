"""
SKYNET Gateway — Unified Configuration

Single source of truth for all environment variables.
Replaces the old gateway_config.py + bot_config.py split.

Required env vars (bot fails fast without these):
    TELEGRAM_BOT_TOKEN
    SKYNET_AUTH_TOKEN

Optional but recommended:
    TELEGRAM_ALLOWED_USER_ID   (0 = allow any user)
    At least one AI provider key (GOOGLE_AI_API_KEY, GROQ_API_KEY, etc.)
"""
from __future__ import annotations

import os

_here = os.path.dirname(os.path.abspath(__file__))


def _s(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _i(name: str, default: int = 0) -> int:
    try:
        return int((os.environ.get(name) or "").strip())
    except (ValueError, AttributeError):
        return default


def _b(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _s("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID: int    = _i("TELEGRAM_ALLOWED_USER_ID", 0)   # 0 = allow all

# ── Worker WebSocket server ───────────────────────────────────────────────────
AUTH_TOKEN: str             = _s("SKYNET_AUTH_TOKEN") or _s("OPENCLAW_AUTH_TOKEN")  # fallback for existing EC2 env
WS_HOST: str                = _s("SKYNET_WS_HOST", "0.0.0.0")
WS_PORT: int                = _i("SKYNET_WS_PORT", 8765)
WS_PING_INTERVAL: int       = _i("SKYNET_WS_PING_INTERVAL", 20)
WS_PING_TIMEOUT: int        = _i("SKYNET_WS_PING_TIMEOUT", 10)
ACTION_TIMEOUT_SECONDS: int = _i("SKYNET_ACTION_TIMEOUT", 120)

# ── TLS ───────────────────────────────────────────────────────────────────────
TLS_CERT: str = _s("SKYNET_TLS_CERT", "")
TLS_KEY: str  = _s("SKYNET_TLS_KEY", "")

# ── HTTP API (loopback only) ──────────────────────────────────────────────────
HTTP_HOST: str = _s("SKYNET_HTTP_HOST", "127.0.0.1")
HTTP_PORT: int = _i("SKYNET_HTTP_PORT", 8766)

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH: str = _s("SKYNET_DB_PATH", os.path.join(_here, "data", "skynet.db"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = _s("SKYNET_LOG_LEVEL", "INFO")
LOG_DIR: str   = _s("SKYNET_LOG_DIR", os.path.join(_here, "logs"))

# SSH log mirror (optional — set all three to enable)
LOG_ENABLE_SSH_MIRROR: bool = _b("SKYNET_LOG_SSH_MIRROR")
LOG_SSH_HOST: str           = _s("SKYNET_LOG_SSH_HOST")
LOG_SSH_PORT: int           = _i("SKYNET_LOG_SSH_PORT", 22)
LOG_SSH_USER: str           = _s("SKYNET_LOG_SSH_USER")
LOG_SSH_KEY_PATH: str       = _s("SKYNET_LOG_SSH_KEY_PATH")
LOG_SSH_PASSWORD: str       = _s("SKYNET_LOG_SSH_PASSWORD")
LOG_SSH_STRICT_HOST_KEY: bool = _b("SKYNET_LOG_SSH_STRICT_HOST_KEY")
LOG_SSH_CONNECT_TIMEOUT: int  = _i("SKYNET_LOG_SSH_CONNECT_TIMEOUT", 10)
LOG_SSH_COMMAND_TIMEOUT: int  = _i("SKYNET_LOG_SSH_COMMAND_TIMEOUT", 30)
LOG_ENABLE_LOCAL_FILES: bool  = _b("SKYNET_LOG_LOCAL_FILES", True)
LOG_MAX_BYTES: int            = _i("SKYNET_LOG_MAX_BYTES", 10_485_760)   # 10 MB
LOG_BACKUP_COUNT: int         = _i("SKYNET_LOG_BACKUP_COUNT", 5)
TRACE_MIRROR_LOG_DIR: str     = _s("SKYNET_TRACE_MIRROR_LOG_DIR", "")

# ── AI Providers ──────────────────────────────────────────────────────────────
GOOGLE_AI_API_KEY: str          = _s("GOOGLE_AI_API_KEY")
GEMINI_MODEL: str               = _s("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_ONLY_MODE: bool          = _b("GEMINI_ONLY_MODE")
GROQ_API_KEY: str               = _s("GROQ_API_KEY")
OPENROUTER_API_KEY: str         = _s("OPENROUTER_API_KEY")
OPENROUTER_MODEL: str           = _s("OPENROUTER_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free")
OPENROUTER_FALLBACK_MODELS: str = _s("OPENROUTER_FALLBACK_MODELS", "")
DEEPSEEK_API_KEY: str           = _s("DEEPSEEK_API_KEY")
OPENAI_API_KEY: str             = _s("OPENAI_API_KEY")
ANTHROPIC_API_KEY: str          = _s("ANTHROPIC_API_KEY")
OLLAMA_DEFAULT_MODEL: str       = _s("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:32b-instruct-q4_K_M")
AI_PROVIDER_PRIORITY: str       = _s(
    "AI_PROVIDER_PRIORITY",
    "ollama,gemini,claude,openai,deepseek,openrouter,groq",
)

# ── Web Search ────────────────────────────────────────────────────────────────
BRAVE_SEARCH_API_KEY: str = _s("BRAVE_SEARCH_API_KEY")

# ── GitHub ─────────────────────────────────────────────────────────────────────
GITHUB_PAT: str      = _s("GITHUB_PAT")       # Personal Access Token (repo scope)
GITHUB_USERNAME: str = _s("GITHUB_USERNAME")  # Owner username for repo creation

# ── Worker ────────────────────────────────────────────────────────────────────
# Read OPENCLAW_PROJECT_BASE_DIR first (set by CI/CD), fall back to
# WORKER_PROJECTS_DIR for manual overrides, then a safe default.
WORKER_PROJECTS_DIR: str = _s(
    "OPENCLAW_PROJECT_BASE_DIR",
    _s("WORKER_PROJECTS_DIR", "C:/Projects"),
)
