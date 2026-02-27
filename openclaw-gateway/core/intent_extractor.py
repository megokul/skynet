"""
Structured intent/payload extraction helpers.

The extractor isolates prompt composition + JSON parsing so callers can work
with stable typed dictionaries/dataclasses instead of raw model responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any

from core.dev_trace import (
    DevTracePhase,
    trace_control_flow,
    trace_data_flow,
    trace_decision,
    trace_output,
)
from core.prompt_library import commander_prompt_block, render_prompt

logger = logging.getLogger("skynet.core.intent_extractor")

_NEW_PROJECT_RE = re.compile(
    r"\b(?:start|create|make|begin|initiate)\b.{0,35}\bproject\b"
    r"|\bnew\s+(?:project|app|application)\b"
    r"|\bproject\b.{0,20}\b(?:start|create|make|begin)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ExtractedIntent:
    """
    ExtractedIntent.
    
    Purpose:
    - Represent a cohesive runtime concept for this subsystem.
    - Group related state and methods behind a single abstraction boundary.
    
    How it works:
    - Holds domain-specific fields and exposes operations that enforce local invariants.
    - Shields calling code from low-level implementation details.
    
    Why this exists:
    - Improves readability by giving the concept an explicit named type.
    - Reduces coupling by centralizing behavior inside `ExtractedIntent`.
    """

    intent: str = "exploratory"
    confidence: float = 0.0
    entities: dict[str, Any] = field(default_factory=dict)
    recommended_role: str | None = None


class IntentExtractor:
    """
    Thin gateway around schema-constrained LLM extraction calls.

    Design goals:
    - Keep orchestration deterministic: caller decides routing semantics.
    - Keep model usage narrow: only structured extraction here.
    - Keep failure mode safe: always return parseable fallback objects.
    """

    def __init__(self, provider_router, *, allowed_providers: list[str] | None = None):
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
        - `allowed_providers`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self._provider_router = provider_router
        self._allowed_providers = allowed_providers
        self._commander_guidance = commander_prompt_block()

    async def classify_intent(
        self,
        user_text: str,
        *,
        active_role: str,
        active_project_id: str | None,
    ) -> ExtractedIntent:
        # Deterministic short-circuit for explicit project-start phrases.
        # This keeps routing stable even when providers return unstructured text
        # and avoids unnecessary LLM calls for obvious commands.
        """
        Extract.
        
        Purpose:
        - Implement `extract` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `user_text`: input used by this function to compute or route work.
        - `active_role`: input used by this function to compute or route work.
        - `active_project_id`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `ExtractedIntent` when available; otherwise side effects only.
        """

        trace_control_flow(DevTracePhase.INTENT, stack_depth=2)
        trace_data_flow(
            DevTracePhase.INTENT,
            source_name="user_input",
            source_value=user_text,
            target_name="intent_classifier.input",
            target_value=user_text[:1200],
        )
        heuristic = self._heuristic_intent(user_text)
        if heuristic is not None:
            trace_decision(
                DevTracePhase.INTENT,
                {
                    "classifier_confidence": heuristic.confidence,
                    "evaluated_intents": [f"{heuristic.intent} ({heuristic.confidence:.2f})"],
                    "selected_intent": heuristic.intent,
                    "recommended_role": heuristic.recommended_role or "",
                    "reasoning": "deterministic heuristic matched high-signal phrase",
                },
            )
            trace_output(
                DevTracePhase.INTENT,
                key="intent_result",
                value={
                    "intent": heuristic.intent,
                    "confidence": heuristic.confidence,
                    "recommended_role": heuristic.recommended_role or "",
                },
            )
            return heuristic

        # User prompt carries only the context needed for classification.
        # We intentionally keep this small to reduce latency and reduce drift.
        prompt = render_prompt(
            "core/intent_extract_user.md",
            active_role=active_role,
            active_project_id=active_project_id or "none",
            user_message=user_text[:1200],
        )
        # System prompt is composed from active prompt files; no inline prompt strings.
        system_prompt = render_prompt(
            "core/intent_extract_system.md",
            commander_guidance=self._commander_guidance,
        ).strip()
        try:
            trace_decision(
                DevTracePhase.INTENT,
                {
                    "routing_rule": "llm structured classification",
                    "active_role": active_role,
                    "active_project_id": active_project_id or "",
                    "reasoning": "heuristic path not matched",
                },
            )
            response = await self._provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system_prompt,
                max_tokens=220,
                task_type="general",
                allowed_providers=self._allowed_providers,
            )
            data = self._load_json(response.text or "")
            # Normalization below makes downstream handling predictable even when
            # providers return partial or malformed JSON fields.
            intent = str(data.get("intent") or "exploratory").strip() or "exploratory"
            confidence = float(data.get("confidence") or 0.0)
            entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
            recommended = data.get("recommended_role")
            recommended_role = str(recommended).strip() if recommended else None
            bounded_confidence = max(0.0, min(confidence, 1.0))
            trace_data_flow(
                DevTracePhase.INTENT,
                source_name="model_response.text",
                source_value=response.text or "",
                target_name="normalized_intent",
                target_value={
                    "intent": intent,
                    "confidence": bounded_confidence,
                    "recommended_role": recommended_role or "",
                    "entities": entities,
                },
            )
            trace_decision(
                DevTracePhase.INTENT,
                {
                    "classifier_confidence": bounded_confidence,
                    "evaluated_intents": [f"{intent} ({bounded_confidence:.2f})"],
                    "selected_intent": intent,
                    "reasoning": "highest confidence from classifier response",
                },
            )
            trace_output(
                DevTracePhase.INTENT,
                key="intent_result",
                value={
                    "intent": intent,
                    "confidence": bounded_confidence,
                    "recommended_role": recommended_role or "",
                },
            )
            return ExtractedIntent(
                intent=intent,
                confidence=bounded_confidence,
                entities=entities,
                recommended_role=recommended_role,
            )
        except Exception as exc:
            # Extraction failures must never break the request path.
            # We default to exploratory + low confidence so the caller can choose
            # conservative behavior.
            logger.exception("Intent extraction failed")
            trace_decision(
                DevTracePhase.INTENT,
                {
                    "selected_intent": "exploratory",
                    "classifier_confidence": 0.0,
                    "reasoning": f"classifier error fallback: {exc}",
                },
            )
            trace_output(
                DevTracePhase.INTENT,
                key="intent_result",
                value={
                    "intent": "exploratory",
                    "confidence": 0.0,
                    "recommended_role": "igris",
                },
            )
            return ExtractedIntent(intent="exploratory", confidence=0.0, entities={}, recommended_role="igris")

    async def extract(self, user_text: str, *, active_role: str, active_project_id: str | None) -> ExtractedIntent:
        """Compatibility alias for callers still using the old method name."""
        return await self.classify_intent(
            user_text,
            active_role=active_role,
            active_project_id=active_project_id,
        )

    @staticmethod
    def _heuristic_intent(user_text: str) -> ExtractedIntent | None:
        """Return deterministic intents for high-signal command phrases."""
        text = (user_text or "").strip()
        if not text:
            return None
        if _NEW_PROJECT_RE.search(text):
            return ExtractedIntent(
                intent="start_project",
                confidence=0.95,
                entities={},
                recommended_role="project_specialist",
            )
        return None

    async def extract_payload(
        self,
        user_text: str,
        schema: dict[str, Any],
        *,
        instruction: str,
    ) -> dict[str, Any]:
        # Payload extraction is strict JSON. The schema parameter is echoed into
        # the prompt so callers can enforce task-specific payload contracts.
        """
        Extract payload.
        
        Purpose:
        - Implement `extract_payload` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `user_text`: input used by this function to compute or route work.
        - `schema`: input used by this function to compute or route work.
        - `instruction`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        prompt = render_prompt(
            "core/payload_extract_user.md",
            instruction=instruction,
            schema=json.dumps(schema),
            user_message=user_text[:1400],
        )
        system_prompt = render_prompt(
            "core/payload_extract_system.md",
            commander_guidance=self._commander_guidance,
        ).strip()
        try:
            response = await self._provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system_prompt,
                max_tokens=260,
                task_type="general",
                allowed_providers=self._allowed_providers,
            )
            data = self._load_json(response.text or "")
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            # Callers expect a dict in all cases; empty dict is the safe default.
            logger.exception("Payload extraction failed")
            return {}

    @staticmethod
    def _load_json(raw_text: str) -> dict[str, Any]:
        # Providers sometimes wrap JSON in markdown fences; strip them first.
        """
        Load json.
        
        Purpose:
        - Implement `_load_json` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `raw_text`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        text = (raw_text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
