"""
Project specialist role for project bootstrap and idea capture.

This role handles typed pending-question state transitions and project-scoped
writes (project creation, requirement capture, idea append).
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time

from core.dev_trace import (
    DevTracePhase,
    trace_control_flow,
    trace_data_flow,
    trace_decision,
    trace_output,
    trace_role_enter,
    trace_state_mutation,
)
from core.roles.base import Role, RoleContext, RoleOutput
from db import store

_INVALID_NAMES = {
    # Reject vague names that usually indicate the user has not provided a
    # concrete project identifier yet.
    "today",
    "tomorrow",
    "now",
    "new",
    "project",
    "app",
    "application",
    "one",
    "it",
}

_NEW_PROJECT_RE = re.compile(
    r"\b(?:start|create|make|begin|initiate)\b.{0,35}\bproject\b"
    r"|\bnew\s+(?:project|app|application)\b"
    r"|\bproject\b.{0,20}\b(?:start|create|make|begin)\b",
    re.IGNORECASE,
)

_PROJECT_NAME_PATTERNS = (
    re.compile(r"\b(?:called|named)\s+([a-zA-Z0-9][a-zA-Z0-9_\- ]{1,63})", re.IGNORECASE),
    re.compile(
        r"\b(?:start|create|make|begin|initiate)\s+(?:a\s+)?(?:new\s+)?(?:project|app|application)\s+([a-zA-Z0-9][a-zA-Z0-9_\- ]{1,63})",
        re.IGNORECASE,
    ),
)


def _slugify_name(name: str) -> str:
    """
    Normalize user-provided project names to stable slug form.

    This slug is used for uniqueness lookups while preserving original display
    name for user-facing responses.
    """
    raw = (name or "").strip().lower()
    chars: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_"}:
            chars.append(ch)
        elif ch.isspace():
            chars.append("-")
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:64]


class ProjectSpecialistRole(Role):
    """
    ProjectSpecialistRole.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `ProjectSpecialistRole`.
    """

    name = "project_specialist"

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Main project-specialist state machine.

        Branch order matters:
        1. Pending question handlers (typed state continuation).
        2. Active project path (append idea to current project).
        3. New-project bootstrap question.
        """
        conversation = context.conversation
        pending = conversation.pending_question or {}
        pending_type = str(pending.get("type") or "")
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        trace_role_enter(DevTracePhase.SPECIALIST, self.name)
        trace_output(DevTracePhase.SPECIALIST, key="pending_type", value=pending_type)

        if pending_type == "need_project_name":
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "pending question continuation",
                    "selected_action": "capture_project_name",
                    "reasoning": "conversation is waiting for project name",
                },
            )
            return await self._handle_project_name(context, user_text)

        if pending_type == "need_project_requirements":
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "pending question continuation",
                    "selected_action": "capture_project_requirements",
                    "reasoning": "conversation is waiting for requirements",
                },
            )
            return await self._handle_requirements(context, user_text)

        # Explicit "start/create new project" requests should preempt active
        # project append behavior so users can switch scope naturally.
        if _is_new_project_intent(user_text):
            hinted_name = _extract_project_name_hint(user_text)
            if hinted_name:
                trace_decision(
                    DevTracePhase.SPECIALIST,
                    {
                        "routing_rule": "new project intent with inline name",
                        "selected_action": "capture_project_name",
                        "hinted_name": hinted_name,
                    },
                )
                return await self._handle_project_name(context, hinted_name)
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "new project intent without name",
                    "selected_action": "ask_for_project_name",
                },
            )
            await context.conversation_manager.set_pending_question(
                conversation.id,
                {
                    "type": "need_project_name",
                    "choices": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {},
                },
            )
            return RoleOutput(
                command="continue",
                response="What should I name the new project?",
            )

        if conversation.active_project_id:
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "active project scope",
                    "selected_action": "append_idea",
                    "active_project_id": conversation.active_project_id,
                },
            )
            return await self._append_idea_to_active(context, user_text)

        await context.conversation_manager.set_pending_question(
            conversation.id,
            {
                "type": "need_project_name",
                "choices": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {},
            },
        )
        return RoleOutput(
            command="continue",
            response="What should I name the new project?",
        )

    async def _handle_project_name(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Capture/validate project name and prepare requirements collection.

        If project already exists by normalized slug, it is reused; otherwise
        one is created.
        """
        conversation = context.conversation
        proposed = (user_text or "").strip().strip(".!,?;:")
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        if not proposed:
            return RoleOutput(command="continue", response="Please share a project name.")

        normalized = _slugify_name(proposed)
        trace_data_flow(
            DevTracePhase.SPECIALIST,
            source_name="project_name.proposed",
            source_value=proposed,
            target_name="project_name.normalized",
            target_value=normalized,
        )
        if not normalized or normalized in _INVALID_NAMES:
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "project name validation",
                    "selected_action": "ask_for_specific_name",
                    "reasoning": "name was ambiguous or invalid",
                },
            )
            return RoleOutput(
                command="continue",
                response="That name is too ambiguous. Please provide a specific project name.",
            )

        project = await store.get_project_by_name(context.db, normalized)
        if project is None:
            if context.project_manager is not None and hasattr(context.project_manager, "create_project"):
                trace_decision(
                    DevTracePhase.SPECIALIST,
                    {
                        "routing_rule": "create project via project_manager",
                        "project_name": proposed,
                    },
                )
                project = await context.project_manager.create_project(proposed)
            else:
                trace_decision(
                    DevTracePhase.SPECIALIST,
                    {
                        "routing_rule": "create project via store fallback",
                        "project_name": proposed,
                        "normalized_name": normalized,
                    },
                )
                project = await store.create_project(
                    context.db,
                    name=normalized,
                    display_name=proposed,
                    local_path="",
                )
        else:
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "reuse existing project",
                    "project_id": project["id"],
                    "reasoning": "matching normalized project already exists",
                },
            )

        await context.conversation_manager.set_active_project(conversation.id, project["id"])
        await context.conversation_manager.set_pending_question(
            conversation.id,
            {
                "type": "need_project_requirements",
                "choices": [],
                "metadata": {"project_id": project["id"]},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        trace_output(DevTracePhase.SPECIALIST, key="project_id", value=project["id"])

        return RoleOutput(
            command="continue",
            response=(
                f"Project '{project.get('display_name') or project.get('name')}' is ready. "
                "What should this project do?"
            ),
            result={"project_id": project["id"]},
        )

    async def _handle_requirements(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Persist initial project requirements as both idea and description.

        Completing this step returns control to `igris`.
        """
        conversation = context.conversation
        project_id = conversation.active_project_id
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        if not project_id:
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "requirements without active project",
                    "selected_action": "ask_for_project_name",
                },
            )
            await context.conversation_manager.set_pending_question(
                conversation.id,
                {
                    "type": "need_project_name",
                    "choices": [],
                    "metadata": {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return RoleOutput(command="continue", response="I need a project name before requirements.")

        requirements = (user_text or "").strip()
        if not requirements:
            return RoleOutput(command="continue", response="Please describe the project requirements.")

        trace_decision(
            DevTracePhase.SPECIALIST,
            {
                "routing_rule": "capture requirements",
                "project_id": project_id,
                "selected_action": "persist requirements and complete",
            },
        )
        await store.add_idea(context.db, project_id, requirements)
        await store.update_project(context.db, project_id, description=requirements)
        await context.conversation_manager.clear_pending_question(conversation.id)
        trace_output(DevTracePhase.SPECIALIST, key="requirements", value=requirements)

        return RoleOutput(
            command="complete",
            response="Requirements captured. Igris is back in command.",
            result={"project_id": project_id, "requirements": requirements},
        )

    async def _append_idea_to_active(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Deterministically append user message as an idea on active project.

        This is used when a conversation already has project scope and no typed
        pending-question branch is active.
        """
        started = time.perf_counter()
        project_id = context.conversation.active_project_id
        idea_text = (user_text or "").strip()
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        if not idea_text:
            return RoleOutput(command="continue", response="What idea should I add to the active project?")

        before_count = 0
        try:
            before_count = len(await store.get_ideas(context.db, project_id))
        except Exception:
            before_count = 0
        await store.add_idea(context.db, project_id, idea_text)
        after_count = before_count + 1
        try:
            after_count = len(await store.get_ideas(context.db, project_id))
        except Exception:
            pass

        trace_state_mutation(
            DevTracePhase.SPECIALIST,
            key="project.idea_count",
            old_value=before_count,
            new_value=after_count,
        )
        trace_output(DevTracePhase.SPECIALIST, key="idea_text", value=idea_text)
        trace_output(
            DevTracePhase.SPECIALIST,
            key="append_execution_ms",
            value=int(round((time.perf_counter() - started) * 1000.0)),
        )
        project = await store.get_project(context.db, project_id)
        name = (project or {}).get("display_name") or (project or {}).get("name") or "the active project"
        return RoleOutput(
            command="complete",
            response=f"Idea added to {name}. Igris is back in command.",
            result={"project_id": project_id, "idea_text": idea_text},
        )


def _is_new_project_intent(text: str) -> bool:
    """Return True when text explicitly asks to start a new project."""
    value = (text or "").strip()
    if not value:
        return False
    return bool(_NEW_PROJECT_RE.search(value))


def _extract_project_name_hint(text: str) -> str:
    """
    Extract inline project name hints from creation phrases.

    Examples:
    - "create project beepapp"
    - "start a project called my app"
    """
    value = (text or "").strip()
    if not value:
        return ""
    for pattern in _PROJECT_NAME_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        name = match.group(1).strip().strip(".!,?;:")
        if not name:
            continue
        if name.lower() in _INVALID_NAMES:
            continue
        return name
    return ""
