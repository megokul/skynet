"""
SKYNET Gateway unified configuration.

Policy:
- Secrets stay in environment variables.
- Non-secret runtime defaults can live in settings YAML.
- Environment variables always override settings values.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_here = Path(__file__).resolve().parent

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SETTINGS_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

# Keep secret-bearing names env-only.
_SECRET_EXACT = {
    "SKYNET_API_KEY",
    "SKYNET_AUTH_TOKEN",
    "OPENCLAW_AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "GOOGLE_AI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "GH_TOKEN",
    "GITHUB_PAT",
    "SKYNET_E2E_TELEGRAM_API_HASH",
    "SKYNET_E2E_TELEGRAM_SESSION",
    "OPENCLAW_SSH_PRIVATE_KEY_B64",
    "OPENCLAW_SSH_PASSWORD",
    "LOG_SSH_PASSWORD",
}
_SECRET_SUFFIXES = (
    "_TOKEN",
    "_PASSWORD",
    "_API_KEY",
    "_API_HASH",
    "_SECRET",
    "_PRIVATE_KEY",
    "_PAT",
)


def _is_secret_name(name: str) -> bool:
    key = name.strip().upper()
    if not key:
        return False
    if key in _SECRET_EXACT:
        return True
    return any(key.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _strip_inline_comment(raw: str) -> str:
    in_single = False
    in_double = False
    out: list[str] = []
    for ch in raw:
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            continue
        if ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
    return "".join(out).strip()


def _parse_yaml_scalar(raw: str) -> Any:
    value = _strip_inline_comment(raw)
    if value == "":
        return ""
    low = value.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if re.fullmatch(r"[+-]?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_settings_map() -> tuple[str, dict[str, Any]]:
    env_path = (os.environ.get("SKYNET_SETTINGS_FILE") or "").strip()
    default_path = _here / "settings" / "settings.yaml"
    path = Path(env_path) if env_path else default_path
    settings: dict[str, Any] = {}

    if not path.exists():
        return str(path), settings

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SETTINGS_LINE_RE.match(line)
        if not match:
            continue
        key, raw_value = match.group(1), match.group(2)
        if not _ENV_NAME_RE.match(key):
            continue
        settings[key] = _parse_yaml_scalar(raw_value)

    return str(path), settings


def _setting_raw(name: str) -> str:
    value = _SETTINGS.get(name)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value).strip()


def _get_raw(name: str, default: str = "") -> str:
    env_value = (os.environ.get(name) or "").strip()
    if env_value:
        return env_value
    if not _is_secret_name(name):
        settings_value = _setting_raw(name)
        if settings_value:
            return settings_value
    return default


def _s(name: str, default: str = "") -> str:
    return _get_raw(name, default)


def _i(name: str, default: int = 0) -> int:
    raw = _get_raw(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _b(name: str, default: bool = False) -> bool:
    raw = _get_raw(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_str(name: str, default: str = "") -> str:
    return _s(name, default)


def get_int(name: str, default: int = 0) -> int:
    return _i(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    return _b(name, default)


SETTINGS_FILE, _SETTINGS = _load_settings_map()
SSH_EXECUTION_MODES = {"ssh", "ssh_tunnel", "tunnel", "ssh-only"}

# Telegram
TELEGRAM_BOT_TOKEN: str = _s("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID: int = _i("TELEGRAM_ALLOWED_USER_ID", 0)  # 0 = allow all

# Worker WebSocket server
AUTH_TOKEN: str = _s("SKYNET_AUTH_TOKEN") or _s("OPENCLAW_AUTH_TOKEN")
WS_HOST: str = _s("SKYNET_WS_HOST", "0.0.0.0")
WS_PORT: int = _i("SKYNET_WS_PORT", 8765)
WS_PING_INTERVAL: int = _i("SKYNET_WS_PING_INTERVAL", 20)
WS_PING_TIMEOUT: int = _i("SKYNET_WS_PING_TIMEOUT", 10)
ACTION_TIMEOUT_SECONDS: int = _i("SKYNET_ACTION_TIMEOUT", 120)

# TLS
TLS_CERT: str = _s("SKYNET_TLS_CERT", "")
TLS_KEY: str = _s("SKYNET_TLS_KEY", "")

# HTTP API
HTTP_HOST: str = _s("SKYNET_HTTP_HOST") or _s("OPENCLAW_HTTP_HOST", "127.0.0.1")
HTTP_PORT: int = _i("SKYNET_HTTP_PORT", _i("OPENCLAW_HTTP_PORT", 8766))

# Database
DB_PATH: str = _s("SKYNET_DB_PATH", str(_here / "data" / "skynet.db"))

# Logging
LOG_LEVEL: str = _s("SKYNET_LOG_LEVEL") or _s("OPENCLAW_LOG_LEVEL", "INFO")
LOG_DIR: str = _s("SKYNET_LOG_DIR", str(_here / "logs"))

LOG_ENABLE_SSH_MIRROR: bool = _b(
    "SKYNET_LOG_ENABLE_SSH_MIRROR",
    _b("SKYNET_LOG_SSH_MIRROR", False),
)
LOG_SSH_HOST: str = _s("SKYNET_LOG_SSH_HOST")
LOG_SSH_PORT: int = _i("SKYNET_LOG_SSH_PORT", 22)
LOG_SSH_USER: str = _s("SKYNET_LOG_SSH_USER")
LOG_SSH_KEY_PATH: str = _s("SKYNET_LOG_SSH_KEY_PATH")
LOG_SSH_PASSWORD: str = _s("SKYNET_LOG_SSH_PASSWORD")
LOG_SSH_STRICT_HOST_KEY: bool = _b("SKYNET_LOG_SSH_STRICT_HOST_KEY", False)
LOG_SSH_CONNECT_TIMEOUT: int = _i("SKYNET_LOG_SSH_CONNECT_TIMEOUT", 10)
LOG_SSH_COMMAND_TIMEOUT: int = _i("SKYNET_LOG_SSH_COMMAND_TIMEOUT", 30)
LOG_ENABLE_LOCAL_FILES: bool = _b(
    "SKYNET_LOG_ENABLE_LOCAL_FILES",
    _b("SKYNET_LOG_LOCAL_FILES", True),
)
LOG_MAX_BYTES: int = _i("SKYNET_LOG_MAX_BYTES", 10_485_760)
LOG_BACKUP_COUNT: int = _i("SKYNET_LOG_BACKUP_COUNT", 5)
TRACE_MIRROR_LOG_DIR: str = _s("SKYNET_TRACE_MIRROR_LOG_DIR", "")

# AI providers
GOOGLE_AI_API_KEY: str = _s("GOOGLE_AI_API_KEY")
GEMINI_MODEL: str = _s("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_ONLY_MODE: bool = _b("GEMINI_ONLY_MODE", False)
GROQ_API_KEY: str = _s("GROQ_API_KEY")
OPENROUTER_API_KEY: str = _s("OPENROUTER_API_KEY")
OPENROUTER_MODEL: str = _s("OPENROUTER_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free")
OPENROUTER_FALLBACK_MODELS: str = _s("OPENROUTER_FALLBACK_MODELS", "")
DEEPSEEK_API_KEY: str = _s("DEEPSEEK_API_KEY")
OPENAI_API_KEY: str = _s("OPENAI_API_KEY")
ANTHROPIC_API_KEY: str = _s("ANTHROPIC_API_KEY")
OLLAMA_DEFAULT_MODEL: str = _s("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:32b-instruct-q4_K_M")
AI_PROVIDER_PRIORITY: str = _s(
    "AI_PROVIDER_PRIORITY",
    "ollama,gemini,claude,openai,deepseek,openrouter,groq",
)

# Web search
BRAVE_SEARCH_API_KEY: str = _s("BRAVE_SEARCH_API_KEY")

# GitHub auth (prefer GH_TOKEN, keep legacy fallback for local use)
GITHUB_PAT: str = _s("GH_TOKEN") or _s("GITHUB_PAT")
GITHUB_USERNAME: str = _s("GH_USERNAME") or _s("GITHUB_USERNAME")

# Worker paths
WORKER_PROJECTS_DIR: str = _s(
    "OPENCLAW_PROJECT_BASE_DIR",
    _s("WORKER_PROJECTS_DIR", _s("SKYNET_PROJECT_BASE_DIR", "C:/Projects")),
)

# Quality gates
STRICT_QUALITY_GATES_ENABLED: bool = _b("SKYNET_STRICT_QUALITY_GATES_ENABLED", True)
STRICT_QUALITY_GATES_DEFAULT_PROFILE: str = _s(
    "SKYNET_STRICT_QUALITY_GATES_DEFAULT_PROFILE", "strict"
).lower()
STRICT_QUALITY_GATES_FIX_RETRIES: int = _i("SKYNET_STRICT_QUALITY_GATES_FIX_RETRIES", 1)
STRICT_EMPTY_OUTPUT_EMERGENCY_SCAFFOLD: bool = _b(
    "SKYNET_STRICT_EMPTY_OUTPUT_EMERGENCY_SCAFFOLD", True
)

# Coding backend/profile defaults
CODING_DEFAULT_PROFILE: str = _s("SKYNET_CODING_DEFAULT_PROFILE", "codex_primary").lower()
CODING_FORCE_PRIMARY_FOR_ALL: bool = _b("SKYNET_CODING_FORCE_PRIMARY_FOR_ALL", True)
CODING_FALLBACK_CHAIN: str = _s(
    "SKYNET_CODING_FALLBACK_CHAIN", "codex,claude_ollama,cline"
).lower()
CODEX_WRITE_MODE: str = _s("SKYNET_CODEX_WRITE_MODE", "danger_full_access").strip().lower()
CLAUDE_OLLAMA_STAGE_ENABLED: bool = _b("SKYNET_CLAUDE_OLLAMA_STAGE_ENABLED", False)
ORCHESTRATION_MODE: str = _s("SKYNET_ORCHESTRATION_MODE", "legacy").lower()
ORCHESTRATION_ALLOW_ACP_WITH_SSH: bool = _b(
    "SKYNET_ORCHESTRATION_ALLOW_ACP_WITH_SSH",
    False,
)
OPENCLAW_RUNTIME: str = _s("SKYNET_OPENCLAW_RUNTIME", "acp").lower()
OPENCLAW_QUEUE_MODE: str = _s("SKYNET_OPENCLAW_QUEUE_MODE", "require_empty_queue").lower()
OPENCLAW_RETRY_TRANSIENT: bool = _b("SKYNET_OPENCLAW_RETRY_TRANSIENT", True)
OPENCLAW_SESSION_TIMEOUT_SECONDS: int = _i("SKYNET_OPENCLAW_SESSION_TIMEOUT_SECONDS", 1800)
OPENCLAW_STAGE_CHAIN: str = _s("SKYNET_OPENCLAW_STAGE_CHAIN", "codex,claude,cline").lower()
OPENCLAW_AGENT_HOSTING: str = _s("SKYNET_OPENCLAW_AGENT_HOSTING", "ec2_control").lower()
OPENCLAW_TRACE_ENABLED: bool = _b("SKYNET_OPENCLAW_TRACE_ENABLED", True)
OPENCLAW_CLI_BIN: str = _s("SKYNET_OPENCLAW_CLI_BIN", "claw")
OPENCLAW_CODEX_BIN: str = _s("OPENCLAW_CODEX_BIN", _s("OPENCLAW_SSH_CODEX_BIN", "codex"))
OPENCLAW_CLAUDE_BIN: str = _s("OPENCLAW_CLAUDE_BIN", _s("OPENCLAW_SSH_CLAUDE_BIN", "claude"))
OPENCLAW_CLINE_BIN: str = _s("OPENCLAW_CLINE_BIN", _s("OPENCLAW_SSH_CLINE_BIN", "cline"))
PLANNER_PRIMARY_AGENT: str = _s("SKYNET_PLANNER_PRIMARY_AGENT", "router").lower()
PLANNER_CODEX_TIMEOUT_SECONDS: int = _i("SKYNET_PLANNER_CODEX_TIMEOUT_SECONDS", 120)
MILESTONE_CODEX_TIMEOUT_SECONDS: int = _i("SKYNET_MILESTONE_CODEX_TIMEOUT_SECONDS", 120)
CODING_TRANSPORT: str = _s("SKYNET_CODING_TRANSPORT", "ssh_first").lower()
E2E_FAIL_ON_SKIP: bool = _b("SKYNET_E2E_FAIL_ON_SKIP", True)
CODING_PROGRESS_HEARTBEAT_SECONDS: int = _i("SKYNET_CODING_PROGRESS_HEARTBEAT_SECONDS", 30)
CODING_AGENT_MAX_WAIT_SECONDS: int = _i("SKYNET_CODING_AGENT_MAX_WAIT_SECONDS", 900)
MILESTONE_EXTRACTION_HEARTBEAT_SECONDS: int = _i(
    "SKYNET_MILESTONE_EXTRACTION_HEARTBEAT_SECONDS", 20
)
MILESTONE_EXTRACTION_MAX_WAIT_SECONDS: int = _i(
    "SKYNET_MILESTONE_EXTRACTION_MAX_WAIT_SECONDS", 180
)
TELEGRAM_TRACKER_ENABLED: bool = _b("SKYNET_TELEGRAM_TRACKER_ENABLED", True)
TELEGRAM_TRACKER_EDIT_INTERVAL_SECONDS: int = _i(
    "SKYNET_TELEGRAM_TRACKER_EDIT_INTERVAL_SECONDS", 3
)
TELEGRAM_TRACKER_STALE_WARN_SECONDS: int = _i(
    "SKYNET_TELEGRAM_TRACKER_STALE_WARN_SECONDS", 90
)
TELEGRAM_TRACKER_BAR_WIDTH: int = _i("SKYNET_TELEGRAM_TRACKER_BAR_WIDTH", 20)
TELEGRAM_TRACKER_VERBOSE_PIPELINE: bool = _b(
    "SKYNET_TELEGRAM_TRACKER_VERBOSE_PIPELINE",
    True,
)

# SSH tunnel reliability controls
SSH_MAX_PARALLEL: int = _i("OPENCLAW_SSH_MAX_PARALLEL", 2)
SSH_CIRCUIT_BREAKER_SECONDS: int = _i("OPENCLAW_SSH_CIRCUIT_BREAKER_SECONDS", 60)
SSH_CAPACITY_BACKOFF_SECONDS: int = _i("OPENCLAW_SSH_CAPACITY_BACKOFF_SECONDS", 30)
SSH_HEALTH_PROBE_TIMEOUT: int = _i("OPENCLAW_SSH_HEALTH_PROBE_TIMEOUT", 6)

# Backward-compatible fallbacks for OPENCLAW_OLLAMA_* wiring.
_CLAUDE_OLLAMA_BASE_URL_DEFAULT = _s("OPENCLAW_OLLAMA_URL", "http://localhost:11434")
_CLAUDE_OLLAMA_MODEL_DEFAULT = _s("OPENCLAW_OLLAMA_MODEL", "qwen2.5-coder:7b")
_OPENCLAW_OLLAMA_AUTO_PULL_RAW = _s("OPENCLAW_OLLAMA_AUTO_PULL", "")
_OPENCLAW_OLLAMA_AUTO_PULL_DEFAULT = (
    _OPENCLAW_OLLAMA_AUTO_PULL_RAW.lower() in {"1", "true", "yes", "on"}
    if _OPENCLAW_OLLAMA_AUTO_PULL_RAW
    else False
)

CLAUDE_OLLAMA_BASE_URL: str = _s(
    "SKYNET_CLAUDE_OLLAMA_BASE_URL",
    _CLAUDE_OLLAMA_BASE_URL_DEFAULT,
)
CLAUDE_OLLAMA_AUTH_TOKEN: str = _s("SKYNET_CLAUDE_OLLAMA_AUTH_TOKEN", "ollama")
CLAUDE_OLLAMA_DEFAULT_MODEL: str = _s(
    "SKYNET_CLAUDE_OLLAMA_DEFAULT_MODEL",
    _CLAUDE_OLLAMA_MODEL_DEFAULT,
)
CLAUDE_OLLAMA_AUTO_PULL: bool = _b(
    "SKYNET_CLAUDE_OLLAMA_AUTO_PULL",
    _OPENCLAW_OLLAMA_AUTO_PULL_DEFAULT,
)
CLAUDE_OLLAMA_MIN_CONTEXT: int = _i("SKYNET_CLAUDE_OLLAMA_MIN_CONTEXT", 64000)


def is_ssh_execution_mode() -> bool:
    mode = _s("OPENCLAW_EXECUTION_MODE", "").strip().lower()
    return mode in SSH_EXECUTION_MODES


def effective_orchestration_mode() -> str:
    mode = str(ORCHESTRATION_MODE or "legacy").strip().lower() or "legacy"
    if (
        mode == "acp_first"
        and is_ssh_execution_mode()
        and not bool(ORCHESTRATION_ALLOW_ACP_WITH_SSH)
    ):
        return "legacy"
    return mode
