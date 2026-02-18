"""
SKYNET control-plane entrypoint.

This module intentionally avoids runtime execution logic.
Run the FastAPI control plane directly.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Start SKYNET control-plane API."""
    uvicorn.run(
        "skynet.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
