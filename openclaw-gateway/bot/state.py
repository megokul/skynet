"""
bot/state.py -- All module-level mutable globals for the SKYNET Telegram bot.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import bot_config as cfg
from agents.main_persona import MainPersonaAgent
from core.prompt_library import load_prompt

if TYPE_CHECKING:
    from .orchestrator import Orchestrator
    from core.engine import ConversationEngine


class _TTLDict(dict):
    """dict that evicts entries older than ttl_seconds on every write.

    asyncio.Future values are cancelled before eviction so callers that are
    waiting on them get an immediate CancelledError rather than hanging forever.
    """

    def __init__(self, ttl_seconds: int) -> None:
        super().__init__()
        self._ttl = ttl_seconds
        self._timestamps: dict = {}

    def __setitem__(self, key, value):
        self._evict()
        self._timestamps[key] = time.monotonic()
        super().__setitem__(key, value)

    def __delitem__(self, key):
        self._timestamps.pop(key, None)
        super().__delitem__(key)

    def pop(self, key, *args):
        self._timestamps.pop(key, None)
        return super().pop(key, *args)

    def _evict(self):
        now = time.monotonic()
        stale = [k for k, ts in self._timestamps.items() if now - ts >= self._ttl]
        for k in stale:
            value = self.get(k)
            if isinstance(value, asyncio.Future) and not value.done():
                value.cancel()
            super().pop(k, None)
            self._timestamps.pop(k, None)

# ---------------------------------------------------------------------------
# Injected at startup by main.py.
# ---------------------------------------------------------------------------
_project_manager = None
_provider_router = None
_heartbeat = None
_sentinel = None
_searcher = None
_skill_registry = None
_orchestrator: "Orchestrator | None" = None
_conversation_engine: "ConversationEngine | None" = None

# Stores pending CONFIRM actions keyed by a short ID.
_pending_confirms: _TTLDict = _TTLDict(ttl_seconds=1800)
_confirm_counter: int = 0

# Stores pending approval futures from the orchestrator worker.
# { "key": asyncio.Future }
_pending_approvals: _TTLDict = _TTLDict(ttl_seconds=600)
_approval_counter: int = 0
# Stores pending destructive remove-project confirmations.
_pending_project_removals: _TTLDict = _TTLDict(ttl_seconds=300)
_background_tasks: set[asyncio.Task] = set()

_DOC_LLM_TARGET_PATHS: tuple[str, ...] = (
    "docs/product/PRD.md",
    "docs/product/overview.md",
    "docs/product/features.md",
    "docs/architecture/overview.md",
    "docs/architecture/system-design.md",
    "docs/architecture/data-flow.md",
    "docs/runbooks/local-dev.md",
    "docs/runbooks/deploy.md",
    "docs/runbooks/recovery.md",
    "docs/guides/getting-started.md",
    "docs/guides/configuration.md",
    "docs/decisions/ADR-001-tech-stack.md",
    "planning/task_plan.md",
    "planning/progress.md",
    "planning/findings.md",
)

_FINALIZED_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "templates"
    / "skynet-project-documentation"
    / "templates"
)

# Reference to the Telegram app for sending proactive messages.
_bot_app = None  # Application | None -- assigned in build_app()

# Short rolling chat history for natural Telegram conversation.
_chat_history: list[dict] = []
_CHAT_HISTORY_MAX: int = 12
# Universal personality + core rules -- applies to every LLM call regardless of phase.
_PERSONALITY_PROMPT = load_prompt("bot/personality_system.md")

_last_model_signature: str | None = None
_CHAT_PROVIDER_ALLOWLIST = (
    ["gemini"]
    if cfg.GEMINI_ONLY_MODE
    else ["claude", "gemini", "openai", "deepseek", "openrouter", "groq", "ollama"]
)
_main_persona_agent = MainPersonaAgent()
_NO_STORE_ONCE_MARKERS = {
    "don't store this",
    "do not store this",
    "dont store this",
}
_NO_STORE_CHAT_MARKERS = {
    "don't store anything from this chat",
    "do not store anything from this chat",
    "dont store anything from this chat",
}


def set_dependencies(
    project_manager,
    provider_router,
    heartbeat=None,
    sentinel=None,
    searcher=None,
    skill_registry=None,
):
    """Called by main.py to inject dependencies."""
    global _project_manager, _provider_router, _heartbeat, _sentinel, _searcher, _skill_registry, _orchestrator, _conversation_engine
    _project_manager = project_manager
    _provider_router = provider_router
    _heartbeat = heartbeat
    _sentinel = sentinel
    _searcher = searcher
    _skill_registry = skill_registry

    if all((_project_manager, _provider_router)):
        from core.engine import ConversationEngine

        _conversation_engine = ConversationEngine(
            db=_project_manager.db,
            provider_router=_provider_router,
            project_manager=_project_manager,
        )
    else:
        _conversation_engine = None

    # Legacy orchestrator is kept nullable for backward compatibility.
    _orchestrator = None


# ------------------------------------------------------------------
# Helpers
