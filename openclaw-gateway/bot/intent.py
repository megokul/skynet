from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from bot.message_utils import strip_tool_messages
from bot.session import Session

logger = logging.getLogger(__name__)

INTENT_CATEGORIES = [
    "greeting", "casual_conversation", "ask_question", "request_explanation",
    "propose_idea", "request_plan", "approve_plan", "reject_plan",
    "request_execution", "request_fix", "approve_execution", "request_review",
    "request_continue", "request_stop", "change_direction", "provide_feedback",
    "memory_command", "unclear",
]


@dataclass
class ClassifiedIntent:
    intent: str
    confidence: float
    secondary_intents: list[str] = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    requires_tools: bool = False
    is_continuation: bool = False


CLASSIFIER_PROMPT = """You are an intent classifier for a software development assistant bot.

Given the user's message and conversation context, classify the intent.

Respond with ONLY a JSON object:
{{
    "intent": "one of: greeting, casual_conversation, ask_question, request_explanation, propose_idea, request_plan, approve_plan, reject_plan, request_execution, request_fix, approve_execution, request_review, request_continue, request_stop, change_direction, provide_feedback, memory_command, unclear",
    "confidence": 0.0 to 1.0,
    "secondary_intents": [],
    "entities": {{"project_name": null, "task_description": null}},
    "requires_tools": false,
    "is_continuation": false
}}

Context:
- Active project: {project_name}
- Project phase: {conversation_phase}
- Last mode: {last_mode}
- Last intent: {last_intent}

Recent messages:
{recent_context}

User message: {message}
"""


class IntentClassifier:
    def __init__(self, provider_router):
        self.provider_router = provider_router

    async def classify(
        self,
        message: str,
        session: Session,
        recent_messages: list[dict],
    ) -> ClassifiedIntent:
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
            prompt = CLASSIFIER_PROMPT.format(
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

            return ClassifiedIntent(
                intent=intent,
                confidence=float(data.get("confidence", 0.5)),
                secondary_intents=data.get("secondary_intents", []),
                entities=data.get("entities", {}),
                requires_tools=bool(data.get("requires_tools", False)),
                is_continuation=bool(data.get("is_continuation", False)),
            )
        except Exception as e:
            logger.warning("Intent classifier failed (%s), using fallback", e)
            return self.fallback_classify(message, session)

    def fallback_classify(self, message: str, session: Session) -> ClassifiedIntent:
        """Rule-based fallback when LLM classifier fails."""
        msg = message.lower().strip()

        if msg in ("yes", "ok", "sure", "go ahead", "do it", "approved", "lgtm", "build it"):
            if session.conversation_phase == "planning":
                return ClassifiedIntent("approve_plan", 0.8, [], {}, False, True)
            if session.metadata.get("waiting_for") == "plan_approval":
                return ClassifiedIntent("approve_plan", 0.85, [], {}, False, True)
            return ClassifiedIntent("approve_execution", 0.6, [], {}, False, True)

        if msg.startswith("/"):
            return ClassifiedIntent("memory_command", 1.0, [], {}, False, False)

        if any(w in msg for w in ("plan", "steps", "break down")):
            return ClassifiedIntent("request_plan", 0.6, [], {}, True, False)

        if any(w in msg for w in ("hi", "hello", "hey")):
            return ClassifiedIntent("greeting", 0.9, [], {}, False, False)

        return ClassifiedIntent("casual_conversation", 0.5, [], {}, False, False)
