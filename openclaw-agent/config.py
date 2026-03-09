"""
CHATHAN Worker — Configuration

Central configuration for security policy, connection settings,
allowed actions, path restrictions, and rate limiting.

All non-secret defaults live in settings/defaults.yaml.
Secrets must be set via environment variables or .env.worker-agent.
Environment variables always override YAML defaults.
"""

import os
import sys
from enum import Enum
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skynet.settings.loader import get_component_settings  # noqa: E402


_settings_loader = get_component_settings("agent")


def _setting(name: str, default=None, required: bool = False):
    return _settings_loader.get(name, default, required)


def _s(name: str, default: str = "", required: bool = False) -> str:
    return _settings_loader.get_str(name, default, required)


def _i(name: str, default: int = 0, required: bool = False) -> int:
    return _settings_loader.get_int(name, default, required)


def _b(name: str, default: bool = False, required: bool = False) -> bool:
    return _settings_loader.get_bool(name, default, required)


def _list(name: str, default: list[str] | None = None, required: bool = False) -> list[str]:
    return _settings_loader.get_list(name, default=default, required=required)


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


def _required_action_set(name: str) -> set[str]:
    items = _list(name, default=[], required=True)
    return {str(item).strip() for item in items if str(item).strip()}


def _required_string_map(name: str) -> dict[str, str]:
    raw = _setting(name, required=True)
    if not isinstance(raw, dict):
        raise ValueError(f"Required mapping setting '{name}' must be a dict")
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        key_text = str(key).strip().lower()
        value_text = str(value).strip()
        if key_text and value_text:
            normalized[key_text] = value_text
    if not normalized:
        raise ValueError(f"Required mapping setting '{name}' is empty")
    return normalized


# ---------------------------------------------------------------------------
# Action → Tier mapping
# Only actions listed in settings are permitted. Everything else is BLOCKED.
# ---------------------------------------------------------------------------
AUTO_ACTIONS: set[str] = _required_action_set("AUTO_ACTIONS")
CONFIRM_ACTIONS: set[str] = _required_action_set("CONFIRM_ACTIONS")

# Only these executables can be closed — anything else is rejected.
CLOSEABLE_APPS: dict[str, str] = _required_string_map("CLOSEABLE_APPS")

# Explicitly listed so the validator can log attempts against known-bad ops.
BLOCKED_ACTIONS: set[str] = _required_action_set("BLOCKED_ACTIONS")


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
