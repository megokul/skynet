"""
Shared layered settings loader for SKYNET components.

Policy:
- Structured, non-secret defaults live in component settings YAML files.
- Local overrides live in component-local YAML files.
- Secrets live in environment variables or component env files.
- Environment variables always win over file-based defaults.

This module is the single settings-loading implementation for:
- `skynet/`
- `openclaw-gateway/`
- `openclaw-agent/`
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[2]

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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

_PROJECT_PREFIXES = (
    "SKYNET_",
    "OPENCLAW_",
    "TELEGRAM_",
    "GOOGLE_",
    "GROQ_",
    "OPENROUTER_",
    "DEEPSEEK_",
    "OPENAI_",
    "ANTHROPIC_",
    "BRAVE_",
    "GH_",
    "GITHUB_",
    "AWS_",
    "OLLAMA_",
)

_PROJECT_EXACT = {
    "AI_PROVIDER_PRIORITY",
    "ALLOWED_USER_ID",
    "AUTH_TOKEN",
    "CORS_ORIGINS",
    "DB_PATH",
    "GATEWAY_URL",
    "LOG_LEVEL",
    "RATE_LIMIT_PER_MINUTE",
    "WORKER_ID",
}

_COMPONENT_ALIASES = {
    "control": "control",
    "control_plane": "control",
    "control-plane": "control",
    "skynet": "control",
    "gateway": "gateway",
    "openclaw-gateway": "gateway",
    "agent": "agent",
    "openclaw-agent": "agent",
}


@dataclass(frozen=True)
class ComponentSettingsSpec:
    name: str
    settings_dir: Path
    defaults_file: str
    local_file: str
    default_env_file: str
    settings_override_env_vars: tuple[str, ...]
    local_override_env_vars: tuple[str, ...]
    env_override_env_vars: tuple[str, ...]


def _component_specs() -> dict[str, ComponentSettingsSpec]:
    return {
        "control": ComponentSettingsSpec(
            name="control",
            settings_dir=ROOT / "skynet" / "settings",
            defaults_file="defaults.yaml",
            local_file="settings.local.yaml",
            default_env_file=".env",
            settings_override_env_vars=("SKYNET_CONTROL_SETTINGS_FILE",),
            local_override_env_vars=("SKYNET_CONTROL_LOCAL_SETTINGS_FILE",),
            env_override_env_vars=("SKYNET_CONTROL_ENV_FILE", "SKYNET_ENV_FILE"),
        ),
        "gateway": ComponentSettingsSpec(
            name="gateway",
            settings_dir=ROOT / "openclaw-gateway" / "settings",
            defaults_file="settings.yaml",
            local_file="settings.local.yaml",
            default_env_file=".env",
            settings_override_env_vars=("SKYNET_SETTINGS_FILE",),
            local_override_env_vars=("SKYNET_LOCAL_SETTINGS_FILE",),
            env_override_env_vars=("SKYNET_ENV_FILE", "OPENCLAW_ENV_FILE"),
        ),
        "agent": ComponentSettingsSpec(
            name="agent",
            settings_dir=ROOT / "openclaw-agent" / "settings",
            defaults_file="defaults.yaml",
            local_file="settings.local.yaml",
            default_env_file=".env.worker-agent",
            settings_override_env_vars=("SKYNET_AGENT_SETTINGS_FILE",),
            local_override_env_vars=("SKYNET_AGENT_LOCAL_SETTINGS_FILE",),
            env_override_env_vars=(
                "SKYNET_AGENT_ENV_FILE",
                "SKYNET_ENV_FILE",
                "OPENCLAW_ENV_FILE",
            ),
        ),
    }


def _normalize_component_name(component: str) -> str:
    normalized = str(component or "").strip().lower()
    resolved = _COMPONENT_ALIASES.get(normalized)
    if not resolved:
        raise ValueError(f"Unsupported settings component: {component!r}")
    return resolved


def _resolve_override_path(raw: str, *, repo_root: Path) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate


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


def _parse_scalar(raw: str) -> Any:
    value = _strip_inline_comment(str(raw))
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


def _is_secret_name(name: str) -> bool:
    key = str(name or "").strip().upper()
    if not key:
        return False
    if key in _SECRET_EXACT:
        return True
    return any(key.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _is_project_setting_name(name: str) -> bool:
    key = str(name or "").strip().upper()
    if not key:
        return False
    if key in _PROJECT_EXACT:
        return True
    return key.startswith(_PROJECT_PREFIXES)


def _stringify_env_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return str(value)


def _load_yaml_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return {}

    settings: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not _ENV_NAME_RE.match(key):
            continue
        settings[key] = "" if value is None else value
    return settings


def _load_dotenv(path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    if not path.exists():
        return env_vars

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if _ENV_NAME_RE.match(key):
            env_vars[key] = value
    return env_vars


class SettingsLoader:
    """
    Layered settings loader for one repo component.

    Precedence:
    1. Process environment
    2. Component env file
    3. Local YAML overrides
    4. Component defaults YAML
    """

    def __init__(
        self,
        *,
        component: str = "control",
        settings_dir: Path | None = None,
        env_file: Path | None = None,
        settings_file: Path | None = None,
        local_settings_file: Path | None = None,
        repo_root: Path | None = None,
    ):
        component_name = _normalize_component_name(component)
        spec = _component_specs()[component_name]

        self._component = component_name
        self._repo_root = repo_root or ROOT
        self._settings_dir = settings_dir or spec.settings_dir
        self._spec = spec

        settings_override = self._first_override_path(
            spec.settings_override_env_vars,
            repo_root=self._repo_root,
        )
        local_override = self._first_override_path(
            spec.local_override_env_vars,
            repo_root=self._repo_root,
        )
        env_override = self._first_override_path(
            spec.env_override_env_vars,
            repo_root=self._repo_root,
        )

        if settings_override is not None and settings_file is None:
            suffixes = (".local.yaml", ".local.yml")
            if settings_override.name.lower().endswith(suffixes):
                local_settings_file = local_settings_file or settings_override
            else:
                settings_file = settings_override

        self._settings_file = settings_file or (self._settings_dir / spec.defaults_file)
        self._local_settings_file = (
            local_settings_file or local_override or (self._settings_dir / spec.local_file)
        )
        self._env_file = env_file or env_override or (self._repo_root / spec.default_env_file)

        self._settings = self._load_all()

    def _first_override_path(
        self,
        env_names: Iterable[str],
        *,
        repo_root: Path,
    ) -> Path | None:
        for env_name in env_names:
            candidate = _resolve_override_path(os.environ.get(env_name, ""), repo_root=repo_root)
            if candidate is not None:
                return candidate
        return None

    def _load_all(self) -> dict[str, Any]:
        settings = _load_yaml_settings(self._settings_file)
        settings.update(_load_yaml_settings(self._local_settings_file))

        self._dotenv_vars = _load_dotenv(self._env_file)
        for key, value in self._dotenv_vars.items():
            if not _is_secret_name(key):
                settings[key] = _parse_scalar(value)
        return settings

    def get(self, name: str, default: Any = None, required: bool = False) -> Any:
        env_value = os.environ.get(name)
        if env_value is not None:
            return _parse_scalar(env_value)

        if name in self._settings:
            return self._settings[name]

        if _is_secret_name(name) and name in self._dotenv_vars:
            return self._dotenv_vars[name]

        if required:
            raise ValueError(f"Required setting '{name}' is not defined")
        return default

    def get_str(self, name: str, default: str = "", required: bool = False) -> str:
        value = self.get(name, default, required)
        return str(value) if value is not None else default

    def get_int(self, name: str, default: int = 0, required: bool = False) -> int:
        value = self.get(name, default, required)
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def get_bool(self, name: str, default: bool = False, required: bool = False) -> bool:
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
        value = self.get(name, None, required)
        if value is None or value == "":
            return list(default or [])
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, tuple | set):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(separator) if item.strip()]

    def all(self) -> dict[str, Any]:
        return dict(self._settings)

    def effective(self) -> dict[str, Any]:
        merged = dict(self._settings)
        for key, value in self._dotenv_vars.items():
            if _is_secret_name(key):
                merged[key] = value
        for key, value in os.environ.items():
            if not _ENV_NAME_RE.match(key):
                continue
            if key in merged or _is_secret_name(key) or _is_project_setting_name(key):
                merged[key] = _parse_scalar(value)
        return merged

    def export_values(self) -> dict[str, str]:
        return {
            key: _stringify_env_value(value)
            for key, value in self.effective().items()
        }

    def load_into_environ(self, *, override: bool = False) -> int:
        hydrated = 0
        for key, value in self.export_values().items():
            if not override and os.environ.get(key) is not None:
                continue
            os.environ[key] = value
            hydrated += 1
        return hydrated

    def build_environ(
        self,
        base_env: Mapping[str, str] | None = None,
        *,
        override: bool = False,
    ) -> dict[str, str]:
        env = dict(base_env or {})
        for key, value in self.export_values().items():
            if not override and key in env:
                continue
            env[key] = value
        return env

    @property
    def component(self) -> str:
        return self._component

    @property
    def settings_dir(self) -> Path:
        return self._settings_dir

    @property
    def settings_file(self) -> Path:
        return self._settings_file

    @property
    def local_settings_file(self) -> Path:
        return self._local_settings_file

    @property
    def env_file(self) -> Path:
        return self._env_file


SkynetSettingsLoader = SettingsLoader

_COMPONENT_LOADERS: dict[tuple[str, str], SettingsLoader] = {}


def get_component_settings(
    component: str = "control",
    *,
    settings_dir: Path | None = None,
    force_reload: bool = False,
) -> SettingsLoader:
    normalized = _normalize_component_name(component)
    resolved_dir = str((settings_dir or _component_specs()[normalized].settings_dir).resolve())
    cache_key = (normalized, resolved_dir)
    if force_reload or cache_key not in _COMPONENT_LOADERS:
        _COMPONENT_LOADERS[cache_key] = SettingsLoader(
            component=normalized,
            settings_dir=settings_dir,
        )
    return _COMPONENT_LOADERS[cache_key]


def reload_component_settings(component: str = "control") -> SettingsLoader:
    return get_component_settings(component, force_reload=True)


def bootstrap_component_env(
    component: str = "control",
    *,
    override: bool = False,
    settings_dir: Path | None = None,
) -> SettingsLoader:
    loader = get_component_settings(component, settings_dir=settings_dir)
    loader.load_into_environ(override=override)
    return loader


def merge_component_values(components: Iterable[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for component in components:
        loader = get_component_settings(component)
        merged.update(loader.export_values())
    return merged


def write_component_env_file(path: Path, components: Iterable[str]) -> Path:
    merged = merge_component_values(components)
    lines = [f"{key}={merged[key]}" for key in sorted(merged)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def get_settings(
    settings_dir: Path | None = None,
    force_reload: bool = False,
) -> SettingsLoader:
    return get_component_settings("control", settings_dir=settings_dir, force_reload=force_reload)


def reload_settings() -> SettingsLoader:
    return get_settings(force_reload=True)


def get_setting(name: str, default: Any = None, required: bool = False) -> Any:
    return get_settings().get(name, default, required)


def get_str(name: str, default: str = "", required: bool = False) -> str:
    return get_settings().get_str(name, default, required)


def get_int(name: str, default: int = 0, required: bool = False) -> int:
    return get_settings().get_int(name, default, required)


def get_bool(name: str, default: bool = False, required: bool = False) -> bool:
    return get_settings().get_bool(name, default, required)


def get_list(
    name: str,
    separator: str = ",",
    default: list[str] | None = None,
    required: bool = False,
) -> list[str]:
    return get_settings().get_list(name, separator, default, required)
