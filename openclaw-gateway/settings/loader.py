"""
Gateway settings wrapper.

The shared implementation lives in `skynet.settings.loader`.
This module keeps the existing gateway import surface stable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skynet.settings.loader import SettingsLoader as _SharedSettingsLoader, get_component_settings  # noqa: E402


class SettingsLoader(_SharedSettingsLoader):
    def __init__(
        self,
        settings_dir: Path | None = None,
        env_file: Path | None = None,
        settings_file: Path | None = None,
        local_settings_file: Path | None = None,
    ):
        super().__init__(
            component="gateway",
            settings_dir=settings_dir,
            env_file=env_file,
            settings_file=settings_file,
            local_settings_file=local_settings_file,
        )


def get_settings(
    settings_dir: Path | None = None,
    force_reload: bool = False,
) -> SettingsLoader:
    return get_component_settings(
        "gateway",
        settings_dir=settings_dir,
        force_reload=force_reload,
    )


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


def load_into_environ(*, override: bool = False) -> int:
    loader = get_settings()
    return loader.load_into_environ(override=override)
