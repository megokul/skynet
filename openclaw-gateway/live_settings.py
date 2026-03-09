from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
GATEWAY_ROOT = ROOT / "openclaw-gateway"

for candidate in (str(ROOT), str(GATEWAY_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from skynet.settings.loader import SettingsLoader  # noqa: E402


def bootstrap_gateway_runtime(*, override: bool = False) -> tuple[SettingsLoader, int]:
    loader = SettingsLoader(component="gateway", settings_dir=GATEWAY_ROOT / "settings")
    hydrated = loader.load_into_environ(override=override)
    return loader, hydrated


def trace_gateway_runtime(trace_fn: Callable[..., None], *, override: bool = False) -> SettingsLoader:
    loader, hydrated = bootstrap_gateway_runtime(override=override)
    loaded_files: list[str] = []
    if loader.settings_file.exists():
        loaded_files.append(str(loader.settings_file))
    if loader.local_settings_file.exists():
        loaded_files.append(str(loader.local_settings_file))
    if loader.env_file.exists():
        loaded_files.append(str(loader.env_file))
    trace_fn(
        "env.settings",
        loaded=bool(loaded_files),
        files=loaded_files,
        hydrated=hydrated,
    )
    return loader


def build_gateway_runtime_env(
    base_env: dict[str, str] | None = None,
    *,
    override: bool = False,
) -> dict[str, str]:
    loader = SettingsLoader(component="gateway", settings_dir=GATEWAY_ROOT / "settings")
    return loader.build_environ(base_env or dict(os.environ), override=override)
