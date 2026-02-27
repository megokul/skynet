"""
Coding specialist role.

The role validates active project scope, queues a coding job through the
scheduler, and immediately returns control to commander.
"""

from __future__ import annotations

import logging

from core.dev_trace import (
    DevTracePhase,
    trace_control_flow,
    trace_decision,
    trace_output,
    trace_role_enter,
)
from core.roles.base import Role, RoleContext, RoleOutput
from db import store

logger = logging.getLogger("skynet.core.roles.coding")


class CodingSpecialistRole(Role):
    """
    CodingSpecialistRole.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `CodingSpecialistRole`.
    """

    name = "coding_specialist"

    async def handle_message(self, context: RoleContext, user_text: str) -> RoleOutput:
        """
        Queue coding execution for the active project.

        This role does not run code directly in-process. It delegates actual
        execution work to the scheduler and returns a queued-job acknowledgement.
        """
        project_id = context.conversation.active_project_id
        trace_control_flow(DevTracePhase.SPECIALIST, stack_depth=2)
        trace_role_enter(DevTracePhase.SPECIALIST, self.name)
        trace_output(DevTracePhase.SPECIALIST, key="active_project_id", value=project_id or "")
        if not project_id:
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "require active project",
                    "selected_action": "continue",
                    "reasoning": "no active project scope available",
                },
            )
            return RoleOutput(
                command="continue",
                response="No active project in this conversation. Ask Igris to create or select a project first.",
            )

        project = await store.get_project(context.db, project_id)
        if not project:
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "project existence check",
                    "selected_action": "complete",
                    "reasoning": "active project row missing",
                },
            )
            return RoleOutput(command="complete", response="The active project no longer exists.")

        instructions = (user_text or "").strip()
        job_id = None
        if context.scheduler is not None and hasattr(context.scheduler, "enqueue_coding_job"):
            # Scheduler availability is environment-dependent (tests vs runtime).
            job_id = await context.scheduler.enqueue_coding_job(
                project_id=project_id,
                requested_by="coding_specialist",
                instructions=instructions,
            )
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "queue background coding job",
                    "project_id": project_id,
                    "selected_action": "complete",
                    "reasoning": "scheduler available",
                },
            )
            trace_output(DevTracePhase.SPECIALIST, key="queued_job_id", value=job_id or "")
        else:
            logger.warning("Coding scheduler unavailable for project_id=%s", project_id)
            trace_decision(
                DevTracePhase.SPECIALIST,
                {
                    "routing_rule": "queue background coding job",
                    "project_id": project_id,
                    "selected_action": "complete",
                    "reasoning": "scheduler unavailable",
                },
            )

        return RoleOutput(
            command="complete",
            response=(
                f"Coding execution queued for {project.get('display_name') or project.get('name')}. "
                f"Job: {job_id or 'n/a'}."
            ),
            result={"project_id": project_id, "job_id": job_id},
        )
