"""
SKYNET — Google Gemini Provider Adapter

Uses the ``google-genai`` SDK.  Gemini 2.0 Flash free tier offers
15 RPM and ~1 500 requests/day with 1M tokens/day — making it the
primary workhorse for zero-cost operation.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from google import genai
from google.genai import types

from .base import BaseProvider, ProviderResponse, ToolCall

logger = logging.getLogger("skynet.ai.gemini")


def _convert_tools_to_gemini(tools: list[dict[str, Any]]) -> list[types.Tool]:
    """Convert OpenAI-style tool defs to Gemini function declarations."""
    declarations = []
    for tool in tools:
        schema = tool.get("input_schema", {})
        declarations.append(types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters=schema,
        ))
    return [types.Tool(function_declarations=declarations)]


def _convert_messages_to_gemini(
    messages: list[dict[str, Any]],
) -> list[types.Content]:
    """Convert normalised messages to Gemini Content objects."""
    contents = []
    for msg in messages:
        role = msg["role"]
        # Gemini uses "user" and "model" roles.
        gemini_role = "model" if role == "assistant" else "user"

        raw_content = msg.get("content", "")

        # Handle tool_result messages (list of dicts).
        if isinstance(raw_content, list):
            parts = []
            for item in raw_content:
                if isinstance(item, dict):
                    if item.get("type") == "tool_result":
                        parts.append(types.Part.from_function_response(
                            name=item.get("name", "unknown"),
                            response={"result": item.get("content", "")},
                        ))
                    elif item.get("type") == "text":
                        parts.append(types.Part.from_text(text=item.get("text", "")))
                    else:
                        parts.append(types.Part.from_text(text=json.dumps(item)))
                else:
                    parts.append(types.Part.from_text(text=str(item)))
            contents.append(types.Content(role=gemini_role, parts=parts))
        elif isinstance(raw_content, str):
            contents.append(types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=raw_content)],
            ))
    return contents


class GeminiProvider(BaseProvider):
    """Google Gemini via the genai SDK."""

    name = "gemini"
    supports_tool_use = True
    context_limit = 1_048_576  # Gemini Flash: 1M tokens
    cost_rank = 1              # free cloud tier
    daily_limit = 1500         # conservative; actual free tier may be higher
    rpm_limit = 15

    def __init__(self, api_key: str, model: str | None = None):
        super().__init__(api_key, model)
        self._client = genai.Client(api_key=api_key)

    @property
    def default_model(self) -> str:
        return "gemini-2.0-flash"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.2,
        )
        if system:
            config.system_instruction = system
        if tools:
            config.tools = _convert_tools_to_gemini(tools)

        contents = _convert_messages_to_gemini(messages)

        response = await self._client.aio.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        # Parse response into normalised format.
        text_parts = []
        tool_calls = []

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
                if part.function_call:
                    fc = part.function_call
                    tool_calls.append(ToolCall(
                        id=f"gemini-{uuid.uuid4().hex[:8]}",
                        name=fc.name,
                        input=dict(fc.args) if fc.args else {},
                    ))

        stop_reason = "tool_use" if tool_calls else "end_turn"
        input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

        return ProviderResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_name=self.name,
            model=self.model_name,
            raw=response,
        )
