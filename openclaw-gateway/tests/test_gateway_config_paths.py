from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_gateway_config():
    module_path = REPO_ROOT / "openclaw-gateway" / "config.py"
    spec = importlib.util.spec_from_file_location("gateway_config_paths", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load gateway config")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_runtime_paths_resolve_relative_to_repo_root(monkeypatch) -> None:
    monkeypatch.setenv("SKYNET_DB_PATH", "data/skynet.test.db")
    monkeypatch.setenv("SKYNET_LOG_DIR", "logs/gateway")
    monkeypatch.setenv("SKYNET_TRACE_MIRROR_LOG_DIR", "logs/gateway-mirror")
    monkeypatch.setenv("SKYNET_RUNTIME_TRACE_LIVE_FILE", "logs/gateway.trace.log")

    cfg = _load_gateway_config()

    assert Path(cfg.DB_PATH) == REPO_ROOT / "data" / "skynet.test.db"
    assert Path(cfg.LOG_DIR) == REPO_ROOT / "logs" / "gateway"
    assert Path(cfg.TRACE_MIRROR_LOG_DIR) == REPO_ROOT / "logs" / "gateway-mirror"
    assert Path(cfg.RUNTIME_TRACE_LIVE_FILE) == REPO_ROOT / "logs" / "gateway.trace.log"
