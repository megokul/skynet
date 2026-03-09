"""
CHATHAN Worker — Configuration

Central configuration for security policy, connection settings,
allowed actions, path restrictions, and rate limiting.

All non-secret defaults live in settings/defaults.yaml.
Secrets must be set via environment variables or .env.worker-agent.
Environment variables always override YAML defaults.
"""

import os
from enum import Enum

from settings.loader import get_str as _s, get_int as _i, get_bool as _b


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
GATEWAY_URL: str = _s("SKYNET_GATEWAY_URL") or _s("OPENCLAW_GATEWAY_URL")

# Pre-shared bearer token for WebSocket authentication.
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
AUTH_TOKEN: str = _s("SKYNET_AUTH_TOKEN") or _s("OPENCLAW_AUTH_TOKEN")

# Seconds between reconnection attempts after a drop.
RECONNECT_DELAY_SECONDS: int = _i("RECONNECT_DELAY_SECONDS", 5)
MAX_RECONNECT_DELAY_SECONDS: int = _i("MAX_RECONNECT_DELAY_SECONDS", 120)

# WebSocket ping interval to keep NAT/firewall mappings alive.
WS_PING_INTERVAL_SECONDS: int = _i("WS_PING_INTERVAL_SECONDS", 30)
WS_PING_TIMEOUT_SECONDS: int = _i("WS_PING_TIMEOUT_SECONDS", 10)
WORKER_ID: str = _s("SKYNET_WORKER_ID", "worker-primary").strip() or "worker-primary"
AGENT_HEARTBEAT_SECONDS: int = _i("AGENT_HEARTBEAT_SECONDS", 15)
AGENT_RESULT_CACHE_TTL_SECONDS: int = _i("AGENT_RESULT_CACHE_TTL_SECONDS", 900)
AGENT_LOG_MIRROR_DIR: str = (
    _s("SKYNET_AGENT_LOG_MIRROR_DIR")
    or _s("SKYNET_TRACE_MIRROR_LOG_DIR")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
)


# ---------------------------------------------------------------------------
# Risk Tiers
# ---------------------------------------------------------------------------
class Tier(str, Enum):
    """
    Tier.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `Tier`.
    """

    AUTO = "AUTO"         # Execute immediately, no confirmation.
    CONFIRM = "CONFIRM"   # Prompt operator in terminal before executing.
    BLOCKED = "BLOCKED"   # Never execute — reject instantly.


# ---------------------------------------------------------------------------
# Action → Tier mapping
# Only actions listed here are permitted. Everything else is BLOCKED.
# ---------------------------------------------------------------------------
AUTO_ACTIONS: set[str] = {
    "git_status",
    "web_search",
    "run_tests",
    "lint_project",
    "start_dev_server",
    "build_project",
    "file_read",
    "list_directory",
    "ollama_chat",
    "check_coding_agents",
    "trace_runtime_probe",
    "cancel_runtime_session",
}

CONFIRM_ACTIONS: set[str] = {
    "git_commit",
    "install_dependencies",
    "file_write",
    "create_directory",
    "git_init",
    "git_add_all",
    "git_push",
    "gh_create_repo",
    "open_in_vscode",
    "run_coding_agent",
    "exec_command",
    "docker_build",
    "docker_compose_up",
    "close_app",
    "zip_project",
}

# Hardcoded allowlist of process names that close_app can terminate.
# Only these executables can be closed — anything else is rejected.
CLOSEABLE_APPS: dict[str, str] = {
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
}

# Explicitly listed so the validator can log attempts against known-bad ops.
BLOCKED_ACTIONS: set[str] = {
    "shell_exec",
    "format_disk",
    "modify_registry",
    "manage_users",
    "firewall_change",
    "download_exec",
    "eval_code",
}


# ---------------------------------------------------------------------------
# Path restrictions
# ---------------------------------------------------------------------------
def _parse_allowed_roots() -> list[str]:
    """
    Resolve path-jail roots from environment or platform defaults.

    Environment:
      SKYNET_ALLOWED_ROOTS / OPENCLAW_ALLOWED_ROOTS
      Delimiters: ';' or ',' (portable across Windows/Linux).
    """
    raw = _s("SKYNET_ALLOWED_ROOTS") or _s("OPENCLAW_ALLOWED_ROOTS")
    if raw:
        roots = [p.strip() for p in raw.replace(",", ";").split(";") if p.strip()]
        if roots:
            return roots

    # No SKYNET_ALLOWED_ROOTS / OPENCLAW_ALLOWED_ROOTS configured.
    # Using safe user-home-relative defaults. Set the env var to restrict
    # access to specific directories on this machine.
    if os.name == "nt":
        home = os.path.expanduser("~")
        return [
            os.path.join(home, "Projects"),
            os.path.join(home, "Documents"),
        ]

    # Linux/macOS defaults.
    return [os.path.expanduser("~"), "/tmp"]


ALLOWED_ROOTS: list[str] = _parse_allowed_roots()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
RATE_LIMIT_PER_MINUTE: int = _i("RATE_LIMIT_PER_MINUTE", 120)


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------
# When True, ALL actions (including AUTO) are rejected.
# Toggle at runtime via the /emergency-stop control message.
EMERGENCY_STOP: bool = False


# ---------------------------------------------------------------------------
# Logging / Audit
# ---------------------------------------------------------------------------
AUDIT_LOG_DIR: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "logs",
)
AUDIT_LOG_FILE: str = "audit.jsonl"
LOG_LEVEL: str = _s("SKYNET_LOG_LEVEL") or _s("OPENCLAW_LOG_LEVEL", "INFO")
