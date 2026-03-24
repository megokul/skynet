from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any

import gateway_config as bot_cfg

from ssh_tunnel_support import env_bool, env_int, parse_provider_priority, parse_roots


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSHExecutorConfig:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    key_path: str
    connect_timeout: int
    command_timeout: int
    remote_os: str
    strict_host_key: bool
    allowed_roots: list[str]
    brave_search_api_key: str
    coding_bins: dict[str, str]
    coding_prefix: dict[str, list[str]]
    codex_write_mode: str
    qwen_policy: dict[str, Any]
    closeable_apps: dict[str, str]
    health_cache_seconds: int
    max_parallel: int
    circuit_breaker_seconds: int
    capacity_backoff_seconds: int
    health_probe_timeout: int
    cline_auto_switch: bool
    cline_provider_priority: list[str]
    cline_provider_base_urls: dict[str, str]
    claude_permission_mode: str
    claude_disable_slash_commands: bool
    claude_dangerously_skip_permissions: bool


def load_ssh_executor_config() -> SSHExecutorConfig:
    mode = bot_cfg.get_str("OPENCLAW_EXECUTION_MODE", "").strip().lower()
    enabled = mode in {"ssh", "ssh_tunnel", "tunnel", "ssh-only"} or env_bool(
        "OPENCLAW_SSH_FALLBACK_ENABLED",
        False,
    )

    host = bot_cfg.get_str("OPENCLAW_SSH_HOST", "127.0.0.1").strip()
    username = bot_cfg.get_str("OPENCLAW_SSH_USER", "").strip()
    password = bot_cfg.get_str("OPENCLAW_SSH_PASSWORD", "")
    key_path = bot_cfg.get_str("OPENCLAW_SSH_KEY_PATH", "").strip()
    if key_path.startswith("/app/") and not os.path.exists("/.dockerenv"):
        _LOG.warning(
            "OPENCLAW_SSH_KEY_PATH=%s looks container-scoped, but process appears host-local.",
            key_path,
        )

    remote_os = bot_cfg.get_str("OPENCLAW_SSH_REMOTE_OS", "windows").strip().lower()
    configured_codex_write_mode = bot_cfg.get_str(
        "SKYNET_CODEX_WRITE_MODE",
        str(getattr(bot_cfg, "CODEX_WRITE_MODE", "danger_full_access") or "danger_full_access"),
    ).strip().lower()
    if configured_codex_write_mode not in {"danger_full_access", "workspace_write", "read_only"}:
        _LOG.warning(
            "Invalid SKYNET_CODEX_WRITE_MODE=%r, defaulting to danger_full_access",
            configured_codex_write_mode,
        )
        configured_codex_write_mode = "danger_full_access"

    allowed_permission_modes = {
        "acceptEdits",
        "bypassPermissions",
        "default",
        "dontAsk",
        "plan",
    }
    configured_permission_mode = bot_cfg.get_str(
        "OPENCLAW_SSH_CLAUDE_PERMISSION_MODE",
        "bypassPermissions",
    ).strip()
    if configured_permission_mode and configured_permission_mode not in allowed_permission_modes:
        _LOG.warning(
            "Invalid OPENCLAW_SSH_CLAUDE_PERMISSION_MODE=%r, falling back to bypassPermissions",
            configured_permission_mode,
        )
        configured_permission_mode = "bypassPermissions"

    return SSHExecutorConfig(
        enabled=enabled,
        host=host,
        port=env_int("OPENCLAW_SSH_PORT", 2222),
        username=username,
        password=password,
        key_path=key_path,
        connect_timeout=env_int("OPENCLAW_SSH_CONNECT_TIMEOUT", 4),
        command_timeout=env_int("OPENCLAW_SSH_COMMAND_TIMEOUT", 180),
        remote_os=remote_os,
        strict_host_key=env_bool("OPENCLAW_SSH_STRICT_HOST_KEY", False),
        allowed_roots=parse_roots(bot_cfg.get_str("OPENCLAW_SSH_ALLOWED_ROOTS", ""), remote_os),
        brave_search_api_key=bot_cfg.BRAVE_SEARCH_API_KEY,
        coding_bins={
            "codex": bot_cfg.get_str("OPENCLAW_SSH_CODEX_BIN", "codex"),
            "claude": bot_cfg.get_str("OPENCLAW_SSH_CLAUDE_BIN", "claude"),
            "cline": bot_cfg.get_str("OPENCLAW_SSH_CLINE_BIN", "cline"),
            "qwen": bot_cfg.get_str("OPENCLAW_QWEN_BIN", bot_cfg.get_str("SKYNET_QWEN_BIN", "qwen")),
        },
        coding_prefix={
            "codex": ["exec"],
            "claude": ["-p"],
            "cline": ["-p"],
        },
        codex_write_mode=configured_codex_write_mode,
        qwen_policy=bot_cfg.get_qwen_execution_policy(),
        closeable_apps={
            "chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "notepad": "notepad.exe",
            "code": "Code.exe",
            "explorer": "explorer.exe",
            "slack": "slack.exe",
            "discord": "Discord.exe",
            "spotify": "Spotify.exe",
            "teams": "Teams.exe",
        },
        health_cache_seconds=env_int("OPENCLAW_SSH_HEALTH_CACHE_SECONDS", 15),
        max_parallel=max(1, env_int("OPENCLAW_SSH_MAX_PARALLEL", bot_cfg.SSH_MAX_PARALLEL)),
        circuit_breaker_seconds=max(
            0,
            env_int("OPENCLAW_SSH_CIRCUIT_BREAKER_SECONDS", bot_cfg.SSH_CIRCUIT_BREAKER_SECONDS),
        ),
        capacity_backoff_seconds=max(
            1,
            env_int("OPENCLAW_SSH_CAPACITY_BACKOFF_SECONDS", bot_cfg.SSH_CAPACITY_BACKOFF_SECONDS),
        ),
        health_probe_timeout=max(
            1,
            env_int("OPENCLAW_SSH_HEALTH_PROBE_TIMEOUT", bot_cfg.SSH_HEALTH_PROBE_TIMEOUT),
        ),
        cline_auto_switch=env_bool("OPENCLAW_CLINE_AUTO_SWITCH", True),
        cline_provider_priority=parse_provider_priority(
            bot_cfg.get_str("OPENCLAW_CLINE_PROVIDER_PRIORITY", ""),
        ),
        cline_provider_base_urls={
            "gemini": bot_cfg.get_str("OPENCLAW_CLINE_GEMINI_BASE_URL", "").strip(),
            "deepseek": bot_cfg.get_str("OPENCLAW_CLINE_DEEPSEEK_BASE_URL", "").strip(),
            "groq": bot_cfg.get_str("OPENCLAW_CLINE_GROQ_BASE_URL", "").strip(),
            "openrouter": bot_cfg.get_str("OPENCLAW_CLINE_OPENROUTER_BASE_URL", "").strip(),
            "openai": bot_cfg.get_str("OPENCLAW_CLINE_OPENAI_BASE_URL", "").strip(),
            "anthropic": bot_cfg.get_str("OPENCLAW_CLINE_ANTHROPIC_BASE_URL", "").strip(),
        },
        claude_permission_mode=configured_permission_mode,
        claude_disable_slash_commands=env_bool(
            "OPENCLAW_SSH_CLAUDE_DISABLE_SLASH_COMMANDS",
            False,
        ),
        claude_dangerously_skip_permissions=env_bool(
            "OPENCLAW_SSH_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS",
            False,
        ),
    )
