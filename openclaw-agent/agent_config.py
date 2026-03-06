from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType


def _load() -> ModuleType:
    module_name = "_skynet_agent_config"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
    spec = importlib.util.spec_from_file_location(module_name, config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load agent config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_CFG = _load()


def __getattr__(name: str):
    return getattr(_CFG, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_CFG)))
