"""
SKYNET Policy Engine — Core Engine

Gateway-side risk classification and approval enforcement.
Evaluates PlanSpecs and ExecutionSpecs against policy rules
before any work reaches CHATHAN Workers.

Replaces the per-skill approval model with a system-wide policy layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from chathan.protocol.plan_spec import PlanSpec
from chathan.protocol.execution_spec import ExecutionSpec, ExecutionStep

from .rules import (
    ACTION_RISK,
    BLOCKED_ACTIONS,
    classify_action_risk,
    risk_exceeds,
)

logger = logging.getLogger("skynet.policy")


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""

    allowed: bool = True
    requires_approval: bool = False
    risk_level: str = "READ_ONLY"
    reasons: list[str] = field(default_factory=list)
    blocked_steps: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
            "reasons": self.reasons,
            "blocked_steps": self.blocked_steps,
        }


class PolicyEngine:
    """
    SKYNET system-wide policy engine.

    Classifies risk levels, checks approval requirements,
    and validates execution specs against policy constraints.
    """

    def __init__(self, auto_approve_read_only: bool = True):
        self.auto_approve_read_only = auto_approve_read_only

    def classify_risk(self, plan_spec: PlanSpec) -> str:
        """Classify overall plan risk: READ_ONLY | WRITE | ADMIN."""
        return plan_spec.max_risk_level

    def requires_approval(self, risk_level: str) -> bool:
        """
        Determine if a risk level requires user approval.

        READ_ONLY → no (if auto_approve_read_only is True)
        WRITE     → yes
        ADMIN     → yes (explicit confirmation required)
        """
        if risk_level == "READ_ONLY" and self.auto_approve_read_only:
            return False
        return risk_level in ("WRITE", "ADMIN")

    def validate_plan(self, plan_spec: PlanSpec) -> PolicyDecision:
        """Evaluate a PlanSpec against policy rules."""
        decision = PolicyDecision()
        decision.risk_level = self.classify_risk(plan_spec)
        decision.requires_approval = self.requires_approval(decision.risk_level)

        for i, step in enumerate(plan_spec.steps):
            if step.risk_level == "BLOCKED":
                decision.allowed = False
                decision.blocked_steps.append(i)
                decision.reasons.append(
                    f"Step {i} '{step.title}' has BLOCKED risk level."
                )

        if not plan_spec.steps:
            decision.reasons.append("Plan has no steps.")

        return decision

    def validate_execution(self, exec_spec: ExecutionSpec) -> PolicyDecision:
        """
        Check an ExecutionSpec against policy rules before dispatching.

        Returns a PolicyDecision indicating whether execution should proceed.
        """
        decision = PolicyDecision()
        decision.risk_level = exec_spec.risk_level
        decision.requires_approval = self.requires_approval(exec_spec.risk_level)

        for i, step in enumerate(exec_spec.steps):
            action_risk = classify_action_risk(step.action)

            # Check if action is permanently blocked.
            if action_risk == "BLOCKED":
                decision.allowed = False
                decision.blocked_steps.append(i)
                decision.reasons.append(
                    f"Step {i}: action '{step.action}' is permanently blocked."
                )
                continue

            # Check if action risk exceeds spec risk level.
            if risk_exceeds(action_risk, exec_spec.risk_level):
                decision.allowed = False
                decision.blocked_steps.append(i)
                decision.reasons.append(
                    f"Step {i}: action '{step.action}' requires {action_risk} "
                    f"but spec allows max {exec_spec.risk_level}."
                )

            # Flag steps that need individual approval.
            if step.requires_approval:
                decision.requires_approval = True

        if not exec_spec.steps:
            decision.reasons.append("ExecutionSpec has no steps.")

        return decision

    def check_action(self, action: str, current_risk_level: str = "WRITE") -> PolicyDecision:
        """
        Quick check for a single action against policy.

        Used by skill execution to validate individual tool calls.
        """
        decision = PolicyDecision()
        action_risk = classify_action_risk(action)

        if action_risk == "BLOCKED":
            decision.allowed = False
            decision.risk_level = "BLOCKED"
            decision.reasons.append(f"Action '{action}' is permanently blocked.")
            return decision

        decision.risk_level = action_risk
        decision.requires_approval = self.requires_approval(action_risk)

        if risk_exceeds(action_risk, current_risk_level):
            decision.allowed = False
            decision.reasons.append(
                f"Action '{action}' requires {action_risk} but current level is {current_risk_level}."
            )

        return decision

    def get_blocked_actions(self) -> set[str]:
        """Return the set of permanently blocked actions."""
        return BLOCKED_ACTIONS.copy()
