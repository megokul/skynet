"""
SKYNET AI Client — Gemini (Google AI)

Simple wrapper for Google's Gemini API for planning tasks.
"""

from __future__ import annotations

import os
import logging
from typing import Any

try:
    import google.generativeai as genai
except ImportError:
    raise ImportError(
        "google-generativeai not installed. "
        "Install with: pip install google-generativeai"
    )

logger = logging.getLogger("skynet.ai.gemini")


class GeminiResponse:
    """Wrapper for Gemini response to match our interface."""

    def __init__(self, text: str):
        self.text = text
        self.tool_calls = []  # Gemini tool calling handled differently


class GeminiClient:
    """
    Google Gemini client for AI planning.

    Uses the google-generativeai library to call Gemini models.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.0-flash-exp",
    ):
        """
        Initialize Gemini client.

        Args:
            api_key: Google AI API key (or set GOOGLE_AI_API_KEY env var)
            model: Gemini model to use
                - gemini-2.0-flash-exp (recommended, fast, good quality)
                - gemini-1.5-pro (more capable, slower)
                - gemini-1.5-flash (faster, lower quality)
        """
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "GOOGLE_AI_API_KEY not found. "
                "Set environment variable or pass api_key parameter."
            )

        genai.configure(api_key=self.api_key)
        self.model_name = model
        self.model = genai.GenerativeModel(model)

        logger.info(f"Gemini client initialized with model: {model}")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> GeminiResponse:
        """
        Chat completion with Gemini.

        Args:
            messages: List of message dicts with 'role' and 'content'
                     Format: [{"role": "user", "content": "..."}]
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0-1.0)
            system_prompt: Optional system instructions

        Returns:
            GeminiResponse with .text attribute
        """
        # Convert our message format to Gemini format
        gemini_messages = self._convert_messages(messages)

        # Configure generation
        generation_config = genai.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        # Build model with system instruction if provided
        if system_prompt:
            model = genai.GenerativeModel(
                self.model_name,
                system_instruction=system_prompt,
            )
        else:
            model = self.model

        try:
            # Generate response
            # Note: Using generate_content (sync) for now
            # For async, we'd need generate_content_async
            response = model.generate_content(
                gemini_messages,
                generation_config=generation_config,
            )

            # Extract text from response
            text = response.text

            logger.debug(f"Gemini response: {len(text)} chars")

            return GeminiResponse(text=text)

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

    def _convert_messages(self, messages: list[dict]) -> list[dict] | str:
        """
        Convert our message format to Gemini format.

        Our format: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        Gemini format: For simple cases, just pass the user content as string.
                      For multi-turn, pass list of {"role": "user"/"model", "parts": [...]}
        """
        # If single user message, just return content
        if len(messages) == 1 and messages[0]["role"] == "user":
            return messages[0]["content"]

        # Multi-turn conversation
        gemini_messages = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            # Map our roles to Gemini roles
            if role == "user":
                gemini_role = "user"
            elif role == "assistant":
                gemini_role = "model"
            else:
                # Skip system messages (handled via system_instruction)
                continue

            gemini_messages.append({
                "role": gemini_role,
                "parts": [{"text": content}],
            })

        return gemini_messages


# Convenience function for quick usage
async def chat_gemini(
    prompt: str,
    api_key: str | None = None,
    temperature: float = 0.7,
) -> str:
    """
    Quick chat with Gemini.

    Example:
        response = await chat_gemini("Plan a deployment task")
        print(response)
    """
    client = GeminiClient(api_key=api_key)
    response = await client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return response.text
