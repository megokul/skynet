"""
Bootstrap sys.path so all agent imports resolve without installing the package.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Provide env vars expected by config.py before any import.
os.environ.setdefault("SKYNET_AUTH_TOKEN", "test-token")
os.environ.setdefault("SKYNET_GATEWAY_URL", "wss://localhost:8765/agent/ws")

# Allow the system temp dir through the security path-jail so tests can use
# pytest's tmp_path fixture without triggering SecurityViolation.
# This must be set before security/validator.py is imported (lazy, at test time).
os.environ.setdefault("SKYNET_ALLOWED_ROOTS", tempfile.gettempdir())
