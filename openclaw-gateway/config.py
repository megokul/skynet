"""
SKYNET Gateway unified configuration.

Policy:
- Secrets stay in environment variables.
- Non-secret runtime defaults come from settings YAML files.
- Environment variables always override settings values.

This module now uses the unified settings loader for consistency.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skynet.settings.loader import get_component_settings  # noqa: E402

# Initialize global settings loader
_settings_loader = get_component_settings("gateway")

# ---------------------------------------------------------------------------
# Backward compatibility wrappers - these delegate to the unified loader
# ---------------------------------------------------------------------------

def get_str(name: str, default: str = "") -> str:
    """Get string setting (backward compatible with old _s function)."""
    return _settings_loader.get_str(name, default)


def get_int(name: str, default: int = 0) -> int:
    """Get integer setting (backward compatible with old _i function)."""
    return _settings_loader.get_int(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    """Get boolean setting (backward compatible with old _b function)."""
    return _settings_loader.get_bool(name, default)


# Aliases for backward compatibility with existing code
_s = get_str
_i = get_int
_b = get_bool

# Re-export for external use
__all__ = [
    "get_str",
    "get_int",
    "get_bool",
    "get_list",
    "get_settings",
    "get_provider_config",
    "get_coding_fallback_chain",
    "get_openclaw_stage_chain",
    "get_code_index_globs",
    "get_live_e2e_flow",
    "get_live_e2e_backend",
    "get_live_e2e_agent",
    "get_live_e2e_container_log_config",
    "get_provider_priority",
    "get_ssh_config",
    "dump_config",
]


def get_settings() -> Any:
    """Get the settings loader instance."""
    return _settings_loader


def get_list(name: str, separator: str = ",", default: list[str] | None = None) -> list[str]:
    """Get list setting."""
    return _settings_loader.get_list(name, separator, default)


def _normalized_agent_list(name: str, default: list[str]) -> tuple[str, ...]:
    items = get_list(name, default=default)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        agent = str(raw or "").strip().lower()
        if agent == "claude_ollama":
            agent = "claude"
        if not agent or agent in seen:
            continue
        seen.add(agent)
        normalized.append(agent)
    return tuple(normalized)


# Backward compatibility: SETTINGS_FILE path
SETTINGS_FILE = str(_settings_loader.settings_file)

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
DB_PATH: str = _s("SKYNET_DB_PATH", "data/skynet.db")

# Logging
LOG_LEVEL: str = _s("SKYNET_LOG_LEVEL") or _s("OPENCLAW_LOG_LEVEL", "INFO")
LOG_DIR: str = _s("SKYNET_LOG_DIR", "logs")

LOG_ENABLE_SSH_MIRROR: bool = _b(
    "SKYNET_LOG_ENABLE_SSH_MIRROR",
    _b("SKYNET_LOG_SSH_MIRROR", False),
)
# Mirror transport defaults to execution SSH transport when explicit mirror values are unset.
LOG_SSH_HOST: str = _s("SKYNET_LOG_SSH_HOST", _s("OPENCLAW_SSH_HOST"))
LOG_SSH_PORT: int = _i("SKYNET_LOG_SSH_PORT", _i("OPENCLAW_SSH_PORT", 22))
LOG_SSH_USER: str = _s("SKYNET_LOG_SSH_USER", _s("OPENCLAW_SSH_USER"))
LOG_SSH_KEY_PATH: str = _s("SKYNET_LOG_SSH_KEY_PATH", _s("OPENCLAW_SSH_KEY_PATH"))
LOG_SSH_PASSWORD: str = _s("SKYNET_LOG_SSH_PASSWORD", _s("OPENCLAW_SSH_PASSWORD"))
LOG_SSH_STRICT_HOST_KEY: bool = _b("SKYNET_LOG_SSH_STRICT_HOST_KEY", False)
LOG_SSH_CONNECT_TIMEOUT: int = _i("SKYNET_LOG_SSH_CONNECT_TIMEOUT", 10)
LOG_SSH_COMMAND_TIMEOUT: int = _i("SKYNET_LOG_SSH_COMMAND_TIMEOUT", 30)
LOG_ENABLE_WEBSOCKET_MIRROR: bool = _b("SKYNET_LOG_ENABLE_WEBSOCKET_MIRROR", True)
LOG_WEBSOCKET_REQUIRE_ACK: bool = _b("SKYNET_LOG_WEBSOCKET_REQUIRE_ACK", True)
LOG_WEBSOCKET_ACK_TIMEOUT_SECONDS: int = _i("SKYNET_LOG_WEBSOCKET_ACK_TIMEOUT_SECONDS", 5)
LOG_ENABLE_LOCAL_FILES: bool = _b(
    "SKYNET_LOG_ENABLE_LOCAL_FILES",
    _b("SKYNET_LOG_LOCAL_FILES", True),
)
LOG_MAX_BYTES: int = _i("SKYNET_LOG_MAX_BYTES", 10_485_760)
LOG_BACKUP_COUNT: int = _i("SKYNET_LOG_BACKUP_COUNT", 5)
TRACE_MIRROR_LOG_DIR: str = _s("SKYNET_TRACE_MIRROR_LOG_DIR", "logs")
RUNTIME_TRACE_ENABLED: bool = _b("SKYNET_RUNTIME_TRACE_ENABLED", True)
RUNTIME_TRACE_LIVE_FILE: str = _s(
    "SKYNET_RUNTIME_TRACE_LIVE_FILE",
    "logs/skynet.trace.log",
)
RUNTIME_TRACE_LEVEL: str = _s("SKYNET_RUNTIME_TRACE_LEVEL", "info").strip().lower()
RUNTIME_TRACE_DETAIL_PROFILE: str = _s(
    "SKYNET_RUNTIME_TRACE_DETAIL_PROFILE",
    "max_forensic",
).strip().lower()
RUNTIME_TRACE_PAYLOAD_MODE: str = _s(
    "SKYNET_RUNTIME_TRACE_PAYLOAD_MODE",
    "forensic_redacted",
).strip().lower()
RUNTIME_TRACE_INCLUDE_STDIO_TAIL: bool = _b("SKYNET_RUNTIME_TRACE_INCLUDE_STDIO_TAIL", True)
RUNTIME_TRACE_STDIO_TAIL_BYTES: int = _i("SKYNET_RUNTIME_TRACE_STDIO_TAIL_BYTES", 4000)
RUNTIME_TRACE_STDIO_CHUNK_BYTES: int = _i("SKYNET_RUNTIME_TRACE_STDIO_CHUNK_BYTES", 16000)
RUNTIME_TRACE_COMMAND_PREVIEW_CHARS: int = _i("SKYNET_RUNTIME_TRACE_COMMAND_PREVIEW_CHARS", 2000)
RUNTIME_TRACE_DB_ENABLED: bool = _b("SKYNET_RUNTIME_TRACE_DB_ENABLED", True)
RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE: bool = _b("SKYNET_RUNTIME_TRACE_REQUIRE_DEBUG_BUNDLE", True)
RUNTIME_TRACE_REQUIRED_EVENT_ENFORCEMENT: bool = _b(
    "SKYNET_RUNTIME_TRACE_REQUIRED_EVENT_ENFORCEMENT",
    True,
)
RUNTIME_TRACE_REDACTION_MODE: str = _s(
    "SKYNET_RUNTIME_TRACE_REDACTION_MODE",
    "forensic_redacted",
).strip().lower()
RUNTIME_TRACE_HEARTBEAT_REMOTE_SNAPSHOT: bool = _b("SKYNET_RUNTIME_TRACE_HEARTBEAT_REMOTE_SNAPSHOT", True)
RUNTIME_TRACE_HEARTBEAT_REMOTE_SNAPSHOT_INTERVAL_SECONDS: int = _i(
    "SKYNET_RUNTIME_TRACE_HEARTBEAT_REMOTE_SNAPSHOT_INTERVAL_SECONDS",
    30,
)
RUNTIME_TRACE_REMOTE_PROBE_TIMEOUT_SECONDS: int = _i(
    "SKYNET_RUNTIME_TRACE_REMOTE_PROBE_TIMEOUT_SECONDS",
    8,
)
RUNTIME_TRACE_PROCESS_SNAPSHOT_TOPK: int = _i("SKYNET_RUNTIME_TRACE_PROCESS_SNAPSHOT_TOPK", 25)
RUNTIME_TRACE_ARTIFACT_SNAPSHOT_MAX_FILES: int = _i(
    "SKYNET_RUNTIME_TRACE_ARTIFACT_SNAPSHOT_MAX_FILES",
    120,
)
RUNTIME_TRACE_PROMPT_FILE_EVENTS: bool = _b("SKYNET_RUNTIME_TRACE_PROMPT_FILE_EVENTS", True)
RUNTIME_TRACE_ACTIVE_SESSION_REGISTRY: bool = _b("SKYNET_RUNTIME_TRACE_ACTIVE_SESSION_REGISTRY", True)
RUNTIME_TRACE_STOP_CLEANUP_EVENTS: bool = _b("SKYNET_RUNTIME_TRACE_STOP_CLEANUP_EVENTS", True)
WEBSOCKET_PRIMARY_ENABLED: bool = _b("SKYNET_WEBSOCKET_PRIMARY_ENABLED", True)
WEBSOCKET_ACCEPT_TIMEOUT_SECONDS: int = _i("SKYNET_WEBSOCKET_ACCEPT_TIMEOUT_SECONDS", 3)
WEBSOCKET_REPLAY_TIMEOUT_SECONDS: int = _i("SKYNET_WEBSOCKET_REPLAY_TIMEOUT_SECONDS", 30)
WEBSOCKET_HEARTBEAT_STALE_SECONDS: int = _i("SKYNET_WEBSOCKET_HEARTBEAT_STALE_SECONDS", 45)
WEBSOCKET_FALLBACK_TO_SSH: bool = _b("SKYNET_WEBSOCKET_FALLBACK_TO_SSH", True)

# AI providers
GOOGLE_AI_API_KEY: str = _s("GOOGLE_AI_API_KEY")
GEMINI_MODEL: str = _s("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_ONLY_MODE: bool = _b("GEMINI_ONLY_MODE", False)
GROQ_API_KEY: str = _s("GROQ_API_KEY")
DASHSCOPE_API_KEY: str = _s("DASHSCOPE_API_KEY")
OPENROUTER_API_KEY: str = _s("OPENROUTER_API_KEY")
OPENROUTER_MODEL: str = _s("OPENROUTER_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free")
OPENROUTER_FALLBACK_MODELS: str = _s("OPENROUTER_FALLBACK_MODELS", "")
DEEPSEEK_API_KEY: str = _s("DEEPSEEK_API_KEY")
OPENAI_API_KEY: str = _s("OPENAI_API_KEY")
ANTHROPIC_API_KEY: str = _s("ANTHROPIC_API_KEY")
OLLAMA_DEFAULT_MODEL: str = _s("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:7b")
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
    _s("WORKER_PROJECTS_DIR", _s("SKYNET_PROJECT_BASE_DIR", "")),
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
    "SKYNET_CODING_FALLBACK_CHAIN", "qwen,codex"
).lower()
CODEX_WRITE_MODE: str = _s("SKYNET_CODEX_WRITE_MODE", "danger_full_access").strip().lower()
CLAUDE_OLLAMA_STAGE_ENABLED: bool = _b("SKYNET_CLAUDE_OLLAMA_STAGE_ENABLED", False)
CONTROL_LOOP_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_ENABLED", True)
CONTROL_LOOP_FORCE_FOR_ALL: bool = _b("SKYNET_CONTROL_LOOP_FORCE_FOR_ALL", True)
CONTROL_LOOP_DEFAULT_PROFILE: str = _s(
    "SKYNET_CONTROL_LOOP_DEFAULT_PROFILE", "loop_v2"
).lower()
CONTROL_LOOP_SCHEDULER_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_SCHEDULER_ENABLED", True)
CONTROL_LOOP_MAX_PARALLEL_NODES: int = _i("SKYNET_CONTROL_LOOP_MAX_PARALLEL_NODES", 2)
CONTROL_LOOP_DEADLOCK_IDLE_TICKS: int = _i("SKYNET_CONTROL_LOOP_DEADLOCK_IDLE_TICKS", 3)
CONTROL_LOOP_REPAIR_RETRIES: int = _i("SKYNET_CONTROL_LOOP_REPAIR_RETRIES", 1)
CONTROL_LOOP_BUDGET_MAX_ITERATIONS: int = _i("SKYNET_CONTROL_LOOP_BUDGET_MAX_ITERATIONS", 40)
CONTROL_LOOP_BUDGET_MAX_RUNTIME_SECONDS: int = _i(
    "SKYNET_CONTROL_LOOP_BUDGET_MAX_RUNTIME_SECONDS", 3600
)
CONTROL_LOOP_BUDGET_MAX_REPAIRS: int = _i("SKYNET_CONTROL_LOOP_BUDGET_MAX_REPAIRS", 1)
CONTROL_LOOP_BUDGET_MAX_TOKENS: int = _i("SKYNET_CONTROL_LOOP_BUDGET_MAX_TOKENS", 250000)
CONTROL_LOOP_FAILURE_CLASSIFIER_ENABLED: bool = _b(
    "SKYNET_CONTROL_LOOP_FAILURE_CLASSIFIER_ENABLED",
    True,
)
CONTROL_LOOP_CRITIC_BLOCK_THRESHOLD: str = _s(
    "SKYNET_CONTROL_LOOP_CRITIC_BLOCK_THRESHOLD", "high"
).lower()
CONTROL_LOOP_REVIEW_CRITIC_ENABLED: bool = _b(
    "SKYNET_CONTROL_LOOP_REVIEW_CRITIC_ENABLED",
    True,
)
CONTROL_LOOP_REVIEW_TIMEOUT_SECONDS: int = _i(
    "SKYNET_CONTROL_LOOP_REVIEW_TIMEOUT_SECONDS",
    120,
)
CONTROL_LOOP_MEMORY_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_MEMORY_ENABLED", True)
CONTROL_LOOP_CODE_INDEX_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_CODE_INDEX_ENABLED", True)
CONTROL_LOOP_CODE_INDEX_GLOBS: str = _s(
    "SKYNET_CONTROL_LOOP_CODE_INDEX_GLOBS",
    "*.py,*.js,*.ts,*.tsx",
)
CONTROL_LOOP_CONTEXT_COMPRESSION_ENABLED: bool = _b(
    "SKYNET_CONTROL_LOOP_CONTEXT_COMPRESSION_ENABLED",
    True,
)
CONTROL_LOOP_CONTEXT_MAX_CHARS: int = _i("SKYNET_CONTROL_LOOP_CONTEXT_MAX_CHARS", 12000)
CONTROL_LOOP_CONTEXT_TOPK: int = _i("SKYNET_CONTROL_LOOP_CONTEXT_TOPK", 12)
CONTROL_LOOP_TRACE_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_TRACE_ENABLED", True)
CONTROL_LOOP_TRACE_RETENTION_DAYS: int = _i("SKYNET_CONTROL_LOOP_TRACE_RETENTION_DAYS", 14)
CONTROL_LOOP_TOOL_AWARE_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_TOOL_AWARE_ENABLED", True)
CONTROL_LOOP_ARCH_CRITIC_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_ARCH_CRITIC_ENABLED", True)
CONTROL_LOOP_ARCH_RULES_FILE: str = _s(
    "SKYNET_CONTROL_LOOP_ARCH_RULES_FILE",
    "openclaw-gateway/settings/architecture_rules.yaml",
)
CONTROL_LOOP_COMPLETION_CONTRACT_REQUIRED: bool = _b(
    "SKYNET_CONTROL_LOOP_COMPLETION_CONTRACT_REQUIRED",
    True,
)
CONTROL_LOOP_PLANNER_AGENT: str = _s(
    "SKYNET_CONTROL_LOOP_PLANNER_AGENT",
    "qwen",
).lower()
CONTROL_LOOP_CRITIC_AGENT: str = _s(
    "SKYNET_CONTROL_LOOP_CRITIC_AGENT",
    "qwen",
).lower()
CONTROL_LOOP_ROUTER_FALLBACK_ENABLED: bool = _b(
    "SKYNET_CONTROL_LOOP_ROUTER_FALLBACK_ENABLED",
    False,
)
CONTROL_LOOP_DIRECTOR_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_DIRECTOR_ENABLED", True)
CONTROL_LOOP_ARCHITECT_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_ARCHITECT_ENABLED", True)
CONTROL_LOOP_ARCH_BLOCKING: bool = _b("SKYNET_CONTROL_LOOP_ARCH_BLOCKING", True)
CONTROL_LOOP_ARCH_MAX_VIOLATIONS: int = _i("SKYNET_CONTROL_LOOP_ARCH_MAX_VIOLATIONS", 0)
CONTROL_LOOP_WORKER_POOL_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_WORKER_POOL_ENABLED", True)
CONTROL_LOOP_WORKER_POOL_STRATEGY: str = _s(
    "SKYNET_CONTROL_LOOP_WORKER_POOL_STRATEGY",
    "capability_priority",
).strip().lower()
CONTROL_LOOP_DEFAULT_WORKER_ID: str = _s(
    "SKYNET_CONTROL_LOOP_DEFAULT_WORKER_ID",
    "worker-primary",
).strip()
CONTROL_LOOP_LEARNING_ENABLED: bool = _b("SKYNET_CONTROL_LOOP_LEARNING_ENABLED", True)
CONTROL_LOOP_LEARNING_MIN_SAMPLES: int = _i("SKYNET_CONTROL_LOOP_LEARNING_MIN_SAMPLES", 5)
CONTROL_LOOP_LEARNING_APPLY_MODE: str = _s(
    "SKYNET_CONTROL_LOOP_LEARNING_APPLY_MODE",
    "conservative",
).strip().lower()
CONTROL_LOOP_DIRECTOR_TIMEOUT_SECONDS: int = _i(
    "SKYNET_CONTROL_LOOP_DIRECTOR_TIMEOUT_SECONDS",
    120,
)
CONTROL_LOOP_ARCHITECT_TIMEOUT_SECONDS: int = _i(
    "SKYNET_CONTROL_LOOP_ARCHITECT_TIMEOUT_SECONDS",
    180,
)
CONTROL_LOOP_PLANNER_TIMEOUT_SECONDS: int = _i(
    "SKYNET_CONTROL_LOOP_PLANNER_TIMEOUT_SECONDS",
    180,
)
CONTROL_LOOP_TRACE_INCLUDE_REASONING: bool = _b(
    "SKYNET_CONTROL_LOOP_TRACE_INCLUDE_REASONING",
    False,
)
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
PLANNER_PRIMARY_AGENT: str = _s("SKYNET_PLANNER_PRIMARY_AGENT", "codex").lower()
PLANNER_WORKER_AGENTS: tuple[str, ...] = _normalized_agent_list(
    "SKYNET_PLANNER_WORKER_AGENTS",
    ["codex", "qwen", "claude", "cline"],
)
PLANNER_ACP_AGENTS: tuple[str, ...] = _normalized_agent_list(
    "SKYNET_PLANNER_ACP_AGENTS",
    ["codex", "claude", "cline"],
)
PLANNER_ROUTER_FALLBACK_ENABLED: bool = _b(
    "SKYNET_PLANNER_ROUTER_FALLBACK_ENABLED",
    True,
)
PLANNER_CODEX_TIMEOUT_SECONDS: int = _i("SKYNET_PLANNER_CODEX_TIMEOUT_SECONDS", 120)
MILESTONE_CODEX_TIMEOUT_SECONDS: int = _i("SKYNET_MILESTONE_CODEX_TIMEOUT_SECONDS", 120)
CODING_TRANSPORT: str = _s("SKYNET_CODING_TRANSPORT", "websocket_primary").lower()
E2E_FAIL_ON_SKIP: bool = _b("SKYNET_E2E_FAIL_ON_SKIP", True)
E2E_CONTAINER_LOG_STREAM_ENABLED: bool = _b("SKYNET_E2E_CONTAINER_LOG_STREAM_ENABLED", True)
E2E_CONTAINER_LOG_REQUIRE_STREAM: bool = _b("SKYNET_E2E_CONTAINER_LOG_REQUIRE_STREAM", True)
E2E_CONTAINER_LOG_SOURCES: tuple[str, ...] = tuple(
    get_list("SKYNET_E2E_CONTAINER_LOG_SOURCES", default=["openclaw-gateway", "skynet-api"])
)
E2E_CONTAINER_LOG_MAX_LINE_CHARS: int = _i("SKYNET_E2E_CONTAINER_LOG_MAX_LINE_CHARS", 1200)
E2E_CONTAINER_LOG_RING_LINES: int = _i("SKYNET_E2E_CONTAINER_LOG_RING_LINES", 300)
E2E_CONTAINER_LOG_TAIL_DEFAULT: int = _i("SKYNET_E2E_CONTAINER_LOG_TAIL_DEFAULT", 100)
E2E_CONTAINER_LOG_TAIL_OVERRIDES: str = _s(
    "SKYNET_E2E_CONTAINER_LOG_TAIL_OVERRIDES",
    "openclaw-gateway=200,skynet-api=100",
).strip()
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
TELEGRAM_TRACKER_STUCK_EXIT_SECONDS: int = _i(
    "SKYNET_TELEGRAM_TRACKER_STUCK_EXIT_SECONDS", 300
)
TELEGRAM_TRACKER_WATCHDOG_POLL_SECONDS: int = _i(
    "SKYNET_TELEGRAM_TRACKER_WATCHDOG_POLL_SECONDS", 5
)
TELEGRAM_TRACKER_BAR_WIDTH: int = _i("SKYNET_TELEGRAM_TRACKER_BAR_WIDTH", 20)
TELEGRAM_TRACKER_VERBOSE_PIPELINE: bool = _b(
    "SKYNET_TELEGRAM_TRACKER_VERBOSE_PIPELINE",
    True,
)
LIVE_E2E_FLOW: str = _s("SKYNET_LIVE_E2E_FLOW", "conversation").strip().lower()
LIVE_E2E_BACKEND: str = _s("SKYNET_LIVE_E2E_BACKEND", "auto").strip().lower()
LIVE_E2E_AGENT: str = _s("SKYNET_LIVE_E2E_AGENT", "").strip().lower()

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
    # Read from module globals so monkeypatching cfg attributes in tests works correctly.
    _globals = globals()
    mode = str(_globals.get("ORCHESTRATION_MODE") or "legacy").strip().lower() or "legacy"
    allow_acp_with_ssh = bool(_globals.get("ORCHESTRATION_ALLOW_ACP_WITH_SSH", False))
    if mode == "acp_first" and is_ssh_execution_mode() and not allow_acp_with_ssh:
        return "legacy"
    return mode


# ---------------------------------------------------------------------------
# Convenience helpers — use these instead of getattr(cfg, ...) in handlers
# ---------------------------------------------------------------------------

def is_acp_first() -> bool:
    """Return True when ACP-first orchestration is the effective mode."""
    return effective_orchestration_mode() == "acp_first"


def is_websocket_primary() -> bool:
    """Return True when WebSocket-primary transport is enabled."""
    return bool(WEBSOCKET_PRIMARY_ENABLED)


def is_websocket_fallback_to_ssh() -> bool:
    """Return True when WebSocket may fall back to SSH."""
    return bool(WEBSOCKET_FALLBACK_TO_SSH)


def is_ssh_fallback_enabled() -> bool:
    """Return True when SSH fallback is explicitly enabled."""
    return _b("OPENCLAW_SSH_FALLBACK_ENABLED", False)


def is_control_loop_active() -> bool:
    """Return True when the control loop is enabled and forced for all projects."""
    return bool(CONTROL_LOOP_ENABLED) and bool(CONTROL_LOOP_FORCE_FOR_ALL)


def is_strict_gates_enabled() -> bool:
    """Return True when strict quality gates are enabled."""
    return bool(STRICT_QUALITY_GATES_ENABLED)


def is_runtime_trace_active() -> bool:
    """Return True when runtime trace is enabled."""
    return bool(RUNTIME_TRACE_ENABLED)


def is_telegram_tracker_active() -> bool:
    """Return True when the Telegram coding tracker is enabled."""
    return bool(TELEGRAM_TRACKER_ENABLED)


def get_coding_fallback_chain() -> list[str]:
    """Return the ordered coding fallback chain as a list."""
    raw = CODING_FALLBACK_CHAIN or "codex"
    return [c.strip() for c in raw.split(",") if c.strip()]


def get_provider_config() -> dict[str, str]:
    """Return the provider-router config payload from effective settings."""
    return {
        "OLLAMA_DEFAULT_MODEL": OLLAMA_DEFAULT_MODEL,
        "GOOGLE_AI_API_KEY": GOOGLE_AI_API_KEY,
        "GEMINI_MODEL": GEMINI_MODEL,
        "GEMINI_ONLY_MODE": "1" if GEMINI_ONLY_MODE else "0",
        "GROQ_API_KEY": GROQ_API_KEY,
        "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
        "OPENROUTER_MODEL": OPENROUTER_MODEL,
        "OPENROUTER_FALLBACK_MODELS": OPENROUTER_FALLBACK_MODELS,
        "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }


def get_openclaw_stage_chain() -> list[str]:
    """Return the OpenClaw stage chain as a list."""
    raw = OPENCLAW_STAGE_CHAIN or "codex"
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_code_index_globs() -> list[str]:
    """Return the control-loop code index globs as a list."""
    raw = CONTROL_LOOP_CODE_INDEX_GLOBS or "*.py,*.js,*.ts,*.tsx"
    return [g.strip() for g in raw.split(",") if g.strip()]


def get_provider_priority() -> list[str]:
    """Return the AI provider priority list."""
    raw = AI_PROVIDER_PRIORITY or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_live_e2e_flow() -> str:
    """Return the configured live E2E flow mode."""
    return LIVE_E2E_FLOW or "conversation"


def get_live_e2e_backend() -> str:
    """Return the configured direct live E2E backend."""
    return LIVE_E2E_BACKEND or "auto"


def get_live_e2e_agent() -> str:
    """Return the configured direct live E2E agent, derived from coding policy when unset."""
    if LIVE_E2E_AGENT:
        return LIVE_E2E_AGENT
    chain = get_coding_fallback_chain()
    return chain[0] if chain else "codex"


def _parse_container_log_tail_overrides(raw: str) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for token in str(raw or "").split(","):
        item = token.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        container = name.strip()
        if not container:
            continue
        try:
            tail_lines = int(value.strip())
        except ValueError:
            continue
        if tail_lines < 1:
            continue
        overrides[container] = tail_lines
    return overrides


def get_live_e2e_container_log_config() -> dict[str, Any]:
    """Return normalized live E2E container-log settings."""
    sources = get_list(
        "SKYNET_E2E_CONTAINER_LOG_SOURCES",
        default=list(E2E_CONTAINER_LOG_SOURCES) or ["openclaw-gateway", "skynet-api"],
    )
    tail_default = max(
        1,
        get_int("SKYNET_E2E_CONTAINER_LOG_TAIL_DEFAULT", E2E_CONTAINER_LOG_TAIL_DEFAULT),
    )
    tail_overrides_raw = get_str(
        "SKYNET_E2E_CONTAINER_LOG_TAIL_OVERRIDES",
        E2E_CONTAINER_LOG_TAIL_OVERRIDES,
    )
    return {
        "stream_enabled": get_bool(
            "SKYNET_E2E_CONTAINER_LOG_STREAM_ENABLED",
            E2E_CONTAINER_LOG_STREAM_ENABLED,
        ),
        "require_stream": get_bool(
            "SKYNET_E2E_CONTAINER_LOG_REQUIRE_STREAM",
            E2E_CONTAINER_LOG_REQUIRE_STREAM,
        ),
        "sources": sources,
        "max_line_chars": max(
            200,
            get_int(
                "SKYNET_E2E_CONTAINER_LOG_MAX_LINE_CHARS",
                E2E_CONTAINER_LOG_MAX_LINE_CHARS,
            ),
        ),
        "ring_lines": max(
            20,
            get_int("SKYNET_E2E_CONTAINER_LOG_RING_LINES", E2E_CONTAINER_LOG_RING_LINES),
        ),
        "tail_default": tail_default,
        "tail_overrides": _parse_container_log_tail_overrides(tail_overrides_raw),
    }


def get_ssh_config() -> dict:
    """Return a dict of all SSH transport settings (no secrets)."""
    return {
        "execution_mode": _s("OPENCLAW_EXECUTION_MODE", ""),
        "host": _s("OPENCLAW_SSH_HOST", ""),
        "port": _i("OPENCLAW_SSH_PORT", 22),
        "user": _s("OPENCLAW_SSH_USER", ""),
        "key_path": _s("OPENCLAW_SSH_KEY_PATH", ""),
        "connect_timeout": _i("OPENCLAW_SSH_CONNECT_TIMEOUT", 4),
        "command_timeout": _i("OPENCLAW_SSH_COMMAND_TIMEOUT", 180),
        "remote_os": _s("OPENCLAW_SSH_REMOTE_OS", "windows"),
        "max_parallel": SSH_MAX_PARALLEL,
        "circuit_breaker_seconds": SSH_CIRCUIT_BREAKER_SECONDS,
        "capacity_backoff_seconds": SSH_CAPACITY_BACKOFF_SECONDS,
        "health_probe_timeout": SSH_HEALTH_PROBE_TIMEOUT,
        "fallback_enabled": is_ssh_fallback_enabled(),
        "strict_host_key": _b("OPENCLAW_SSH_STRICT_HOST_KEY", False),
    }


def dump_config() -> dict:
    """Return a redacted snapshot of the current effective configuration.

    Useful for /status diagnostics and startup logging.
    Secrets are masked; all other values are shown as-is.
    """
    return {
        "settings_file": SETTINGS_FILE,
        "transport": {
            "execution_mode": _s("OPENCLAW_EXECUTION_MODE", ""),
            "websocket_primary": WEBSOCKET_PRIMARY_ENABLED,
            "websocket_fallback_to_ssh": WEBSOCKET_FALLBACK_TO_SSH,
            "ssh_fallback_enabled": is_ssh_fallback_enabled(),
            "ssh_host": _s("OPENCLAW_SSH_HOST", ""),
            "ssh_port": _i("OPENCLAW_SSH_PORT", 22),
        },
        "orchestration": {
            "mode": ORCHESTRATION_MODE,
            "effective_mode": effective_orchestration_mode(),
            "allow_acp_with_ssh": ORCHESTRATION_ALLOW_ACP_WITH_SSH,
        },
        "coding": {
            "default_profile": CODING_DEFAULT_PROFILE,
            "force_primary_for_all": CODING_FORCE_PRIMARY_FOR_ALL,
            "fallback_chain": get_coding_fallback_chain(),
            "transport": CODING_TRANSPORT,
            "codex_write_mode": CODEX_WRITE_MODE,
        },
        "control_loop": {
            "enabled": CONTROL_LOOP_ENABLED,
            "force_for_all": CONTROL_LOOP_FORCE_FOR_ALL,
            "default_profile": CONTROL_LOOP_DEFAULT_PROFILE,
            "planner_agent": CONTROL_LOOP_PLANNER_AGENT,
            "critic_agent": CONTROL_LOOP_CRITIC_AGENT,
        },
        "quality_gates": {
            "strict_enabled": STRICT_QUALITY_GATES_ENABLED,
            "default_profile": STRICT_QUALITY_GATES_DEFAULT_PROFILE,
            "fix_retries": STRICT_QUALITY_GATES_FIX_RETRIES,
        },
        "ai_providers": {
            "priority": get_provider_priority(),
            "gemini_model": GEMINI_MODEL,
            "openrouter_model": OPENROUTER_MODEL,
            "ollama_default_model": OLLAMA_DEFAULT_MODEL,
            "gemini_key_set": bool(_s("GOOGLE_AI_API_KEY")),
            "groq_key_set": bool(_s("GROQ_API_KEY")),
            "openrouter_key_set": bool(_s("OPENROUTER_API_KEY")),
            "anthropic_key_set": bool(_s("ANTHROPIC_API_KEY")),
            "openai_key_set": bool(_s("OPENAI_API_KEY")),
        },
        "runtime_trace": {
            "enabled": RUNTIME_TRACE_ENABLED,
            "live_file": RUNTIME_TRACE_LIVE_FILE,
            "level": RUNTIME_TRACE_LEVEL,
            "detail_profile": RUNTIME_TRACE_DETAIL_PROFILE,
            "payload_mode": RUNTIME_TRACE_PAYLOAD_MODE,
            "db_enabled": RUNTIME_TRACE_DB_ENABLED,
        },
        "telegram": {
            "bot_token_set": bool(_s("TELEGRAM_BOT_TOKEN")),
            "allowed_user_id": ALLOWED_USER_ID,
            "tracker_enabled": TELEGRAM_TRACKER_ENABLED,
        },
        "auth": {
            "auth_token_set": bool(AUTH_TOKEN),
        },
        "db": {
            "path": DB_PATH,
        },
        "worker_projects_dir": WORKER_PROJECTS_DIR,
    }
