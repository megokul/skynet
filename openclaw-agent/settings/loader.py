"""
Unified Settings Loader for SKYNET Worker Agent.

This module provides a centralized settings loading mechanism for the agent.
It shares the same loader implementation as the gateway but with agent-specific
defaults and settings directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Import the shared loader implementation
import sys

_agent_settings_dir = Path(__file__).resolve().parent
_gateway_settings_dir = _agent_settings_dir.parent.parent / "openclaw-gateway" / "settings"

# Add gateway settings to path for shared loader
if str(_gateway_settings_dir) not in sys.path:
    sys.path.insert(0, str(_gateway_settings_dir))


class AgentSettingsLoader:
    """
    Settings loader for the worker agent.
    
    Loading precedence (highest to lowest):
    1. Environment variables
    2. .env file (if exists)
    3. settings.local.yaml (if exists)
    4. settings.yaml (agent defaults)
    """

    def __init__(
        self,
        settings_dir: Path | None = None,
        env_file: Path | None = None,
    ):
        """
        Initialize the agent settings loader.
        
        Args:
            settings_dir: Directory containing agent settings files.
            env_file: Path to .env file.
        """
        if settings_dir is None:
            settings_dir = Path(__file__).resolve().parent
        
        self._settings_dir = settings_dir
        self._settings_file = settings_dir / "settings.yaml"
        self._local_settings_file = settings_dir / "settings.local.yaml"
        self._env_file = env_file or (settings_dir.parent.parent / ".env.worker-agent")
        
        # Load all settings layers
        self._settings = self._load_all()

    def _load_all(self) -> dict[str, Any]:
        """Load all settings layers and merge them."""
        from loader import _load_dotenv, _load_yaml_settings, _is_secret_name, _parse_yaml_scalar
        
        # Layer 1: Base defaults from settings.yaml
        settings = _load_yaml_settings(self._settings_file)
        
        # Layer 2: Environment-specific overrides from settings.local.yaml
        local_settings = _load_yaml_settings(self._local_settings_file)
        settings.update(local_settings)
        
        # Layer 3: .env file (secrets and overrides)
        env_vars = _load_dotenv(self._env_file)
        for key, value in env_vars.items():
            if not _is_secret_name(key):
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
        """
        from loader import _is_secret_name, _parse_yaml_scalar
        
        # Environment variables always win
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
        """Get a list setting."""
        value = self.get(name, "", required)
        if not value:
            return default or []
        return [item.strip() for item in str(value).split(separator) if item.strip()]

    def all(self) -> dict[str, Any]:
        """Get all settings (for debugging)."""
        return dict(self._settings)


# ---------------------------------------------------------------------------
# Global Settings Instance
# ---------------------------------------------------------------------------

_global_loader: AgentSettingsLoader | None = None


def get_settings(force_reload: bool = False) -> AgentSettingsLoader:
    """Get the global agent settings loader instance."""
    global _global_loader
    
    if _global_loader is None or force_reload:
        _global_loader = AgentSettingsLoader()
    
    return _global_loader


def reload_settings() -> AgentSettingsLoader:
    """Force reload of agent settings."""
    return get_settings(force_reload=True)


# ---------------------------------------------------------------------------
# Convenience Functions
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
