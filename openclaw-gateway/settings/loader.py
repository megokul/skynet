"""
Unified Settings Loader for SKYNET/OpenClaw.

This module provides a centralized settings loading mechanism that:
1. Loads base defaults from settings.yaml
2. Applies environment-specific overrides from settings.local.yaml
3. Applies secret overrides from .env files
4. Applies final overrides from environment variables

All non-secret settings should be defined in settings.yaml files.
Secrets must remain in environment variables or .env files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SETTINGS_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

# Secret-bearing names that must come from environment variables only.
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
    "DASHSCOPE_API_KEY",
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

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def _is_secret_name(name: str) -> bool:
    """Check if a setting name should be env-only (secret-bearing)."""
    key = name.strip().upper()
    if not key:
        return False
    if key in _SECRET_EXACT:
        return True
    return any(key.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _strip_inline_comment(raw: str) -> str:
    """Remove inline comments from a YAML line, respecting quotes."""
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
    """Parse a YAML scalar value into Python type."""
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
    if re.fullmatch(r"[+-]?\d+\.\d+", value):
        try:
            return float(value)
        except ValueError:
            return value
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_yaml_settings(path: Path) -> dict[str, Any]:
    """Load settings from a YAML file into a dictionary."""
    settings: dict[str, Any] = {}
    if not path.exists():
        return settings

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

    return settings


def _load_dotenv(path: Path) -> dict[str, str]:
    """Load environment variables from a .env file."""
    env_vars: dict[str, str] = {}
    if not path.exists():
        return env_vars

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Remove surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if _ENV_NAME_RE.match(key):
            env_vars[key] = value

    return env_vars


# ---------------------------------------------------------------------------
# Settings Loader Class
# ---------------------------------------------------------------------------


class SettingsLoader:
    """
    Unified settings loader with layered configuration.
    
    Loading precedence (highest to lowest):
    1. Environment variables
    2. .env file (if exists)
    3. settings.local.yaml (if exists, environment-specific overrides)
    4. settings.yaml (base defaults)
    """

    def __init__(
        self,
        settings_dir: Path | None = None,
        env_file: Path | None = None,
        settings_file: Path | None = None,
        local_settings_file: Path | None = None,
    ):
        """
        Initialize the settings loader.
        
        Args:
            settings_dir: Directory containing settings files. Defaults to this module's directory.
            env_file: Path to .env file. Defaults to .env in project root.
            settings_file: Path to base settings.yaml. Defaults to settings/settings.yaml.
            local_settings_file: Path to settings.local.yaml. Defaults to settings/settings.local.yaml.
        """
        if settings_dir is None:
            settings_dir = Path(__file__).resolve().parent
        
        self._settings_dir = settings_dir
        self._settings_file = settings_file or (settings_dir / "settings.yaml")
        self._local_settings_file = local_settings_file or (settings_dir / "settings.local.yaml")
        self._env_file = env_file or (settings_dir.parent.parent / ".env")
        
        # Load all settings layers
        self._settings = self._load_all()

    def _load_all(self) -> dict[str, Any]:
        """Load all settings layers and merge them."""
        # Layer 1: Base defaults from settings.yaml
        settings = _load_yaml_settings(self._settings_file)
        
        # Layer 2: Environment-specific overrides from settings.local.yaml
        local_settings = _load_yaml_settings(self._local_settings_file)
        settings.update(local_settings)
        
        # Layer 3: .env file (secrets and overrides)
        env_vars = _load_dotenv(self._env_file)
        for key, value in env_vars.items():
            if not _is_secret_name(key):
                # Non-secret env vars can override settings
                settings[key] = _parse_yaml_scalar(value)
        
        # Store env vars separately for secret access
        self._env_vars = env_vars
        
        return settings

    def get(self, name: str, default: Any = None, required: bool = False) -> Any:
        """
        Get a setting value.
        
        Precedence:
        1. Environment variable (always wins for secrets)
        2. Loaded settings from YAML files
        
        Args:
            name: Setting name
            default: Default value if not found (should be None for required settings)
            required: If True, raise error when setting is missing
            
        Returns:
            Setting value
            
        Raises:
            ValueError: If required setting is not found
        """
        # Environment variables always win (especially for secrets)
        env_value = os.environ.get(name)
        if env_value is not None:
            return _parse_yaml_scalar(env_value)
        
        # Check loaded settings
        if name in self._settings:
            return self._settings[name]
        
        # Check if it's a secret that should come from .env
        if _is_secret_name(name) and name in self._env_vars:
            return self._env_vars[name]
        
        # Required setting missing
        if required:
            raise ValueError(f"Required setting '{name}' is not defined")
        
        return default

    def get_str(self, name: str, default: str = "", required: bool = False) -> str:
        """Get a string setting."""
        value = self.get(name, default, required)
        return str(value) if value is not None else default

    def get_int(self, name: str, default: int = 0, required: bool = False) -> int:
        """Get an integer setting."""
        value = self.get(name, default, required)
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def get_bool(self, name: str, default: bool = False, required: bool = False) -> bool:
        """Get a boolean setting."""
        value = self.get(name, default, required)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def get_list(
        self,
        name: str,
        separator: str = ",",
        default: list[str] | None = None,
        required: bool = False,
    ) -> list[str]:
        """Get a list setting (comma-separated or custom separator)."""
        value = self.get(name, "", required)
        if not value:
            return default or []
        return [item.strip() for item in str(value).split(separator) if item.strip()]

    def all(self) -> dict[str, Any]:
        """Get all settings (for debugging)."""
        return dict(self._settings)

    @property
    def settings_file(self) -> Path:
        """Path to the base settings file."""
        return self._settings_file

    @property
    def local_settings_file(self) -> Path:
        """Path to the local settings file."""
        return self._local_settings_file


# ---------------------------------------------------------------------------
# Global Settings Instance
# ---------------------------------------------------------------------------

# Singleton instance for global settings access
_global_loader: SettingsLoader | None = None


def get_settings(
    settings_dir: Path | None = None,
    force_reload: bool = False,
) -> SettingsLoader:
    """
    Get the global settings loader instance.
    
    Args:
        settings_dir: Optional custom settings directory
        force_reload: Force reload of settings
        
    Returns:
        SettingsLoader instance
    """
    global _global_loader
    
    if _global_loader is None or force_reload:
        _global_loader = SettingsLoader(settings_dir=settings_dir)
    
    return _global_loader


def reload_settings() -> SettingsLoader:
    """Force reload of global settings."""
    return get_settings(force_reload=True)


# ---------------------------------------------------------------------------
# Convenience Functions (for backward compatibility during migration)
# ---------------------------------------------------------------------------

def get_setting(name: str, default: Any = None, required: bool = False) -> Any:
    """Get a setting value using the global loader."""
    return get_settings().get(name, default, required)


def get_str(name: str, default: str = "", required: bool = False) -> str:
    """Get a string setting."""
    return get_settings().get_str(name, default, required)


def get_int(name: str, default: int = 0, required: bool = False) -> int:
    """Get an integer setting."""
    return get_settings().get_int(name, default, required)


def get_bool(name: str, default: bool = False, required: bool = False) -> bool:
    """Get a boolean setting."""
    return get_settings().get_bool(name, default, required)


def get_list(
    name: str,
    separator: str = ",",
    default: list[str] | None = None,
    required: bool = False,
) -> list[str]:
    """Get a list setting."""
    return get_settings().get_list(name, separator, default, required)
