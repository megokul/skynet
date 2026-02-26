"""Natural-language intent classification helpers for bot orchestration.

Purpose:
- Convert free-form user text into normalized intent categories.
- Blend deterministic pre/post overrides with LLM classification output.
- Extract entities and confidence values used by routing/mode selection.

How it works:
- Applies lightweight regex preclassification for high-confidence cases.
- Calls provider chat endpoint with a strict intent-classifier prompt.
- Validates and normalizes structured response fields with safe fallback logic.

Why this exists:
- Centralized intent semantics reduce drift across downstream decision code.
- Deterministic guards improve reliability when model output is malformed.
- Explicit categories allow stable analytics and regression testing."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from bot.message_utils import strip_tool_messages
from bot.session import Session
from core.prompt_library import render_prompt

logger = logging.getLogger(__name__)

INTENT_CATEGORIES = [
    "greeting", "casual_conversation", "ask_question", "request_explanation",
    "propose_idea", "request_plan", "approve_plan", "reject_plan",
    "request_execution", "request_fix", "approve_execution", "request_review",
    "request_continue", "request_stop", "change_direction", "provide_feedback",
    "memory_command", "unclear",
]

_PLAN_REQUEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bplan it\b", re.IGNORECASE),
    re.compile(r"\btask\s*plan\b", re.IGNORECASE),
    re.compile(r"\b(generate|generating|make|create)\b.{0,24}\b(task\s+)?plan\b", re.IGNORECASE),
)
_NEW_PROJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(start|create|make|begin|initiate)\b.{0,24}\b(project|app|application)\b", re.IGNORECASE),
    re.compile(r"\b(new|different)\s+(project|app|application)\b", re.IGNORECASE),
)
_GREETING_RE = re.compile(r"^(hi|hello|hey|yo|hiya|sup|good (morning|afternoon|evening))[!. ]*$", re.IGNORECASE)

_APPROVAL_PHRASES: frozenset[str] = frozenset({
    "yes", "ok", "sure", "go ahead", "do it", "approved", "lgtm", "build it",
})


@dataclass
class ClassifiedIntent:
    """
    ClassifiedIntent.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `ClassifiedIntent`.
    """

    intent: str
    confidence: float
    secondary_intents: list[str] = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    requires_tools: bool = False
    is_continuation: bool = False


class IntentClassifier:
    """
    IntentClassifier.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `IntentClassifier`.
    """

    def __init__(self, provider_router):
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `provider_router`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.provider_router = provider_router

    async def classify(
        self,
        message: str,
        session: Session,
        recent_messages: list[dict],
    ) -> ClassifiedIntent:
        """
        Classify.
        
        Purpose:
        - Implement `classify` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `message`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `recent_messages`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `ClassifiedIntent` when available; otherwise side effects only.
        """

        preclassified = self._preclassify(message, session)
        if preclassified is not None:
            return preclassified

        # Strip tool_result messages -- they confuse classifiers
        recent_messages = strip_tool_messages(recent_messages)

        # Build recent context string from last 2 messages
        recent_context = ""
        for msg in recent_messages[-2:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            recent_context += f"{role}: {content[:200]}\n"

        try:
            prompt = render_prompt(
                "bot/intent_classifier_user.md",
                project_name=session.project["name"] if session.project else "None",
                conversation_phase=session.conversation_phase,
                last_mode=session.last_mode,
                last_intent=session.last_intent,
                recent_context=recent_context.strip() or "(no recent messages)",
                message=message[:500],
            )
            response = await self.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],  # CRITICAL: explicitly disable tools
                allowed_providers=["groq", "gemini"],
                max_tokens=200,
                task_type="general",
            )
            text = (response.text or "").strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(text)

            intent = data.get("intent", "unclear")
            if intent not in INTENT_CATEGORIES:
                intent = "unclear"

            classified = ClassifiedIntent(
                intent=intent,
                confidence=float(data.get("confidence", 0.5)),
                secondary_intents=data.get("secondary_intents", []),
                entities=data.get("entities", {}),
                requires_tools=bool(data.get("requires_tools", False)),
                is_continuation=bool(data.get("is_continuation", False)),
            )
            return self._apply_post_overrides(message, session, classified)
        except Exception as e:
            logger.warning("Intent classifier failed (%s), using fallback", e)
            return self.fallback_classify(message, session)

    def _preclassify(self, message: str, session: Session) -> ClassifiedIntent | None:
        """
        Preclassify.
        
        Purpose:
        - Implement `_preclassify` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `message`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `ClassifiedIntent | None` when available; otherwise side effects only.
        """

        msg = (message or "").strip()
        if not msg:
            return ClassifiedIntent("unclear", 1.0, [], {}, False, False)

        if _GREETING_RE.match(msg):
            return ClassifiedIntent("greeting", 0.98, [], {}, False, False)

        lowered = msg.lower()
        if lowered.startswith("/"):
            return ClassifiedIntent("memory_command", 1.0, [], {}, False, False)

        if any(pattern.search(msg) for pattern in _PLAN_REQUEST_PATTERNS):
            return ClassifiedIntent("request_plan", 0.95, [], {}, True, False)

        if any(pattern.search(msg) for pattern in _NEW_PROJECT_PATTERNS):
            return ClassifiedIntent("propose_idea", 0.86, [], {}, True, False)

        # Feature-like text should go through deterministic write routing when a project is active.
        if session.project_id and len(lowered.split()) >= 4:
            return ClassifiedIntent("propose_idea", 0.75, [], {}, True, True)

        return None

    def _apply_post_overrides(
        self,
        message: str,
        session: Session,
        classified: ClassifiedIntent,
    ) -> ClassifiedIntent:
        """
        Apply post overrides.
        
        Purpose:
        - Implement `_apply_post_overrides` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `message`: input used by this function to compute or route work.
        - `session`: input used by this function to compute or route work.
        - `classified`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `ClassifiedIntent` when available; otherwise side effects only.
        """

        msg = (message or "").strip().lower()

        # Explicit planning phrases should never be interpreted as approval.
        if any(pattern.search(msg) for pattern in _PLAN_REQUEST_PATTERNS):
            return ClassifiedIntent(
                intent="request_plan",
                confidence=max(classified.confidence, 0.9),
                secondary_intents=classified.secondary_intents,
                entities=classified.entities,
                requires_tools=True,
                is_continuation=classified.is_continuation,
            )

        # Approval phrases map to approve_plan only while waiting for plan approval.
        if msg in _APPROVAL_PHRASES:
            waiting_for = str(session.metadata.get("waiting_for") or "")
            pending_action = session.metadata.get("pending_action") or {}
            if (
                isinstance(pending_action, dict)
                and str(pending_action.get("type") or "") == "approve_plan"
            ) or session.conversation_phase == "planning" or waiting_for == "plan_approval":
                return ClassifiedIntent(
                    intent="approve_plan",
                    confidence=max(classified.confidence, 0.85),
                    secondary_intents=classified.secondary_intents,
                    entities=classified.entities,
                    requires_tools=False,
                    is_continuation=True,
                )
            return ClassifiedIntent(
                intent="approve_execution",
                confidence=max(classified.confidence, 0.6),
                secondary_intents=classified.secondary_intents,
                entities=classified.entities,
                requires_tools=False,
                is_continuation=True,
            )

        return classified

    def fallback_classify(self, message: str, session: Session) -> ClassifiedIntent:
        """Rule-based fallback when LLM classifier fails."""
        msg = message.lower().strip()
        pending_action = session.metadata.get("pending_action") or {}
        waiting_for = str(session.metadata.get("waiting_for") or "")

        if msg in ("yes", "ok", "sure", "go ahead", "do it", "approved", "lgtm", "build it"):
            if (
                isinstance(pending_action, dict)
                and str(pending_action.get("type") or "") == "approve_plan"
            ):
                return ClassifiedIntent("approve_plan", 0.9, [], {}, False, True)
            if session.conversation_phase == "planning":
                return ClassifiedIntent("approve_plan", 0.8, [], {}, False, True)
            if waiting_for == "plan_approval":
                return ClassifiedIntent("approve_plan", 0.85, [], {}, False, True)
            return ClassifiedIntent("approve_execution", 0.6, [], {}, False, True)

        if msg.startswith("/"):
            return ClassifiedIntent("memory_command", 1.0, [], {}, False, False)

        if any(pattern.search(msg) for pattern in _NEW_PROJECT_PATTERNS):
            return ClassifiedIntent("propose_idea", 0.85, [], {}, True, False)

        if any(w in msg for w in ("plan", "steps", "break down")):
            return ClassifiedIntent("request_plan", 0.6, [], {}, True, False)

        if _GREETING_RE.match(msg):
            return ClassifiedIntent("greeting", 0.9, [], {}, False, False)

        if session.project_id and len(msg.split()) >= 4 and not msg.startswith("/"):
            return ClassifiedIntent("propose_idea", 0.72, [], {}, True, False)

        return ClassifiedIntent("casual_conversation", 0.5, [], {}, False, False)
