"""
SKYNET — Anthropic Claude Provider Adapter

Uses the ``anthropic`` SDK.  Only active when free credits are available.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from .base import BaseProvider, ProviderResponse, ToolCall

logger = logging.getLogger("skynet.ai.anthropic")


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic uses the same tool format natively."""
    return tools


def _convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic messages are already in the right format."""
    return messages


class AnthropicProvider(BaseProvider):
    """Anthropic Claude via the official SDK."""

    name = "claude"
    supports_tool_use = True
    context_limit = 200_000    # Claude: 200K tokens
    cost_rank = 9              # paid — use as last resort
    daily_limit = 100          # conservative for free credits
    rpm_limit = 5

    def __init__(self, api_key: str, model: str | None = None):
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
        - `api_key`: input used by this function to compute or route work.
        - `model`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        super().__init__(api_key, model)
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def default_model(self) -> str:
        """
        Default model.
        
        Purpose:
        - Implement `default_model` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        return "claude-sonnet-4-20250514"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        """
        Chat.
        
        Purpose:
        - Implement `chat` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `messages`: input used by this function to compute or route work.
        - `tools`: input used by this function to compute or route work.
        - `system`: input used by this function to compute or route work.
        - `max_tokens`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `ProviderResponse` when available; otherwise side effects only.
        """

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "messages": _convert_messages(messages),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _convert_tools(tools)

        response = await self._client.messages.create(**kwargs)

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        stop_reason = "tool_use" if tool_calls else "end_turn"
        input_tokens = response.usage.input_tokens if response.usage else 0
        output_tokens = response.usage.output_tokens if response.usage else 0

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
