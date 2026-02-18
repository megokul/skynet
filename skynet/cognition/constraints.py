"""
Safety Constraints — Limits on autonomous initiative.

Prevents runaway task generation and ensures safe autonomous operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class SafetyConstraints:
    """
    Safety constraints for autonomous task generation.

    Prevents SKYNET from:
    - Generating too many tasks
    - Taking dangerous actions
    - Operating without approval on risky tasks
    """

    # Rate limiting
    max_tasks_per_hour: int = 5
    max_tasks_per_day: int = 20
    max_concurrent_autonomous_tasks: int = 2

    # Risk level constraints
    allowed_risk_levels: list[str] = None  # type: ignore
    require_approval_for: list[str] = None  # type: ignore

    # Time constraints
    min_idle_time_seconds: int = 300  # 5 minutes idle before initiating
    max_task_duration_seconds: int = 600  # 10 minutes max for autonomous tasks

    # Action blacklist
    forbidden_actions: list[str] = None  # type: ignore

    def __post_init__(self):
        """Initialize default lists."""
        if self.allowed_risk_levels is None:
            self.allowed_risk_levels = ["READ_ONLY"]  # Only safe actions

        if self.require_approval_for is None:
            self.require_approval_for = [
                "deploy",
                "delete",
                "modify_code",
                "push",
                "production",
            ]

        if self.forbidden_actions is None:
            self.forbidden_actions = [
                "deploy_prod",
                "delete_database",
                "modify_production",
                "force_push",
            ]

    def check_task_allowed(
        self, task_description: str, risk_level: str
    ) -> tuple[bool, str]:
        """
        Check if task is allowed under safety constraints.

        Args:
            task_description: Task description
            risk_level: Task risk level

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Check risk level
        if risk_level not in self.allowed_risk_levels:
            return (
                False,
                f"Risk level '{risk_level}' not in allowed list: {self.allowed_risk_levels}",
            )

        # Check for forbidden actions
        desc_lower = task_description.lower()
        for forbidden in self.forbidden_actions:
            if forbidden.lower() in desc_lower:
                return (False, f"Task contains forbidden action: {forbidden}")

        # Check for actions requiring approval
        for action in self.require_approval_for:
            if action.lower() in desc_lower:
                return (
                    False,
                    f"Task requires approval (contains '{action}')",
                )

        return (True, "Task allowed")

    def check_rate_limit(self, task_history: list[dict[str, Any]]) -> tuple[bool, str]:
        """
        Check if rate limits are exceeded.

        Args:
            task_history: List of recent autonomous tasks

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        now = datetime.utcnow()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)

        # Count recent tasks
        tasks_last_hour = sum(
            1
            for task in task_history
            if datetime.fromisoformat(task["created_at"]) > hour_ago
        )

        tasks_last_day = sum(
            1
            for task in task_history
            if datetime.fromisoformat(task["created_at"]) > day_ago
        )

        # Check hourly limit
        if tasks_last_hour >= self.max_tasks_per_hour:
            return (
                False,
                f"Hourly limit exceeded: {tasks_last_hour}/{self.max_tasks_per_hour}",
            )

        # Check daily limit
        if tasks_last_day >= self.max_tasks_per_day:
            return (
                False,
                f"Daily limit exceeded: {tasks_last_day}/{self.max_tasks_per_day}",
            )

        return (True, "Rate limit OK")


# ============================================================================
# Default Constraints
# ============================================================================

# Global default constraints for all autonomous tasks
INITIATIVE_CONSTRAINTS = SafetyConstraints(
    max_tasks_per_hour=5,
    max_tasks_per_day=20,
    max_concurrent_autonomous_tasks=2,
    allowed_risk_levels=["READ_ONLY"],  # Only read-only tasks
    require_approval_for=["deploy", "delete", "modify_code", "push", "production"],
    forbidden_actions=[
        "deploy_prod",
        "delete_database",
        "modify_production",
        "force_push",
        "rm -rf",
        "drop table",
    ],
    min_idle_time_seconds=300,  # 5 minutes
    max_task_duration_seconds=600,  # 10 minutes
)
