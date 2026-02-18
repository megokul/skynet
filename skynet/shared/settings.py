"""
SKYNET — Shared Settings

Central configuration for both gateway and worker components.
Load from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from typing import Optional


# =============================================================================
# Project Identity
# =============================================================================
PROJECT_NAME: str = "SKYNET"
CODENAME: str = "CHATHAN"
VERSION: str = "1.0.0"


# =============================================================================
# Network Configuration
# =============================================================================
# Gateway WebSocket (worker connections)
WS_HOST: str = os.environ.get("SKYNET_WS_HOST", "0.0.0.0")
WS_PORT: int = int(os.environ.get("SKYNET_WS_PORT", "8765"))

# Gateway HTTP API (internal)
HTTP_HOST: str = os.environ.get("SKYNET_HTTP_HOST", "127.0.0.1")
HTTP_PORT: int = int(os.environ.get("SKYNET_HTTP_PORT", "8766"))

# Worker connection
GATEWAY_URL: str = os.environ.get(
    "SKYNET_GATEWAY_URL",
    os.environ.get("OPENCLAW_GATEWAY_URL", "wss://100.50.2.232:8765/agent/ws"),
)


# =============================================================================
# Security
# =============================================================================
AUTH_TOKEN: str = os.environ.get(
    "SKYNET_AUTH_TOKEN",
    os.environ.get("OPENCLAW_AUTH_TOKEN", ""),
)

# Telegram configuration
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID: int = int(os.environ.get("TELEGRAM_ALLOWED_USER_ID", "0"))


# =============================================================================
# AI Providers
# =============================================================================
GOOGLE_AI_API_KEY: str = os.environ.get("GOOGLE_AI_API_KEY", "")
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# Local Ollama
OLLAMA_DEFAULT_MODEL: str = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:7b")


# =============================================================================
# Storage
# =============================================================================
# Database
DB_PATH: str = os.environ.get(
    "SKYNET_DB_PATH",
    os.environ.get("OPENCLAW_DB_PATH", "data/skynet.db"),
)

# S3 Artifact storage
S3_BUCKET: str = os.environ.get("SKYNET_S3_BUCKET", "openclaw-artifacts")
S3_PREFIX: str = os.environ.get("SKYNET_S3_PREFIX", "openclaw/")
AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")

# Local paths
PROJECT_BASE_DIR: str = os.environ.get(
    "SKYNET_PROJECT_BASE_DIR",
    os.environ.get("OPENCLAW_PROJECT_BASE_DIR", "E:/OpenClaw/projects"),
)
DEFAULT_WORKING_DIR: str = os.environ.get("SKYNET_DEFAULT_WORKING_DIR", "E:/MyProjects/clawd-sandbox")


# =============================================================================
# External Services
# =============================================================================
BRAVE_SEARCH_API_KEY: str = os.environ.get("BRAVE_SEARCH_API_KEY", "")
GITHUB_PAT: str = os.environ.get("GITHUB_PAT", "")
GITHUB_USERNAME: str = os.environ.get("GITHUB_USERNAME", "")


# =============================================================================
# Timeouts
# =============================================================================
ACTION_TIMEOUT_SECONDS: int = int(os.environ.get("SKYNET_ACTION_TIMEOUT", "120"))
WS_PING_INTERVAL: int = int(os.environ.get("SKYNET_WS_PING_INTERVAL", "30"))
WS_PING_TIMEOUT: int = int(os.environ.get("SKYNET_WS_PING_TIMEOUT", "10"))
RECONNECT_DELAY_SECONDS: int = int(os.environ.get("SKYNET_RECONNECT_DELAY", "5"))
MAX_RECONNECT_DELAY_SECONDS: int = int(os.environ.get("SKYNET_MAX_RECONNECT_DELAY", "120"))


# =============================================================================
# Rate Limiting
# =============================================================================
RATE_LIMIT_PER_MINUTE: int = int(os.environ.get("SKYNET_RATE_LIMIT", "120"))


# =============================================================================
# Logging
# =============================================================================
LOG_LEVEL: str = os.environ.get(
    "SKYNET_LOG_LEVEL",
    os.environ.get("OPENCLAW_LOG_LEVEL", "INFO"),
)
LOG_DIR: str = os.environ.get("SKYNET_LOG_DIR", "logs")


# =============================================================================
# TLS
# =============================================================================
TLS_CERT: Optional[str] = os.environ.get("SKYNET_TLS_CERT")
TLS_KEY: Optional[str] = os.environ.get("SKYNET_TLS_KEY")


# =============================================================================
# Feature Flags
# =============================================================================
ENABLE_S3_STORAGE: bool = os.environ.get("SKYNET_ENABLE_S3", "true").lower() == "true"
ENABLE_TELEGRAM: bool = os.environ.get("SKYNET_ENABLE_TELEGRAM", "true").lower() == "true"
ENABLE_TLS: bool = os.environ.get("SKYNET_ENABLE_TLS", "false").lower() == "true"


# =============================================================================
# Paths
# =============================================================================
def get_data_dir() -> Path:
    """Get the data directory, creating it if necessary."""
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    return data_dir


def get_logs_dir() -> Path:
    """Get the logs directory, creating it if necessary."""
    logs_dir = Path(LOG_DIR)
    logs_dir.mkdir(exist_ok=True)
    return logs_dir
