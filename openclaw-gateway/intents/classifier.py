from __future__ import annotations

import json
import re
from typing import Any


class IntentClassifier:
    """
    Deterministic intent classifier.
    """

    def classify_intent(self, user_text: str) -> str:
        text = (user_text or "").strip().lower()
        if not text:
            return "unclear"
        if text.startswith("/"):
            return "memory_command"
        if re.search(r"\b(generate|create|make)\b.{0,20}\bplan\b", text):
            return "generate_plan"
        if re.search(r"\b(architecture|design)\b", text):
            return "discuss_architecture"
        if re.search(r"\b(debug|troubleshoot|investigate)\b", text):
            return "debug_strategy"
        if re.search(r"\b(note|remember)\b", text):
            return "add_note"
        if re.search(r"\b(add|create)\b.{0,10}\btask\b", text):
            return "add_task"
        if re.search(r"\bidea\b", text) or len(text.split()) >= 5:
            return "propose_idea"
        return "exploratory_conversation"


class PayloadExtractor:
    """
    Schema-constrained payload extraction.
    LLM is used only for payload extraction, never routing decisions.
    """

    def __init__(self, provider_router):
        self.provider_router = provider_router

    async def extract_payload(
        self,
        user_text: str,
        schema: dict[str, Any],
        *,
        allowed_providers: list[str] | None = None,
    ) -> dict[str, Any]:
        prompt = (
            "Extract structured payload from the user message.\n"
            "Return ONLY valid JSON that matches this schema exactly:\n"
            f"{json.dumps(schema)}\n\n"
            f"User message: {user_text[:1200]}"
        )
        try:
            response = await self.provider_router.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=220,
                task_type="general",
                allowed_providers=allowed_providers,
            )
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
