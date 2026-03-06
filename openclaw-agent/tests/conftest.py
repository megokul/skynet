"""
Bootstrap sys.path so all agent imports resolve without installing the package.
"""
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Gateway and worker test suites both expose a bare ``config`` module.
# Drop any previously imported non-agent variant before agent modules load.
_config_path = os.path.join(ROOT, "config.py")
_config_spec = importlib.util.spec_from_file_location("config", _config_path)
if _config_spec is None or _config_spec.loader is None:
    raise RuntimeError(f"Unable to load agent config from {_config_path}")
_config_module = importlib.util.module_from_spec(_config_spec)
_config_spec.loader.exec_module(_config_module)
sys.modules["config"] = _config_module

# Provide env vars expected by config.py before any import.
os.environ.setdefault("SKYNET_AUTH_TOKEN", "test-token")
os.environ.setdefault("SKYNET_GATEWAY_URL", "wss://localhost:8765/agent/ws")

# Allow the system temp dir through the security path-jail so tests can use
# pytest's tmp_path fixture without triggering SecurityViolation.
# This must be set before security/validator.py is imported (lazy, at test time).
os.environ.setdefault("SKYNET_ALLOWED_ROOTS", tempfile.gettempdir())
