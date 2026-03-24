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


def auto_detect_env_file() -> None:
    """Load base .env then layer .env.local-e2e on top (Windows, when unset)."""
    if sys.platform == "win32" and not os.environ.get("SKYNET_ENV_FILE"):
        # Load base .env first so TELEGRAM_BOT_TOKEN etc. are available
        base_env = ROOT / ".env"
        if base_env.exists():
            for raw_line in base_env.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
        # Then point the loader at .env.local-e2e for E2E-specific overrides
        candidate = ROOT / ".env.local-e2e"
        if candidate.exists():
            os.environ["SKYNET_ENV_FILE"] = str(candidate)


def bootstrap_gateway_runtime(*, override: bool = False) -> tuple[SettingsLoader, int]:
    auto_detect_env_file()
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
