"""
SKYNET control-plane entrypoint.

This module intentionally avoids runtime execution logic.
Run the FastAPI control plane directly.
"""

from __future__ import annotations

import uvicorn

from skynet.settings.loader import get_str, get_int, get_bool


def main() -> None:
    """Start SKYNET control-plane API."""
    uvicorn.run(
        "skynet.api.main:app",
        host=get_str("SKYNET_HTTP_HOST", "0.0.0.0"),
        port=get_int("SKYNET_HTTP_PORT", 8000),
        reload=get_bool("SKYNET_RELOAD", False),
        log_level=get_str("SKYNET_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
