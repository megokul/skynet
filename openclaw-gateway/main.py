"""
SKYNET Gateway — Entry Point

Starts exactly four things in one asyncio event loop:
  1. SQLite database
  2. AI provider router       (ready for Chat; currently wired but not used by bot)
  3. WebSocket server (8765)  — worker laptop (CLAW) connects here
  4. HTTP API (127.0.0.1:8766) — internal action dispatch to worker
  5. Telegram bot (long polling)

Environment variables (required):
    TELEGRAM_BOT_TOKEN      Telegram bot token from BotFather.
    SKYNET_AUTH_TOKEN       Shared secret — must match the CLAW worker.

Optional (recommended):
    TELEGRAM_ALLOWED_USER_ID   Restrict bot to one Telegram user ID.
    GOOGLE_AI_API_KEY          Gemini key (free tier, recommended).
    GROQ_API_KEY               Groq key.
    + other AI provider keys (see config.py)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import config as cfg
from logging_setup import configure_logging
from db.schema import init_db


def _setup_logging() -> None:
    configure_logging(
        level_name=cfg.LOG_LEVEL,
        log_dir=cfg.LOG_DIR,
        enable_local_file_targets=cfg.LOG_ENABLE_LOCAL_FILES,
        enable_ssh_mirror=cfg.LOG_ENABLE_SSH_MIRROR,
        ssh_host=cfg.LOG_SSH_HOST,
        ssh_port=cfg.LOG_SSH_PORT,
        ssh_user=cfg.LOG_SSH_USER,
        ssh_key_path=cfg.LOG_SSH_KEY_PATH,
        ssh_password=cfg.LOG_SSH_PASSWORD,
        ssh_strict_host_key=cfg.LOG_SSH_STRICT_HOST_KEY,
        ssh_connect_timeout=cfg.LOG_SSH_CONNECT_TIMEOUT,
        ssh_command_timeout=cfg.LOG_SSH_COMMAND_TIMEOUT,
        mirror_log_dir=cfg.TRACE_MIRROR_LOG_DIR,
        max_bytes=cfg.LOG_MAX_BYTES,
        backup_count=cfg.LOG_BACKUP_COUNT,
        enable_s3_logs=False,
        s3_bucket="",
        s3_prefix="",
        s3_region="",
    )


def _print_banner() -> None:
    print(
        """
  ____  _  ____   ___   _ _____ _____
 / ___|| |/ /\\ \\ / / \\ | | ____|_   _|
 \\___ \\| ' /  \\ V /|  \\| |  _|   | |
  ___) | . \\   | | | |\\  | |___  | |
 |____/|_|\\_\\  |_| |_| \\_|_____| |_|

  WebSocket : 0.0.0.0:{ws_port}
  HTTP API  : {http_host}:{http_port}
  TLS       : {tls}
  DB        : {db}
""".format(
            ws_port=cfg.WS_PORT,
            http_host=cfg.HTTP_HOST,
            http_port=cfg.HTTP_PORT,
            tls=cfg.TLS_CERT if cfg.TLS_CERT else "disabled",
            db=cfg.DB_PATH,
        )
    )


def _build_ai_router(db):
    from ai.provider_router import ProviderRouter, build_providers, parse_provider_priority
    provider_cfg = {
        "OLLAMA_DEFAULT_MODEL":       cfg.OLLAMA_DEFAULT_MODEL,
        "GOOGLE_AI_API_KEY":          cfg.GOOGLE_AI_API_KEY,
        "GEMINI_MODEL":               cfg.GEMINI_MODEL,
        "GEMINI_ONLY_MODE":           "1" if cfg.GEMINI_ONLY_MODE else "0",
        "GROQ_API_KEY":               cfg.GROQ_API_KEY,
        "OPENROUTER_API_KEY":         cfg.OPENROUTER_API_KEY,
        "OPENROUTER_MODEL":           cfg.OPENROUTER_MODEL,
        "OPENROUTER_FALLBACK_MODELS": cfg.OPENROUTER_FALLBACK_MODELS,
        "DEEPSEEK_API_KEY":           cfg.DEEPSEEK_API_KEY,
        "OPENAI_API_KEY":             cfg.OPENAI_API_KEY,
        "ANTHROPIC_API_KEY":          cfg.ANTHROPIC_API_KEY,
    }
    providers = build_providers(provider_cfg)
    return ProviderRouter(
        providers,
        db,
        provider_priority=parse_provider_priority(cfg.AI_PROVIDER_PRIORITY),
    )


async def _main() -> None:
    _setup_logging()
    _print_banner()

    logger = logging.getLogger("skynet")

    # ── Validate required secrets ─────────────────────────────────────────────
    if not cfg.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Aborting.")
        sys.exit(1)

    if not cfg.AUTH_TOKEN:
        logger.error(
            "SKYNET_AUTH_TOKEN is not set.\n"
            "  Generate one with: python3 -c "
            "\"import secrets; print(secrets.token_urlsafe(48))\""
        )
        sys.exit(1)

    # ── Database ──────────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(os.path.abspath(cfg.DB_PATH)), exist_ok=True)
    db = await init_db(cfg.DB_PATH)
    logger.info("Database ready at %s", cfg.DB_PATH)

    # ── AI router ─────────────────────────────────────────────────────────────
    router = _build_ai_router(db)
    logger.info("AI router ready (%d provider(s)).", len(router.providers))

    # ── WebSocket server (worker laptop connects here) ─────────────────────────
    from gateway import start_ws_server
    ws_server = await start_ws_server()
    logger.info("WebSocket server listening on port %d.", cfg.WS_PORT)

    # ── HTTP API (loopback, internal dispatch) ─────────────────────────────────
    from api import start_http_api
    http_runner = await start_http_api()
    logger.info("HTTP API listening on %s:%d.", cfg.HTTP_HOST, cfg.HTTP_PORT)

    # ── Telegram bot ──────────────────────────────────────────────────────────
    from bot import build_app
    app = build_app(db, router)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling. SKYNET is online.")

    try:
        await asyncio.Future()   # run until Ctrl-C
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down…")

        # Bot
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            logger.exception("Error stopping Telegram bot.")

        # WebSocket server
        ws_server.close()
        await ws_server.wait_closed()

        # HTTP API
        await http_runner.cleanup()

        # Database
        await db.close()

        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
