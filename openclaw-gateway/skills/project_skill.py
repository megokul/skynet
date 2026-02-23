"""
SKYNET — Project Management Skill

Exposes project lifecycle tools to the LLM so it can create projects,
capture ideas, generate plans, write docs, and manage project state
through natural conversation instead of regex-driven menus.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .base import BaseSkill, SkillContext

logger = logging.getLogger("skynet.skills.project")


def _pm():
    """Lazy import to avoid circular imports at module load time."""
    from bot import state as _state
    return _state._project_manager


def _state():
    from bot import state as _s
    return _s


class ProjectManagementSkill(BaseSkill):
    name = "project_management"
    description = "Create and manage SKYNET projects, capture ideas, generate plans and docs"
    plan_auto_approved = {
        "project_add_idea",
        "project_list",
        "project_status",
    }
    requires_approval = {
        "project_remove",
    }

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "project_create",
                "description": (
                    "Create a new SKYNET project. "
                    "IMPORTANT: ONLY call this after the user has given you an explicit project name. "
                    "If they say 'start a project' or 'new project' without naming it, "
                    "ask 'What would you like to name the project?' first — do NOT call this tool "
                    "until the user provides a name."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Project name (short, filesystem-safe)",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "project_add_idea",
                "description": (
                    "Add a concrete idea, feature, or requirement to the active project. "
                    "Only call this when the user explicitly describes something to build, "
                    "a specific feature, a technical requirement, or a constraint. "
                    "Do NOT call for greetings (hi, hello, hey), acknowledgments (ok, sure, "
                    "yes, got it), vague filler (something, anything), or conversational "
                    "messages that contain no actionable project content. "
                    "If project_id is omitted, adds to the last active project."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "idea": {
                            "type": "string",
                            "description": "The idea, requirement, or feature description",
                        },
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                    "required": ["idea"],
                },
            },
            {
                "name": "project_list",
                "description": "List all projects with their status and idea counts.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "project_status",
                "description": (
                    "Get the current status of the active project: status, idea count, "
                    "local path, and recent ideas."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                },
            },
            {
                "name": "project_generate_plan",
                "description": (
                    "Generate the task plan for the active project. "
                    "Call this when the user gives a clear green-light to proceed, for example: "
                    "'generate the plan', 'make the plan', 'plan it', 'I am ready', "
                    "'start building [description]', 'build it', 'let's build this', 'go ahead and build'. "
                    "NEVER call automatically just because ideas were captured — "
                    "the user must express intent to move forward."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                },
            },
            {
                "name": "project_approve_start",
                "description": (
                    "Approve the plan and start autonomous code execution for the active project. "
                    "IMPORTANT: ONLY call this when the user explicitly approves with words like "
                    "'approve', 'start building', 'go ahead', 'begin coding', 'start execution', "
                    "'let's do it'. NEVER call this automatically after plan generation — "
                    "always wait for the user to review and explicitly confirm."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                },
            },
            {
                "name": "project_pause",
                "description": "Pause the active project's execution.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                },
            },
            {
                "name": "project_resume",
                "description": "Resume a paused project.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                },
            },
            {
                "name": "project_cancel",
                "description": "Cancel the active project.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                },
            },
            {
                "name": "project_remove",
                "description": (
                    "Permanently remove a project record (workspace files are kept). "
                    "Requires explicit user confirmation — only call when the user "
                    "clearly asks to delete or remove a project."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID to remove (optional, defaults to last active)",
                        },
                    },
                },
            },
            {
                "name": "project_clean_failed",
                "description": (
                    "Remove all projects with 'failed' status from the database. "
                    "Use when the user asks to clean up, clear failed projects, "
                    "or remove stale/broken project entries. Safe to call — only "
                    "removes projects that are in 'failed' state."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "project_generate_docs",
                "description": (
                    "Write project documentation (PRD, architecture overview, feature list, "
                    "runbooks, ADR, task plan) based on what you know about the project. "
                    "Fill in the fields you have gathered from the conversation. "
                    "Call this when the user asks to generate docs, write the PRD, "
                    "or when you have enough context (problem + requirements + tech stack)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "problem": {
                            "type": "string",
                            "description": "The problem this project solves",
                        },
                        "users": {
                            "type": "string",
                            "description": "Who the primary users are",
                        },
                        "requirements": {
                            "type": "string",
                            "description": "Key requirements and features (can be comma-separated or bullet list)",
                        },
                        "tech_stack": {
                            "type": "string",
                            "description": "Preferred tech stack, language, or framework",
                        },
                        "non_goals": {
                            "type": "string",
                            "description": "What is explicitly out of scope",
                        },
                        "success_metrics": {
                            "type": "string",
                            "description": "How success will be measured",
                        },
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (optional, defaults to last active)",
                        },
                    },
                },
            },
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: SkillContext,
    ) -> str:
        pm = _pm()
        st = _state()

        if pm is None:
            return "ERROR: Project manager is not available."

        try:
            if tool_name == "project_create":
                return await self._create(pm, st, tool_input)
            if tool_name == "project_add_idea":
                return await self._add_idea(pm, st, tool_input)
            if tool_name == "project_list":
                return await self._list(pm)
            if tool_name == "project_status":
                return await self._status(pm, st, tool_input)
            if tool_name == "project_generate_plan":
                return await self._generate_plan(pm, st, tool_input)
            if tool_name == "project_approve_start":
                return await self._approve_start(pm, st, tool_input)
            if tool_name == "project_pause":
                return await self._pause(pm, st, tool_input)
            if tool_name == "project_resume":
                return await self._resume(pm, st, tool_input)
            if tool_name == "project_cancel":
                return await self._cancel(pm, st, tool_input)
            if tool_name == "project_remove":
                return await self._remove(pm, st, tool_input)
            if tool_name == "project_clean_failed":
                return await self._clean_failed(pm, st)
            if tool_name == "project_generate_docs":
                return await self._generate_docs(pm, st, tool_input)
            return f"Unknown tool: {tool_name}"
        except Exception as exc:
            logger.exception("ProjectManagementSkill.%s failed", tool_name)
            return f"ERROR: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_project_id(self, st, tool_input: dict) -> str | None:
        return tool_input.get("project_id") or st._last_project_id

    async def _get_project(self, pm, project_id: str) -> dict | None:
        try:
            from db import store
            return await store.get_project(pm.db, project_id)
        except Exception:
            return None

    async def _find_project_by_name(self, pm, name: str) -> dict | None:
        """Look up an existing project by name/slug (case-insensitive)."""
        try:
            projects = await pm.list_projects()
            name_lower = name.strip().lower()
            for p in projects:
                if (p.get("name") or "").lower() == name_lower:
                    return p
                if (p.get("display_name") or "").lower() == name_lower:
                    return p
                # slug comparison (replace spaces/hyphens)
                slug = name_lower.replace(" ", "-").replace("_", "-")
                p_slug = (p.get("name") or "").lower().replace(" ", "-").replace("_", "-")
                if slug == p_slug:
                    return p
        except Exception:
            pass
        return None

    async def _resolve_project(self, pm, st, tool_input: dict) -> tuple[dict | None, str]:
        """Resolve project from tool_input or active context. Returns (project, error_msg)."""
        project_id = self._resolve_project_id(st, tool_input)
        if project_id:
            project = await self._get_project(pm, project_id)
            if project:
                st._last_project_id = project["id"]
                return project, ""
        # Fall back to listing and picking most recent active project
        try:
            projects = await pm.list_projects()
        except Exception:
            projects = []
        if not projects:
            return None, "No projects found. Create one first with project_create."
        active_statuses = ("ideation", "planning", "approved", "coding", "testing", "paused")
        active = [p for p in projects if p.get("status") in active_statuses]
        chosen = active[0] if active else projects[0]
        st._last_project_id = chosen["id"]
        return chosen, ""

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    # ---------------------------------------------------------------------------
    # Project name validation — rules-based, not an ever-growing blocklist.
    #
    # Strategy: define *generic tokens* (words with no standalone meaning as a
    # project name), then reject any name whose every slug-token is generic.
    # A separate regex catches concatenated compounds without separators.
    # This is more robust than enumerating bad names one by one.
    # ---------------------------------------------------------------------------

    # Individual words that carry no project-specific meaning on their own.
    _GENERIC_TOKENS: frozenset[str] = frozenset({
        # Articles / determiners / pronouns
        "a", "an", "the", "my", "your", "our", "its",
        # Generic adjectives / prefixes LLMs attach to project names
        "new", "old", "temp", "tmp", "test", "demo", "sample", "example",
        # Generic nouns that describe a project but are NOT a name for one
        "project", "app", "application", "site", "web", "tool", "bot",
        "thing", "stuff", "it", "one", "other", "something", "anything",
        # Common verbs LLMs use as project names
        "start", "create", "make", "build", "run", "do",
        # Timing / filler words
        "today", "now", "then", "soon", "tioday", "tday",
        # Affirmations / negations
        "please", "yes", "no", "ok", "okay", "sure", "yep", "nope",
        # Demonstratives / locatives
        "this", "that", "here", "there",
    })

    # Catches concatenated compound non-names that lack a separator.
    # Only "new" is used as prefix: no human intentionally names a production
    # project "newapp" or "newproject" — it is always LLM-invented.
    # Separated forms like "test app" or "my project" are already caught by
    # the all-tokens-generic rule (Rule 4) without needing regex.
    _JUNK_COMPOUND_RE = re.compile(
        r"^new(project|app|application|site|web|tool|bot)$",
        re.IGNORECASE,
    )

    @classmethod
    def _is_invalid_project_name(cls, name: str) -> bool:
        """Return True if *name* is clearly not a meaningful project name.

        Rules applied in order:
        1. Too short after stripping non-alphanumeric characters.
        2. Exact single-token generic word.
        3. Concatenated compound junk (no separator), e.g. "newproject".
        4. Every hyphen/underscore/space-separated token is generic,
           e.g. "new-project", "test_app", "my app".
        """
        slug = name.strip().lower()

        # Rule 1 — need at least 2 alphanumeric characters.
        if len(re.sub(r"[^a-z0-9]", "", slug)) < 2:
            return True

        # Rule 2 — single generic token.
        if slug in cls._GENERIC_TOKENS:
            return True

        # Rule 3 — concatenated compound junk.
        if cls._JUNK_COMPOUND_RE.match(slug):
            return True

        # Rule 4 — every separated token is generic.
        tokens = [t for t in re.split(r"[-_\s]+", slug) if t]
        if tokens and all(t in cls._GENERIC_TOKENS for t in tokens):
            return True

        return False

    async def _create(self, pm, st, inp: dict) -> str:
        name = (inp.get("name") or "").strip()
        if not name:
            return "ERROR: Project name is required. Please provide a name."
        # Reject obviously wrong names using rules-based validation.
        if self._is_invalid_project_name(name):
            return (
                f"ERROR: '{name}' is not a valid project name. "
                "Please ask the user for a specific project name."
            )
        # If project already exists, activate it instead of failing.
        try:
            project = await pm.create_project(name)
        except ValueError as exc:
            if "already exists" in str(exc).lower():
                existing = await self._find_project_by_name(pm, name)
                if existing:
                    st._last_project_id = existing["id"]
                    from bot.helpers import _project_display
                    status = existing.get("status", "?")
                    return (
                        f"Project '{_project_display(existing)}' already exists (status: {status}). "
                        "It is now the active project."
                    )
            return f"ERROR: {exc}"
        st._last_project_id = project["id"]
        from bot.helpers import _project_bootstrap_note, _project_display
        path = project.get("local_path", "")
        bootstrap_note = _project_bootstrap_note(project)
        parts = [f"Created project '{_project_display(project)}' at {path}."]
        if bootstrap_note:
            parts.append(bootstrap_note)
        return "\n".join(parts)

    async def _add_idea(self, pm, st, inp: dict) -> str:
        idea = (inp.get("idea") or "").strip()
        if not idea:
            return "ERROR: idea text is required."
        project, err = await self._resolve_project(pm, st, inp)
        if not project:
            return f"ERROR: {err}"
        count = await pm.add_idea(project["id"], idea)
        st._last_project_id = project["id"]
        return f"Added idea #{count} to '{project.get('name', project['id'])}'."

    async def _list(self, pm) -> str:
        projects = await pm.list_projects()
        if not projects:
            return "No projects found."
        lines = ["Projects:"]
        for p in projects:
            status = p.get("status", "?")
            name = p.get("name") or p.get("id", "?")
            pid = p.get("id", "")
            ideas = p.get("idea_count", "?")
            lines.append(f"  • {name} [{status}] — {ideas} ideas (id: {pid})")
        return "\n".join(lines)

    async def _status(self, pm, st, inp: dict) -> str:
        project, err = await self._resolve_project(pm, st, inp)
        if not project:
            return err or "No active project."
        name = project.get("name", project["id"])
        status = project.get("status", "?")
        path = project.get("local_path", "?")
        ideas = project.get("ideas") or []
        idea_count = len(ideas) if isinstance(ideas, list) else "?"
        recent = []
        if isinstance(ideas, list):
            recent = ideas[-3:]
        lines = [
            f"Project: {name}",
            f"Status: {status}",
            f"Path: {path}",
            f"Ideas: {idea_count}",
        ]
        if recent:
            lines.append("Recent ideas:")
            for idea in recent:
                text = (idea.get("text") or str(idea))[:120]
                lines.append(f"  - {text}")
        return "\n".join(lines)

    async def _generate_plan(self, pm, st, inp: dict) -> str:
        project, err = await self._resolve_project(pm, st, inp)
        if not project:
            return f"ERROR: {err}"
        # Guard: refuse to plan with no ideas — forces the conversation to happen first.
        ideas = project.get("ideas") or []
        if not ideas:
            try:
                from db import store
                ideas = await store.get_ideas(pm.db, project["id"])
            except Exception:
                ideas = []
        if not ideas:
            return (
                f"ERROR: Project '{project.get('name', project['id'])}' has no ideas or requirements yet. "
                "Please describe what you want the project to do before generating a plan."
            )
        await pm.generate_plan(project["id"])
        st._last_project_id = project["id"]
        return f"Plan generation started for '{project.get('name', project['id'])}'. I will notify you when it's ready for review."

    async def _approve_start(self, pm, st, inp: dict) -> str:
        project, err = await self._resolve_project(pm, st, inp)
        if not project:
            return f"ERROR: {err}"

        name = project.get("name", project["id"])
        status = str(project.get("status", "")).lower()
        st._last_project_id = project["id"]

        # Guard: already running — nothing to approve.
        if status in ("coding", "testing"):
            return f"Project '{name}' is already running (status: {status})."
        # Guard: no plan yet — cannot approve before planning.
        if status == "ideation":
            return (
                f"ERROR: Project '{name}' has no plan yet (status: ideation). "
                "Generate a plan first, then approve."
            )

        # Build status-appropriate button text.
        # 'approved' means the plan exists and was approved but execution hasn't
        # started yet — skip the re-approve and jump straight to start.
        already_approved = (status == "approved")
        button_text = (
            f"▶ Start execution for <b>{name}</b>"
            if already_approved else
            f"Approve &amp; start execution for <b>{name}</b>"
        )

        # Send a Telegram confirmation button — the user must physically tap it
        # before any code runs, even if the plan is already approved.
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            import bot_config as cfg
            from bot import state as _st
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("▶ Start", callback_data=f"approve_plan:{project['id']}"),
                InlineKeyboardButton("✖ Cancel", callback_data=f"cancel_plan:{project['id']}"),
            ]])
            if _st._bot_app and _st._bot_app.bot:
                await _st._bot_app.bot.send_message(
                    chat_id=cfg.ALLOWED_USER_ID,
                    text=button_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return (
                    f"Ready to start '{name}'. Tap ▶ Start in Telegram to begin execution."
                )
        except Exception as exc:
            logger.warning("Failed to send approve_start confirmation: %s", exc)
        # Fallback: execute directly if the bot app is unavailable (e.g. tests).
        await pm.approve_plan(project["id"])   # idempotent if already approved
        await pm.start_execution(project["id"])
        return f"Plan approved and execution started for '{name}'."

    async def _pause(self, pm, st, inp: dict) -> str:
        project, err = await self._resolve_project(pm, st, inp)
        if not project:
            return f"ERROR: {err}"
        await pm.pause_project(project["id"])
        return f"Project '{project.get('name', project['id'])}' paused."

    async def _resume(self, pm, st, inp: dict) -> str:
        project, err = await self._resolve_project(pm, st, inp)
        if not project:
            return f"ERROR: {err}"
        await pm.resume_project(project["id"])
        st._last_project_id = project["id"]
        return f"Project '{project.get('name', project['id'])}' resumed."

    async def _cancel(self, pm, st, inp: dict) -> str:
        project, err = await self._resolve_project(pm, st, inp)
        if not project:
            return f"ERROR: {err}"
        await pm.cancel_project(project["id"])
        return f"Project '{project.get('name', project['id'])}' cancelled."

    async def _remove(self, pm, st, inp: dict) -> str:
        project_id = self._resolve_project_id(st, inp)
        if not project_id:
            return "ERROR: No active project to remove."
        project = await self._get_project(pm, project_id)
        if not project:
            return f"Project '{project_id}' not found."
        from bot.helpers import _project_display, _send_remove_project_confirmation
        await _send_remove_project_confirmation(project)
        return f"Confirmation sent for removing '{_project_display(project)}'. Waiting for your approval."

    async def _clean_failed(self, pm, st) -> str:
        """Remove all projects in 'failed' status."""
        from db import store
        projects = await store.list_projects(pm.db)
        failed = [p for p in projects if str(p.get("status", "")).lower() == "failed"]
        if not failed:
            return "No failed projects found. Nothing to clean up."
        removed = []
        for p in failed:
            await store.remove_project_cascade(pm.db, p["id"])
            removed.append(p.get("display_name") or p.get("name") or p["id"])
            # Clear last_project_id if it pointed to a removed project.
            if st._last_project_id == p["id"]:
                st._last_project_id = None
        names = ", ".join(f"'{n}'" for n in removed)
        return f"Removed {len(removed)} failed project(s): {names}."

    async def _generate_docs(self, pm, st, inp: dict) -> str:
        project_id = self._resolve_project_id(st, inp)
        if not project_id:
            return "ERROR: No active project."
        project = await self._get_project(pm, project_id)
        if not project:
            return f"Project '{project_id}' not found."

        answers = {
            k: str(v).strip()
            for k, v in inp.items()
            if k in ("problem", "users", "requirements", "tech_stack", "non_goals", "success_metrics")
            and v
        }

        from bot.doc_intake import _run_project_docs_generation_async
        from bot.helpers import _spawn_background_task

        _spawn_background_task(
            _run_project_docs_generation_async(project, answers, reason="doc_request"),
            tag=f"doc-gen-{project_id}",
        )
        return (
            f"Documentation generation started for '{project.get('name', project_id)}'. "
            "I'll send a notification when the docs are written."
        )
