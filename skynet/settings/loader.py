"""
Unified Settings Loader for SKYNET Control Plane.

This module provides a centralized settings loading mechanism for the
SKYNET control plane API.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class SkynetSettingsLoader:
    """
    Settings loader for the SKYNET control plane.
    
    Loading precedence (highest to lowest):
    1. Environment variables
    2. .env file (if exists)
    3. settings.local.yaml (if exists)
    4. settings.yaml (control plane defaults)
    """

    def __init__(
        self,
        settings_dir: Path | None = None,
        env_file: Path | None = None,
    ):
        """
        Initialize the skynet settings loader.
        
        Args:
            settings_dir: Directory containing skynet settings files.
            env_file: Path to .env file.
        """
        if settings_dir is None:
            settings_dir = Path(__file__).resolve().parent
        
        self._settings_dir = settings_dir
        self._settings_file = settings_dir / "settings.yaml"
        self._local_settings_file = settings_dir / "settings.local.yaml"
        self._env_file = env_file or (settings_dir.parent.parent / ".env")
        
        # Load all settings layers
        self._settings = self._load_all()

    def _load_yaml_settings(self, path: Path) -> dict[str, Any]:
        """Load settings from a YAML file."""
        import re
        
        settings: dict[str, Any] = {}
        if not path.exists():
            return settings

        _settings_line_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
        
        def _strip_comment(raw: str) -> str:
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

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _settings_line_re.match(line)
            if not match:
                continue
            key, raw_value = match.group(1), match.group(2)
            value = _strip_comment(raw_value)
            if value == "":
                settings[key] = ""
            elif value.lower() in {"true", "yes", "on"}:
                settings[key] = True
            elif value.lower() in {"false", "no", "off"}:
                settings[key] = False
            elif re.fullmatch(r"[+-]?\d+", value):
                try:
                    settings[key] = int(value)
                except ValueError:
                    settings[key] = value
            elif len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                settings[key] = value[1:-1]
            else:
                settings[key] = value

        return settings

    def _load_dotenv(self, path: Path) -> dict[str, str]:
        """Load environment variables from a .env file."""
        import re
        
        env_vars: dict[str, str] = {}
        if not path.exists():
            return env_vars

        _env_name_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if _env_name_re.match(key):
                env_vars[key] = value

        return env_vars

    def _load_all(self) -> dict[str, Any]:
        """Load all settings layers and merge them."""
        # Layer 1: Base defaults from settings.yaml
        settings = self._load_yaml_settings(self._settings_file)
        
        # Layer 2: Environment-specific overrides from settings.local.yaml
        local_settings = self._load_yaml_settings(self._local_settings_file)
        settings.update(local_settings)
        
        # Layer 3: .env file (secrets and overrides)
        env_vars = self._load_dotenv(self._env_file)
        settings.update(env_vars)
        
        # Store env vars separately for secret access
        self._env_vars = env_vars
        
        return settings

    def get(self, name: str, default: Any = None, required: bool = False) -> Any:
        """
        Get a setting value.
        
        Precedence:
        1. Environment variable (always wins)
        2. Loaded settings from YAML files
        """
        # Environment variables always win
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value
        
        # Check loaded settings
        if name in self._settings:
            return self._settings[name]
        
        # Check if it's in .env
        if name in self._env_vars:
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

    def all(self) -> dict[str, Any]:
        """Get all settings (for debugging)."""
        return dict(self._settings)


# ---------------------------------------------------------------------------
# Global Settings Instance
# ---------------------------------------------------------------------------

_global_loader: SkynetSettingsLoader | None = None


def get_settings(force_reload: bool = False) -> SkynetSettingsLoader:
    """Get the global skynet settings loader instance."""
    global _global_loader
    
    if _global_loader is None or force_reload:
        _global_loader = SkynetSettingsLoader()
    
    return _global_loader


def reload_settings() -> SkynetSettingsLoader:
    """Force reload of skynet settings."""
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
