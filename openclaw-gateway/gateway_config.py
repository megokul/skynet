from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

_THIS_MODULE = sys.modules[__name__]


def _load() -> ModuleType:
    module_name = "_skynet_gateway_config"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
    spec = importlib.util.spec_from_file_location(module_name, config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load gateway config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_CFG = _load()


def __getattr__(name: str):
    return getattr(_CFG, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_CFG)))


# ---------------------------------------------------------------------------
# effective_orchestration_mode — overridden here so that monkeypatch.setattr
# on this proxy module (gateway.cfg) is correctly reflected.  When tests do:
#   monkeypatch.setattr(gateway.cfg, "ORCHESTRATION_MODE", "acp_first")
# Python writes to gateway_config.__dict__, which is what globals() returns
# inside functions defined in THIS file.
# ---------------------------------------------------------------------------

def effective_orchestration_mode() -> str:
    """Return the effective orchestration mode, respecting test monkeypatching."""
    _g = globals()
    # Prefer values set directly on this proxy module (e.g. by monkeypatch),
    # then fall back to the underlying config module.
    mode = str(
        _g.get("ORCHESTRATION_MODE") if "ORCHESTRATION_MODE" in _g else getattr(_CFG, "ORCHESTRATION_MODE", "legacy")
    ).strip().lower() or "legacy"
    allow_acp_with_ssh = bool(
        _g.get("ORCHESTRATION_ALLOW_ACP_WITH_SSH") if "ORCHESTRATION_ALLOW_ACP_WITH_SSH" in _g else getattr(_CFG, "ORCHESTRATION_ALLOW_ACP_WITH_SSH", False)
    )
    # is_ssh_execution_mode reads from os.environ directly so monkeypatch.setenv works.
    ssh_modes = {"ssh", "ssh_tunnel", "tunnel", "ssh-only"}
    exec_mode = (os.environ.get("OPENCLAW_EXECUTION_MODE") or "").strip().lower()
    is_ssh = exec_mode in ssh_modes
    if mode == "acp_first" and is_ssh and not allow_acp_with_ssh:
        return "legacy"
    return mode
