"""
SKYNET — Groq Provider Adapter

Groq offers extremely fast inference on open models (Llama 3.3 70B,
DeepSeek, etc.).  Free tier: 30 RPM, ~14 400 requests/day.

Groq supports tool_use via the OpenAI-compatible API.
"""

from __future__ import annotations

import logging
from typing import Any

from groq import AsyncGroq

from .base import BaseProvider, ProviderResponse, ToolCall

logger = logging.getLogger("skynet.ai.groq")


def _convert_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wrap tool defs in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        }
        for t in tools
    ]


def _convert_messages_to_openai(
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> list[dict[str, Any]]:
    """Convert normalised messages to OpenAI chat format."""
    result = []
    if system:
        result.append({"role": "system", "content": system})

    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")

        if isinstance(content, list):
            # Could be tool results or multi-part content.
            tool_results = [
                item for item in content
                if isinstance(item, dict) and item.get("type") == "tool_result"
            ]
            if tool_results:
                for tr in tool_results:
                    result.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": tr.get("content", ""),
                    })
            else:
                # Join text parts.
                text = " ".join(
                    item.get("text", str(item)) if isinstance(item, dict) else str(item)
                    for item in content
                )
                result.append({"role": role, "content": text})
        else:
            result.append({"role": role, "content": content})
    return result


class GroqProvider(BaseProvider):
    """Groq cloud inference (OpenAI-compatible API)."""

    name = "groq"
    supports_tool_use = True
    context_limit = 131_072    # 128K context
    cost_rank = 1              # free cloud tier
    daily_limit = 14400
    rpm_limit = 30

    def __init__(self, api_key: str, model: str | None = None):
        super().__init__(api_key, model)
        self._client = AsyncGroq(api_key=api_key)

    @property
    def default_model(self) -> str:
        return "llama-3.3-70b-versatile"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        oai_messages = _convert_messages_to_openai(messages, system)
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if tools:
            kwargs["tools"] = _convert_tools_to_openai(tools)
            kwargs["tool_choice"] = "auto"

        response = await self._client.chat.completions.create(**kwargs)

        choice = response.choices[0] if response.choices else None
        text = ""
        tool_calls = []

        if choice and choice.message:
            text = choice.message.content or ""
            if choice.message.tool_calls:
                import json
                for tc in choice.message.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=json.loads(tc.function.arguments) if tc.function.arguments else {},
                    ))

        stop_reason = "tool_use" if tool_calls else "end_turn"
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        return ProviderResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_name=self.name,
            model=self.model_name,
            raw=response,
        )
