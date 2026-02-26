"""
CHATHAN Worker — Entry Point

SKYNET execution node. Connects to SKYNET Gateway via WebSocket
and executes actions in a secure sandbox.

Usage:
    python main.py

Environment variables (required):
    SKYNET_AUTH_TOKEN       Pre-shared bearer token.
    SKYNET_GATEWAY_URL     WebSocket URL of the SKYNET Gateway.

Optional:
    SKYNET_LOG_LEVEL       DEBUG | INFO | WARNING | ERROR (default: INFO)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

# Ensure project root is on sys.path so bare imports like ``import config``
# resolve correctly regardless of the caller's working directory.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402 — must come after path fixup
from connection.websocket_client import run_agent  # noqa: E402


def _configure_logging() -> None:
    """
    Configure logging.
    
    Purpose:
    - Implement `_configure_logging` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _print_banner() -> None:
    """
    Print banner.
    
    Purpose:
    - Implement `_print_banner` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    print(
        r"""
   ____ _   _    _  _____ _   _    _    _   _
  / ___| | | |  / \|_   _| | | |  / \  | \ | |
 | |   | |_| | / _ \ | | | |_| | / _ \ |  \| |
 | |___|  _  |/ ___ \| | |  _  |/ ___ \| |\  |
  \____|_| |_/_/   \_|_| |_| |_/_/   \_|_| \_|
       SKYNET Execution Worker

  Gateway : {gateway}
  Roots   : {roots}
  Rate    : {rate}/min
  Log dir : {logdir}
""".format(
            gateway=config.GATEWAY_URL,
            roots=", ".join(config.ALLOWED_ROOTS),
            rate=config.RATE_LIMIT_PER_MINUTE,
            logdir=config.AUDIT_LOG_DIR,
        )
    )


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Graceful shutdown on Ctrl+C / SIGTERM."""
    def _shutdown(sig: signal.Signals) -> None:
        """
        Shutdown.
        
        Purpose:
        - Implement `_shutdown` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `sig`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        logging.getLogger("chathan").info("Received %s — shutting down.", sig.name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # Windows does not support add_signal_handler on the default event loop,
    # so fall back to signal.signal for SIGINT (Ctrl+C).
    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda s, f: _shutdown(signal.Signals(s)))
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _shutdown, sig)


async def _main() -> None:
    """
    Main.
    
    Purpose:
    - Implement `_main` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    _configure_logging()
    _print_banner()

    logger = logging.getLogger("chathan")

    # Pre-flight checks.
    if not config.AUTH_TOKEN:
        logger.error(
            "SKYNET_AUTH_TOKEN is not set. "
            "Export it before starting:\n"
            "  set SKYNET_AUTH_TOKEN=<your-token>"
        )
        sys.exit(1)

    logger.info("Agent starting.  Press Ctrl+C to stop.")

    try:
        await run_agent()
    except asyncio.CancelledError:
        logger.info("Agent stopped.")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    _install_signal_handlers(loop)
    try:
        loop.run_until_complete(_main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
