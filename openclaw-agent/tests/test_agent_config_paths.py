from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_agent_config():
    module_path = REPO_ROOT / "openclaw-agent" / "config.py"
    spec = importlib.util.spec_from_file_location("agent_config_paths", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load agent config")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_log_paths_resolve_relative_to_repo_root(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_AGENT_LOG_MIRROR_DIR", "logs/agent-mirror")
    monkeypatch.setenv("AUDIT_LOG_DIR", "openclaw-agent/logs")

    cfg = _load_agent_config()

    assert Path(cfg.AGENT_LOG_MIRROR_DIR) == REPO_ROOT / "logs" / "agent-mirror"
    assert Path(cfg.AUDIT_LOG_DIR) == REPO_ROOT / "openclaw-agent" / "logs"
