from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any

from core.prompt_library import commander_prompt_block, render_prompt
from core.trace import trace_flow
from core.tracing import trace

logger = logging.getLogger("skynet.core.intent_extractor")


@dataclass(slots=True)
class ExtractedIntent:
    intent: str = "exploratory"
    confidence: float = 0.0
    entities: dict[str, Any] = field(default_factory=dict)
    recommended_role: str | None = None


class IntentExtractor:
    """LLM-based structured intent extraction."""

    def __init__(self, provider_router, *, allowed_providers: list[str] | None = None):
        self._provider_router = provider_router
        self._allowed_providers = allowed_providers
        self._commander_guidance = commander_prompt_block()

    @trace(
        role="igris",
        prompt="prompts/core/intent_extract_user.md",
        step_name="classify_intent",
    )
    async def extract(self, user_text: str, *, active_role: str, active_project_id: str | None) -> ExtractedIntent:
        prompt = render_prompt(
            "core/intent_extract_user.md",
            active_role=active_role,
            active_project_id=active_project_id or "none",
            user_message=user_text[:1200],
        )
        system_prompt = render_prompt(
            "core/intent_extract_system.md",
            commander_guidance=self._commander_guidance,
        ).strip()
        try:
            trace_flow(
                "intent_extract.request",
                active_role=active_role,
                active_project_id=active_project_id or "",
                user_text=user_text[:300],
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
            intent = str(data.get("intent") or "exploratory").strip() or "exploratory"
            confidence = float(data.get("confidence") or 0.0)
            entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
            recommended = data.get("recommended_role")
            recommended_role = str(recommended).strip() if recommended else None
            trace_flow(
                "intent_extract.response",
                intent=intent,
                confidence=confidence,
                recommended_role=recommended_role or "",
                entities=entities,
            )
            return ExtractedIntent(
                intent=intent,
                confidence=max(0.0, min(confidence, 1.0)),
                entities=entities,
                recommended_role=recommended_role,
            )
        except Exception as exc:
            logger.exception("Intent extraction failed")
            trace_flow(
                "intent_extract.error",
                error=str(exc),
                active_role=active_role,
                active_project_id=active_project_id or "",
            )
            return ExtractedIntent(intent="exploratory", confidence=0.0, entities={}, recommended_role="igris")

    @trace(
        role="igris",
        prompt="prompts/core/payload_extract_user.md",
        step_name="extract_payload",
    )
    async def extract_payload(
        self,
        user_text: str,
        schema: dict[str, Any],
        *,
        instruction: str,
    ) -> dict[str, Any]:
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
            trace_flow(
                "payload_extract.request",
                instruction=instruction,
                schema=schema,
                user_text=user_text[:300],
            )
            response = await self._provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=system_prompt,
                max_tokens=260,
                task_type="general",
                allowed_providers=self._allowed_providers,
            )
            data = self._load_json(response.text or "")
            trace_flow(
                "payload_extract.response",
                keys=sorted(data.keys()) if isinstance(data, dict) else [],
                payload=data if isinstance(data, dict) else {},
            )
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.exception("Payload extraction failed")
            trace_flow(
                "payload_extract.error",
                error=str(exc),
                instruction=instruction,
            )
            return {}

    @staticmethod
    def _load_json(raw_text: str) -> dict[str, Any]:
        text = (raw_text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
