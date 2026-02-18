"""
CHATHAN Worker — Ollama Local Inference

Proxies AI chat requests through the locally running Ollama server.
This is invoked as an AUTO-tier action so the EC2 gateway can use
local LLMs without direct network access to the laptop.

Ollama HTTP API docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("chathan.executor.ollama")

# Ollama's local HTTP endpoint.
_OLLAMA_BASE_URL = "http://localhost:11434"

# Default timeout for inference (local models can be slow on consumer GPUs).
_OLLAMA_TIMEOUT = 300


async def ollama_chat(params: dict[str, Any]) -> dict[str, Any]:
    """
    Send a chat completion request to the local Ollama server.

    Expected params:
        messages    (str)  JSON-encoded list of {role, content} dicts.
        model       (str)  Ollama model name, e.g. "qwen2.5-coder:7b".
        system      (str)  Optional system prompt.
        tools       (str)  Optional JSON-encoded tool definitions.
        max_tokens  (int)  Max output tokens (default 4096).
        temperature (float) Sampling temperature (default 0.0).

    Returns:
        returncode  (int)  0 on success, 1 on error.
        stdout      (str)  JSON-encoded response with text, tool_calls, etc.
        stderr      (str)  Error message on failure.
    """
    import httpx

    # Parse messages from JSON string.
    try:
        messages_raw = params.get("messages", "[]")
        messages = json.loads(messages_raw) if isinstance(messages_raw, str) else messages_raw
    except (json.JSONDecodeError, TypeError) as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"Invalid messages JSON: {exc}"}

    model = params.get("model", "qwen2.5-coder:7b")
    system = params.get("system", "")
    max_tokens = int(params.get("max_tokens", 4096))
    temperature = float(params.get("temperature", 0.0))

    # Parse optional tools.
    tools = None
    tools_raw = params.get("tools")
    if tools_raw:
        try:
            tools = json.loads(tools_raw) if isinstance(tools_raw, str) else tools_raw
        except (json.JSONDecodeError, TypeError):
            pass  # Ignore invalid tools, proceed without.

    # Build the Ollama request body.
    # Inject system prompt as the first message if provided.
    ollama_messages = []
    if system:
        ollama_messages.append({"role": "system", "content": system})
    ollama_messages.extend(messages)

    body: dict[str, Any] = {
        "model": model,
        "messages": ollama_messages,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }

    # Add tools if provided (Ollama supports OpenAI-compatible tool calling).
    if tools:
        ollama_tools = _convert_tools(tools)
        if ollama_tools:
            body["tools"] = ollama_tools

    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
            resp = await client.post(f"{_OLLAMA_BASE_URL}/api/chat", json=body)

            if resp.status_code != 200:
                error_text = resp.text[:2000]
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"Ollama returned HTTP {resp.status_code}: {error_text}",
                }

            data = resp.json()

    except httpx.ConnectError:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": "Ollama is not running. Start it with: ollama serve",
        }
    except httpx.TimeoutException:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"Ollama timed out after {_OLLAMA_TIMEOUT}s.",
        }
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"Ollama error: {exc}"}

    # Parse the Ollama response into our normalized format.
    result = _parse_response(data, model)
    return {
        "returncode": 0,
        "stdout": json.dumps(result),
        "stderr": "",
    }


def _convert_tools(tools: list[dict]) -> list[dict]:
    """
    Convert our internal tool format to Ollama's tool format.

    Ollama uses the OpenAI-compatible format:
    { "type": "function", "function": { "name": ..., "description": ..., "parameters": ... } }
    """
    converted = []
    for tool in tools:
        if "name" in tool:
            # Our format: { name, description, input_schema }
            converted.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        elif "type" in tool and tool["type"] == "function":
            # Already in OpenAI format.
            converted.append(tool)
    return converted


def _parse_response(data: dict, model: str) -> dict:
    """Parse Ollama API response into our normalized provider response."""
    message = data.get("message", {})
    text = message.get("content", "")

    # Parse tool calls if present.
    tool_calls = []
    for tc in message.get("tool_calls", []):
        func = tc.get("function", {})
        tool_calls.append({
            "id": f"ollama_{len(tool_calls)}",
            "name": func.get("name", ""),
            "input": func.get("arguments", {}),
        })

    # Token counts.
    prompt_tokens = data.get("prompt_eval_count", 0)
    completion_tokens = data.get("eval_count", 0)

    return {
        "text": text,
        "tool_calls": tool_calls,
        "stop_reason": "tool_use" if tool_calls else "end_turn",
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "model": model,
        "provider_name": "ollama",
    }
