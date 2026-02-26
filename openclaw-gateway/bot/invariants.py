"""Deterministic continuity and scope invariants for orchestrator routing.

Purpose:
- Resolve project scope from text, pending state, and session context.
- Enforce write-intent guardrails before tool execution.
- Provide typed decision records for debugging and tests.

How it works:
- Parses scope-switch phrases and explicit project references via regex rules.
- Interprets pending-question answers with expiration checks.
- Produces RoutingDecision objects indicating execute-vs-ask behavior.

Why this exists:
- Avoids accidental writes to wrong project scope.
- Keeps safety-critical routing deterministic and independent from LLM variance.
- Makes continuity behavior transparent and regression-test friendly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Literal


ScopeType = Literal["active", "new", "unknown", "explicit_project"]


@dataclass
class PendingQuestion:
    """
    PendingQuestion.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `PendingQuestion`.
    """

    type: str
    choices: list[str]
    turn_id: str
    expires_at: str
    payload: dict[str, Any] | None = None
    created_at: str | None = None


@dataclass
class PendingAction:
    """
    PendingAction.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `PendingAction`.
    """

    type: str
    project_id: str
    plan_id: int | None = None
    created_at: str | None = None
    expires_at: str | None = None


@dataclass
class ScopeResolution:
    """
    ScopeResolution.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `ScopeResolution`.
    """

    scope: ScopeType
    project_id: str | None
    explicit_project_ref: str | None
    switch_intent: bool
    scope_answer: Literal["new", "existing"] | None
    reason: str


@dataclass
class RoutingDecision:
    """
    RoutingDecision.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `RoutingDecision`.
    """

    target_project_id: str | None
    execute_write_intent: bool
    ask_question: bool
    question_type: str | None
    reason: str


_SWITCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:switch|use)\s+(?:to\s+)?project\b", re.IGNORECASE),
    re.compile(r"\b(?:new|different)\s+(?:project|app|application)\b", re.IGNORECASE),
    re.compile(r"\b(?:create|start|make|begin)\s+(?:a\s+)?new\s+(?:project|app|application)\b", re.IGNORECASE),
    re.compile(r"\b(?:create|start|make|begin|initiate)\b.{0,24}\b(?:project|app|application)\b", re.IGNORECASE),
)
_EXPLICIT_PROJECT_REF_RE = re.compile(
    r"\b(?:use|switch\s+to|for)\s+project\s+([a-zA-Z0-9][a-zA-Z0-9_\-\s]{1,63})\b",
    re.IGNORECASE,
)
_NEW_SCOPE_ANSWER = {"new", "new one", "different", "different project"}
_EXISTING_SCOPE_ANSWER = {"existing", "current", "same", "same one", "this one", "active"}


def detect_switch_intent(user_text: str) -> bool:
    """
    Detect switch intent.
    
    Purpose:
    - Implement `detect_switch_intent` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `user_text`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `bool` when available; otherwise side effects only.
    """

    text = (user_text or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _SWITCH_PATTERNS)


def normalize_scope_answer(user_text: str) -> Literal["new", "existing"] | None:
    """
    Normalize scope answer.
    
    Purpose:
    - Implement `normalize_scope_answer` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `user_text`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `Literal['new', 'existing'] | None` when available; otherwise side effects only.
    """

    text = re.sub(r"\s+", " ", (user_text or "").strip().lower())
    if text in _NEW_SCOPE_ANSWER:
        return "new"
    if text in _EXISTING_SCOPE_ANSWER:
        return "existing"
    return None


def resolve_scope(session: Any, user_text: str, last_bot_turn: str) -> ScopeResolution:
    """
    Resolve scope.
    
    Purpose:
    - Implement `resolve_scope` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `session`: input used by this function to compute or route work.
    - `user_text`: input used by this function to compute or route work.
    - `last_bot_turn`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `ScopeResolution` when available; otherwise side effects only.
    """

    text = (user_text or "").strip()
    metadata = getattr(session, "metadata", {}) or {}
    pending_question = metadata.get("pending_question")
    answer = normalize_scope_answer(text)

    if (
        isinstance(pending_question, dict)
        and pending_question.get("type") == "choose_project_scope"
        and _is_not_expired(pending_question.get("expires_at"))
        and answer is not None
    ):
        if answer == "existing" and getattr(session, "project_id", None):
            return ScopeResolution(
                scope="active",
                project_id=str(session.project_id),
                explicit_project_ref=None,
                switch_intent=False,
                scope_answer=answer,
                reason="scope_answer_existing",
            )
        if answer == "new":
            return ScopeResolution(
                scope="new",
                project_id=None,
                explicit_project_ref=None,
                switch_intent=True,
                scope_answer=answer,
                reason="scope_answer_new",
            )

    explicit_match = _EXPLICIT_PROJECT_REF_RE.search(text)
    explicit_ref = explicit_match.group(1).strip() if explicit_match else None
    switch_intent = detect_switch_intent(text)
    if switch_intent:
        if explicit_ref:
            return ScopeResolution(
                scope="explicit_project",
                project_id=None,
                explicit_project_ref=explicit_ref,
                switch_intent=True,
                scope_answer=None,
                reason="explicit_project_ref",
            )
        if re.search(r"\b(?:new|different)\s+(?:project|app|application)\b", text, re.IGNORECASE):
            return ScopeResolution(
                scope="new",
                project_id=None,
                explicit_project_ref=None,
                switch_intent=True,
                scope_answer=None,
                reason="explicit_new_project_switch",
            )
        if not getattr(session, "project_id", None):
            return ScopeResolution(
                scope="new",
                project_id=None,
                explicit_project_ref=None,
                switch_intent=True,
                scope_answer=None,
                reason="switch_intent_no_active_project",
            )
        return ScopeResolution(
            scope="unknown",
            project_id=None,
            explicit_project_ref=None,
            switch_intent=True,
            scope_answer=None,
            reason="switch_intent_without_target",
        )

    project_id = getattr(session, "project_id", None)
    if project_id:
        return ScopeResolution(
            scope="active",
            project_id=str(project_id),
            explicit_project_ref=None,
            switch_intent=False,
            scope_answer=None,
            reason="default_active_project",
        )

    return ScopeResolution(
        scope="unknown",
        project_id=None,
        explicit_project_ref=None,
        switch_intent=False,
        scope_answer=None,
        reason="no_active_project",
    )


def enforce_continuity(intent: Any, scope_resolution: ScopeResolution, session: Any) -> RoutingDecision:
    """
    Enforce continuity.
    
    Purpose:
    - Implement `enforce_continuity` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `intent`: input used by this function to compute or route work.
    - `scope_resolution`: input used by this function to compute or route work.
    - `session`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `RoutingDecision` when available; otherwise side effects only.
    """

    intent_name = str(getattr(intent, "intent", "unclear"))
    is_write_intent = intent_name == "propose_idea"

    if not is_write_intent:
        return RoutingDecision(
            target_project_id=scope_resolution.project_id or getattr(session, "project_id", None),
            execute_write_intent=False,
            ask_question=False,
            question_type=None,
            reason="non_write_intent",
        )

    if scope_resolution.scope == "active" and scope_resolution.project_id:
        return RoutingDecision(
            target_project_id=scope_resolution.project_id,
            execute_write_intent=True,
            ask_question=False,
            question_type=None,
            reason="write_intent_active_scope",
        )

    if scope_resolution.scope == "new":
        return RoutingDecision(
            target_project_id=None,
            execute_write_intent=False,
            ask_question=True,
            question_type="need_project_name",
            reason="write_intent_new_scope_requested",
        )

    if scope_resolution.scope == "explicit_project":
        return RoutingDecision(
            target_project_id=None,
            execute_write_intent=False,
            ask_question=True,
            question_type="resolve_project_reference",
            reason="write_intent_explicit_project_ref",
        )

    fallback_project_id = getattr(session, "project_id", None)
    if fallback_project_id:
        return RoutingDecision(
            target_project_id=str(fallback_project_id),
            execute_write_intent=True,
            ask_question=False,
            question_type=None,
            reason="write_intent_fallback_active_scope",
        )

    return RoutingDecision(
        target_project_id=None,
        execute_write_intent=False,
        ask_question=True,
        question_type="choose_project_scope",
        reason="write_intent_no_scope_available",
    )


def _is_not_expired(expires_at: Any) -> bool:
    """
    Is not expired.
    
    Purpose:
    - Implement `_is_not_expired` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `expires_at`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `bool` when available; otherwise side effects only.
    """

    if not expires_at:
        return True
    try:
        expires = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)
