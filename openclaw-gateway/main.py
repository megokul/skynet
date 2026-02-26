"""
SKYNET Gateway — Entry Point
Codename: CHATHAN

Starts all components in a single asyncio event loop:
  - WebSocket server (public, port 8765) — CHATHAN worker connection
  - HTTP API (loopback, port 8766) — internal action dispatch
  - Telegram bot (polling) — SKYNET Gateway interface
  - Project orchestrator — SKYNET Core AI-driven lifecycle
  - AI provider router — multi-provider free-tier rotation

Usage:
    python main.py

Environment variables (required):
    SKYNET_AUTH_TOKEN       Shared secret — must match the CHATHAN worker.
    TELEGRAM_BOT_TOKEN     Telegram bot token from BotFather.

Optional:
    DISABLE_TELEGRAM_BOT   Set to "1" or "true" to run in API-only mode (no bot).
    SKYNET_LOG_LEVEL       DEBUG | INFO | WARNING | ERROR (default: INFO)
    SKYNET_TLS_CERT        Path to TLS certificate.
    SKYNET_TLS_KEY         Path to TLS private key.
    GOOGLE_AI_API_KEY      Gemini API key (recommended for free tier).
    GROQ_API_KEY           Groq API key.
    + other AI provider keys (see bot_config.py)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import gateway_config as cfg
import bot_config
from gateway import start_ws_server
from api import start_http_api
from logging_setup import configure_logging
from core.trace import trace_flow


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

    configure_logging(
        level_name=cfg.LOG_LEVEL,
        log_dir=cfg.LOG_DIR,
        mirror_log_dir=cfg.TRACE_MIRROR_LOG_DIR,
        max_bytes=cfg.LOG_MAX_BYTES,
        backup_count=cfg.LOG_BACKUP_COUNT,
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
        enable_s3_logs=cfg.LOG_S3_ENABLED,
        s3_bucket=cfg.LOG_S3_BUCKET,
        s3_prefix=cfg.LOG_S3_PREFIX,
        s3_region=cfg.LOG_S3_REGION,
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
  ____  _  ____   ___   _ _____ _____
 / ___|| |/ /\ \ / / \ | | ____|_   _|
 \___ \| ' /  \ V /|  \| |  _|   | |
  ___) | . \   | | | |\  | |___  | |
 |____/|_|\_\  |_| |_| \_|_____| |_|
      Codename: CHATHAN

  WebSocket : 0.0.0.0:{ws_port}
  HTTP API  : {http_host}:{http_port}
  TLS cert  : {tls}
  Telegram  : enabled
  DB        : {db}
""".format(
            ws_port=cfg.WS_PORT,
            http_host=cfg.HTTP_HOST,
            http_port=cfg.HTTP_PORT,
            tls=cfg.TLS_CERT if cfg.TLS_CERT else "DISABLED",
            db=bot_config.DB_PATH,
        )
    )


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
    trace_flow("main.start")

    logger = logging.getLogger("skynet")

    # ---- Validate required secrets ----
    if not cfg.AUTH_TOKEN:
        logger.error(
            "SKYNET_AUTH_TOKEN is not set.\n"
            "  export SKYNET_AUTH_TOKEN=$(python3 -c "
            "\"import secrets; print(secrets.token_urlsafe(48))\")"
        )
        sys.exit(1)

    if not bot_config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        sys.exit(1)

    # ---- Ensure data directory exists ----
    db_dir = os.path.dirname(bot_config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # ---- Initialize SQLite database ----
    from db.schema import init_db

    db = await init_db(bot_config.DB_PATH)
    logger.info("Database initialized at %s", bot_config.DB_PATH)
    trace_flow("main.database.initialized", db_path=bot_config.DB_PATH)

    # ---- Build AI provider router ----
    from ai.provider_router import ProviderRouter, build_providers, parse_provider_priority

    provider_config = {
        "OLLAMA_DEFAULT_MODEL": bot_config.OLLAMA_DEFAULT_MODEL,
        "GOOGLE_AI_API_KEY": bot_config.GOOGLE_AI_API_KEY,
        "GEMINI_MODEL": bot_config.GEMINI_MODEL,
        "GEMINI_ONLY_MODE": "1" if bot_config.GEMINI_ONLY_MODE else "0",
        "GROQ_API_KEY": bot_config.GROQ_API_KEY,
        "OPENROUTER_API_KEY": bot_config.OPENROUTER_API_KEY,
        "OPENROUTER_MODEL": bot_config.OPENROUTER_MODEL,
        "OPENROUTER_FALLBACK_MODELS": bot_config.OPENROUTER_FALLBACK_MODELS,
        "DEEPSEEK_API_KEY": bot_config.DEEPSEEK_API_KEY,
        "OPENAI_API_KEY": bot_config.OPENAI_API_KEY,
        "ANTHROPIC_API_KEY": bot_config.ANTHROPIC_API_KEY,
    }
    providers = build_providers(provider_config)
    router = ProviderRouter(
        providers,
        db,
        provider_priority=parse_provider_priority(bot_config.AI_PROVIDER_PRIORITY),
    )
    await router.restore_usage()
    logger.info("AI router ready with %d provider(s).", len(providers))
    trace_flow("main.ai_router.ready", provider_count=len(providers))

    # ---- Web search ----
    from search.web_search import WebSearcher

    searcher = WebSearcher(bot_config.BRAVE_SEARCH_API_KEY)

    # ---- SKYNET Policy Engine ----
    from policy.engine import PolicyEngine

    policy_engine = PolicyEngine()
    logger.info("Policy engine online.")
    trace_flow("main.policy.ready")

    # ---- SKYNET Skill Registry ----
    from skills.registry import build_default_registry

    skill_registry = build_default_registry(
        external_skills_dir=bot_config.EXTERNAL_SKILLS_DIR,
        external_skill_urls=bot_config.EXTERNAL_SKILL_URLS,
        always_on_prompt_skills=bot_config.ALWAYS_ON_PROMPT_SKILLS,
        always_on_prompt_snippet_chars=bot_config.ALWAYS_ON_PROMPT_SNIPPET_CHARS,
    )
    logger.info(
        "Skill registry loaded (%d total; %d prompt-only).",
        skill_registry.skill_count,
        skill_registry.prompt_skill_count,
    )
    trace_flow(
        "main.skills.ready",
        skill_count=skill_registry.skill_count,
        prompt_skill_count=skill_registry.prompt_skill_count,
    )

    # ---- SKYNET Memory Manager ----
    from memory.manager import MemoryManager

    s3_storage = None
    try:
        from storage.s3_client import S3Storage
        s3_storage = S3Storage(
            bucket=bot_config.S3_BUCKET,
            prefix=bot_config.S3_PREFIX,
            region=bot_config.AWS_REGION,
        )
    except Exception:
        logger.info("S3 storage not configured — memory sync disabled.")

    memory_manager = MemoryManager(
        db=db,
        gateway_api_url=bot_config.GATEWAY_API_URL,
        s3=s3_storage,
    )

    # ---- CHATHAN Execution Engine ----
    from chathan.execution.engine import ExecutionEngine
    from chathan.providers.chathan_provider import ChathanProvider

    execution_engine = ExecutionEngine(policy_engine=policy_engine)
    execution_engine.register(ChathanProvider(bot_config.GATEWAY_API_URL))
    logger.info(
        "Execution engine ready (providers: %s).",
        ", ".join(execution_engine.available_providers),
    )

    # ---- SKYNET Sentinel ----
    from sentinel.monitor import SentinelMonitor
    from sentinel.alert import AlertDispatcher

    sentinel = SentinelMonitor(
        gateway_api_url=bot_config.GATEWAY_API_URL,
        db=db,
        s3=s3_storage,
    )
    alert_dispatcher = AlertDispatcher()
    logger.info("Sentinel monitor online.")
    trace_flow("main.sentinel.ready")

    # ---- SKYNET Heartbeat Scheduler ----
    from heartbeat.scheduler import HeartbeatScheduler, HeartbeatTask
    from heartbeat.tasks import DEFAULT_TASKS

    heartbeat = HeartbeatScheduler(tick_interval=60)

    # Create a simple context namespace for heartbeat tasks.
    class _HBContext:
        """
        HBContext.
        
        Purpose:
        - Represent a cohesive runtime concept for this subsystem.
        - Group related state and methods behind a single abstraction boundary.
        
        How it works:
        - Holds domain-specific fields and exposes operations that enforce local invariants.
        - Shields calling code from low-level implementation details.
        
        Why this exists:
        - Improves readability by giving the concept an explicit named type.
        - Reduces coupling by centralizing behavior inside `_HBContext`.
        """

        pass
    hb_ctx = _HBContext()
    hb_ctx.sentinel = sentinel
    hb_ctx.alert_dispatcher = alert_dispatcher
    hb_ctx.memory_manager = memory_manager
    hb_ctx.s3 = s3_storage
    hb_ctx.db = db
    hb_ctx.gateway_api_url = bot_config.GATEWAY_API_URL
    hb_ctx.active_project_ids = []

    for task_def in DEFAULT_TASKS:
        heartbeat.register(HeartbeatTask(
            name=task_def["name"],
            description=task_def["description"],
            interval_seconds=task_def["interval_seconds"],
            handler=task_def["handler"],
            context=hb_ctx,
        ))

    # ---- Orchestrator ----
    import telegram_bot
    from orchestrator.scheduler import Scheduler
    from orchestrator.project_manager import ProjectManager

    scheduler = Scheduler(
        db=db,
        router=router,
        searcher=searcher,
        gateway_api_url=bot_config.GATEWAY_API_URL,
        on_progress=telegram_bot.on_project_progress,
        request_approval=telegram_bot.request_worker_approval,
        skill_registry=skill_registry,
        memory_manager=memory_manager,
    )
    sentinel.scheduler = scheduler

    project_manager = ProjectManager(
        db=db,
        router=router,
        searcher=searcher,
        scheduler=scheduler,
        project_base_dir=bot_config.PROJECT_BASE_DIR,
    )

    logger.info("Project orchestrator ready (max %d parallel).", scheduler.max_parallel)
    trace_flow("main.project_orchestrator.ready", max_parallel=scheduler.max_parallel)

    # ---- Inject dependencies into Telegram bot ----
    telegram_bot.set_dependencies(
        project_manager,
        router,
        heartbeat=heartbeat,
        sentinel=sentinel,
        searcher=searcher,
        skill_registry=skill_registry,
    )

    # ---- Start core servers ----
    ws_server = await start_ws_server()
    http_runner = await start_http_api()

    # ---- Start Heartbeat Scheduler ----
    await heartbeat.start()
    logger.info("Heartbeat scheduler started (%d tasks).", heartbeat.task_count)
    trace_flow("main.heartbeat.started", task_count=heartbeat.task_count)

    # ---- Start Telegram bot (non-blocking polling) ----
    bot_app = telegram_bot.build_app()
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started.")
    trace_flow("main.telegram.started")

    logger.info("SKYNET initializing...")
    logger.info("Codename: CHATHAN active.")
    logger.info("Policy engine online.")
    logger.info("Worker connected — waiting for connections...")
    logger.info("System ready.")

    try:
        # Run forever.
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        # Graceful shutdown.
        logger.info("Shutting down…")
        trace_flow("main.shutdown.start")

        # Stop Heartbeat scheduler.
        await heartbeat.stop()

        # Stop Telegram bot.
        try:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
        except Exception:
            logger.exception("Error stopping Telegram bot.")

        # Cancel all running project workers.
        scheduler.cancel_all()

        # Stop WebSocket + HTTP servers.
        ws_server.close()
        await ws_server.wait_closed()
        await http_runner.cleanup()

        # Close database.
        await db.close()

        logger.info("SKYNET shut down.")
        trace_flow("main.shutdown.complete")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
