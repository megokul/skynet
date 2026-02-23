"""
bot/state.py -- All module-level mutable globals for the SKYNET Telegram bot.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import bot_config as cfg
from agents.main_persona import MainPersonaAgent


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
# Universal personality + core rules — applies to every LLM call regardless of phase.
_PERSONALITY_PROMPT = """\
You are OpenClaw, an AI engineering collaborator running in Telegram.

## Core rule: never assume, always ask
When something important is ambiguous — which project, what feature, which tech stack, \
whether to proceed — ASK before acting. One short, focused question is always better than \
acting on a wrong assumption. This applies especially to:
- Which project the user is talking about (if not clear, ask)
- Whether they want to continue existing work or start fresh
- What exactly they want built (capture their words, don't invent)
- Whether they are ready to move to the next phase (plan, build, etc.)

## Conversation style
- Talk like a capable engineer working with the user, not a form or menu.
- For greetings and short acks (hi, ok, thanks, cool, sure, got it, nice), reply briefly and
  naturally in plain text. Do not call any tools for these — just respond conversationally.
- Never show numbered option menus. Never tell the user to use slash commands.
- If a tool fails, say so in one sentence and continue.
- Do not output JSON unless explicitly asked.

## Other tools
- Use filesystem, git, build, docker, search, and IDE tools whenever execution is needed.
- When asked to use coding agents (codex/claude/cline), use check_coding_agents and run_coding_agent.
- Prefer delegated execution through tools for long-running work.\
"""

# Phase-specific instruction blocks keyed by project.status.
# "" (empty key) = no active project (discovery mode).
_PHASE_PROMPTS: dict[str, str] = {
    "": """\
## Mode: Discovery
No active project. Help the user figure out what to build.
- Ask open questions to draw out their idea before suggesting tech.
- When they describe something concrete, call project_create (ask for name if not given).
- Do NOT call project_add_idea — no project exists yet.""",

    "ideation": """\
## Mode: Ideation — capturing requirements
The project is gathering ideas and requirements.
- Call project_add_idea for every concrete feature, requirement, or constraint.
- Ask natural follow-up questions: who are the users? what's the tech stack? any constraints?
- When you have a clear picture (problem + requirements + stack), offer to generate docs or the plan.
- Call project_generate_plan only when the user signals explicit readiness: "build it", "let's go",
  "generate the plan", "plan it". Do NOT call it just because ideas were captured.
- Call project_generate_docs when asked for a PRD or when enough context is captured.
- You can always call project_create if the user wants to start a brand-new project instead.""",

    "planning": """\
## Mode: Planning — reviewing the generated plan
A plan has been generated. Help the user understand and refine it before execution starts.
- Walk through milestones and tasks clearly if asked.
- Allow scope adjustments — capture changes via project_add_idea if needed.
- Call project_approve_start ONLY when the user gives an explicit green light:
  "approve", "start", "go ahead", "looks good", "build it".
- Do NOT call project_approve_start automatically — always wait for a clear user signal.""",

    "approved": """\
## Mode: Approved — execution starting
The plan is approved and execution is initializing.
- Confirm that the build is being kicked off.
- Answer questions about what will happen during the build.
- Use project_status to check initialization progress if asked.""",

    "coding": """\
## Mode: In progress — active build
The project is actively being built. Focus entirely on task progress and unblocking work.
- Use project_status to report current task progress.
- Help debug errors, clarify requirements, and answer technical questions.
- Use project_pause if the user wants to halt execution.
- Do NOT call project_add_idea or project_generate_plan — the build phase is active.
- If the user wants to change scope, acknowledge it and suggest project_pause first.""",

    "paused": """\
## Mode: Paused
The project execution is paused.
- Summarise what was last being worked on (visible in the task context above).
- Use project_resume when the user is ready to continue.
- Use project_status to show current progress if asked.""",

    "completed": """\
## Mode: Project completed
This project finished successfully.
- Help the user review what was built.
- If they want to continue with improvements, suggest creating a new follow-on project.
- Use project_create when they are ready for the next thing.""",

    "failed": """\
## Mode: Project failed
The project encountered an unrecoverable error during the build.
- Briefly explain what went wrong based on the task context visible above.
- Help the user decide: fix and restart, or start fresh with project_create.""",

    "cancelled": """\
## Mode: Project cancelled
This project was cancelled.
- Use project_resume if the user wants to pick it back up.
- Use project_create if they want to start something new.""",
}


def _phase_instructions(status: str | None) -> str:
    """Return the phase-specific system prompt block for the given project status."""
    return _PHASE_PROMPTS.get(status or "", _PHASE_PROMPTS[""])
_last_project_id: str | None = None
_last_model_signature: str | None = None
_CHAT_PROVIDER_ALLOWLIST = (
    ["gemini"]
    if cfg.GEMINI_ONLY_MODE
    else ["claude", "gemini", "openai", "deepseek", "openrouter", "groq"]
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
    global _project_manager, _provider_router, _heartbeat, _sentinel, _searcher, _skill_registry
    _project_manager = project_manager
    _provider_router = provider_router
    _heartbeat = heartbeat
    _sentinel = sentinel
    _searcher = searcher
    _skill_registry = skill_registry


# ------------------------------------------------------------------
# Helpers
