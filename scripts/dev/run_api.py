"""
SKYNET FastAPI service startup script.

Bootstraps the shared control-plane settings loader before starting uvicorn.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skynet.settings.loader import bootstrap_component_env, get_settings  # noqa: E402


loader = bootstrap_component_env("control")
gateway_urls = loader.get_str("OPENCLAW_GATEWAY_URLS", "").strip()
gateway_url = loader.get_str("OPENCLAW_GATEWAY_URL", "").strip()
if not gateway_urls and not gateway_url:
    print("WARNING: OPENCLAW_GATEWAY_URL/OPENCLAW_GATEWAY_URLS not set in settings or env")


if __name__ == "__main__":
    import uvicorn

    print("\nStarting SKYNET FastAPI service...")
    uvicorn.run(
        "skynet.api.main:app",
        host=get_settings().get_str("SKYNET_HTTP_HOST", "0.0.0.0"),
        port=get_settings().get_int("SKYNET_HTTP_PORT", 8000),
        reload=get_settings().get_bool("SKYNET_RELOAD", False),
        log_level=get_settings().get_str("SKYNET_LOG_LEVEL", "info").lower(),
    )
