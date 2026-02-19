"""
SKYNET — OpenAI-Compatible Provider Adapter

Covers OpenAI, OpenRouter, and DeepSeek since they all expose the
same chat completions API.  Instantiate with different base URLs:

    OpenAI:      https://api.openai.com/v1
    OpenRouter:  https://openrouter.ai/api/v1
    DeepSeek:    https://api.deepseek.com
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from .base import BaseProvider, ProviderResponse, ToolCall

logger = logging.getLogger("skynet.ai.openai_compat")


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _convert_messages(
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> list[dict[str, Any]]:
    result = []
    if system:
        result.append({"role": "system", "content": system})
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        if isinstance(content, list):
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
                text = " ".join(
                    item.get("text", str(item)) if isinstance(item, dict) else str(item)
                    for item in content
                )
                result.append({"role": role, "content": text})
        else:
            result.append({"role": role, "content": content})
    return result


class OpenAICompatProvider(BaseProvider):
    """Provider using the OpenAI chat completions API."""

    supports_tool_use = True

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        model_candidates: list[str] | None = None,
        base_url: str | None = None,
        provider_name: str = "openai",
        daily_limit_override: int | None = None,
        rpm_limit_override: int | None = None,
        context_limit_override: int | None = None,
        cost_rank_override: int | None = None,
    ):
        self.name = provider_name
        if daily_limit_override is not None:
            self.daily_limit = daily_limit_override
        if rpm_limit_override is not None:
            self.rpm_limit = rpm_limit_override
        if context_limit_override is not None:
            self.context_limit = context_limit_override
        if cost_rank_override is not None:
            self.cost_rank = cost_rank_override
        self._model_candidates = [m.strip() for m in (model_candidates or []) if m and m.strip()]
        if model and model not in self._model_candidates:
            self._model_candidates.insert(0, model)
        super().__init__(api_key, model)
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    @property
    def default_model(self) -> str:
        return "gpt-4o-mini"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        oai_messages = _convert_messages(messages, system)
        models_to_try: list[str] = [self.model_name]
        for candidate in self._model_candidates:
            if candidate not in models_to_try:
                models_to_try.append(candidate)

        last_error: Exception | None = None
        response = None
        for model_name in models_to_try:
            kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": oai_messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }
            if tools:
                kwargs["tools"] = _convert_tools(tools)
                kwargs["tool_choice"] = "auto"
            try:
                response = await self._client.chat.completions.create(**kwargs)
                if model_name != self.model_name:
                    logger.warning(
                        "[%s] switching model from '%s' to '%s' after fallback",
                        self.name,
                        self.model_name,
                        model_name,
                    )
                    self.model_name = model_name
                break
            except Exception as exc:
                last_error = exc
                if self._is_model_not_found_error(exc):
                    logger.warning(
                        "[%s] model unavailable: %s (trying next candidate if any)",
                        self.name,
                        model_name,
                    )
                    continue
                raise

        if response is None:
            raise last_error if last_error else RuntimeError("Provider request failed with no response.")
        choice = response.choices[0] if response.choices else None
        text = ""
        tool_calls = []

        if choice and choice.message:
            text = choice.message.content or ""
            if choice.message.tool_calls:
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

    @staticmethod
    def _is_model_not_found_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "no endpoints found" in text
            or "model_not_found" in text
            or "does not exist" in text
            or ("404" in text and "model" in text)
        )
