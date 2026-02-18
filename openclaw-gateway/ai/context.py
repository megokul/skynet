"""
SKYNET — Conversation Context Manager

Manages per-project AI conversation history.  Keeps recent messages
in full and summarises older ones to stay within token budgets.

Key function: ``build_messages_for_provider()`` adapts the conversation
to fit a target provider's context window by summarising older turns.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

import aiosqlite

from db import store

logger = logging.getLogger("skynet.ai.context")

# Rough estimate: 1 token ~= 4 characters.
_CHARS_PER_TOKEN = 4
MAX_CONTEXT_TOKENS = 120_000
KEEP_RECENT_MESSAGES = 30

# When context exceeds this fraction of the provider's limit, summarise.
_SUMMARISE_THRESHOLD = 0.70

# Number of recent messages to always keep in full (never summarised).
_KEEP_RECENT_FULL = 12

# System prompt used for generating conversation summaries.
_SUMMARISE_PROMPT = (
    "You are a concise summariser. Condense the following conversation "
    "into a short summary (max 500 words). Focus on:\n"
    "- What code was written or changed (files, functions)\n"
    "- Decisions made and why\n"
    "- Current state of the project\n"
    "- Any errors encountered and how they were resolved\n\n"
    "Do NOT include tool call details or raw code. "
    "Return ONLY the summary text, nothing else."
)


# Type alias for the summariser callback.
SummariseFn = Callable[[list[dict[str, Any]], str], Awaitable[str]]


def _estimate_tokens(content: Any) -> int:
    """Rough token count from message content."""
    if isinstance(content, str):
        return len(content) // _CHARS_PER_TOKEN
    return len(json.dumps(content, default=str)) // _CHARS_PER_TOKEN


def _messages_token_count(messages: list[dict[str, Any]]) -> int:
    """Total estimated tokens across all messages."""
    return sum(_estimate_tokens(m.get("content", "")) for m in messages)


async def build_messages(
    db: aiosqlite.Connection,
    project_id: str,
    phase: str = "coding",
) -> list[dict[str, Any]]:
    """
    Load recent conversation history for the project.

    Returns messages in chronological order, trimmed to fit within
    the token budget.
    """
    rows = await store.get_conversation(
        db, project_id, phase=phase, limit=KEEP_RECENT_MESSAGES,
    )

    messages = []
    total_tokens = 0
    for row in rows:
        content = row["content"]
        tokens = row.get("token_count", 0) or _estimate_tokens(content)
        total_tokens += tokens

        if total_tokens > MAX_CONTEXT_TOKENS:
            logger.info(
                "Context budget exceeded (%d tokens), truncating older messages",
                total_tokens,
            )
            break

        messages.append({
            "role": row["role"],
            "content": content,
        })

    return messages


async def build_messages_for_provider(
    db: aiosqlite.Connection,
    project_id: str,
    context_limit: int,
    summarise_fn: SummariseFn | None = None,
    phase: str = "coding",
) -> list[dict[str, Any]]:
    """
    Load conversation history, summarising older messages if needed
    to fit within the target provider's context window.

    Args:
        db: Database connection.
        project_id: Project ID.
        context_limit: Target provider's max context tokens.
        summarise_fn: Async callback(messages, prompt) -> summary text.
                      If None, falls back to simple truncation.
        phase: Conversation phase.

    Returns:
        Messages list that fits within context_limit.
    """
    # Load all recent messages from DB.
    rows = await store.get_conversation(
        db, project_id, phase=phase, limit=200,
    )

    all_messages = []
    for row in rows:
        content = row["content"]
        all_messages.append({
            "role": row["role"],
            "content": content,
        })

    if not all_messages:
        return []

    total_tokens = _messages_token_count(all_messages)
    budget = int(context_limit * _SUMMARISE_THRESHOLD)

    # If within budget, return all messages as-is.
    if total_tokens <= budget:
        return all_messages

    logger.info(
        "Context (%d tokens) exceeds budget (%d tokens for %d limit), summarising",
        total_tokens, budget, context_limit,
    )

    # Split: keep recent messages in full, summarise older ones.
    keep_count = min(_KEEP_RECENT_FULL, len(all_messages))
    recent = all_messages[-keep_count:]
    older = all_messages[:-keep_count]

    # Check if just the recent messages fit. If not, truncate recent too.
    recent_tokens = _messages_token_count(recent)
    if recent_tokens > budget:
        # Even recent messages are too large — hard truncate.
        truncated = []
        running = 0
        for msg in reversed(recent):
            msg_tokens = _estimate_tokens(msg.get("content", ""))
            if running + msg_tokens > budget:
                break
            truncated.insert(0, msg)
            running += msg_tokens
        return truncated

    if not older:
        return recent

    # Summarise the older messages.
    summary_text = await _summarise_messages(older, summarise_fn)

    if summary_text:
        summary_msg = {
            "role": "user",
            "content": (
                f"[CONVERSATION SUMMARY — earlier messages condensed]\n\n"
                f"{summary_text}\n\n"
                f"[END SUMMARY — the conversation continues below]"
            ),
        }
        return [summary_msg] + recent

    # If summarisation failed, fall back to just recent messages.
    return recent


async def _summarise_messages(
    messages: list[dict[str, Any]],
    summarise_fn: SummariseFn | None,
) -> str:
    """
    Generate a summary of conversation messages.

    Uses the provided callback, or falls back to a simple
    extractive summary if no callback is available.
    """
    if summarise_fn:
        try:
            # Build a simplified version of the messages for the summariser.
            simplified = _simplify_for_summary(messages)
            return await summarise_fn(simplified, _SUMMARISE_PROMPT)
        except Exception:
            logger.exception("AI summarisation failed, using extractive fallback")

    # Extractive fallback: pick key text from messages.
    return _extractive_summary(messages)


def _simplify_for_summary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Reduce messages to essential text for summarisation.
    Strips tool call details, truncates long outputs.
    """
    simplified = []
    for msg in messages:
        content = msg.get("content", "")
        role = msg["role"]

        if isinstance(content, list):
            # Multi-part content — extract text pieces.
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif item.get("type") == "tool_result":
                        result_text = item.get("content", "")
                        if isinstance(result_text, str) and len(result_text) > 200:
                            result_text = result_text[:200] + "..."
                        parts.append(f"[Tool {item.get('name', '?')} result: {result_text}]")
                    elif item.get("type") == "tool_use":
                        parts.append(f"[Called tool: {item.get('name', '?')}]")
                else:
                    parts.append(str(item))
            text = "\n".join(parts)
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)

        # Cap each message at 500 chars for the summariser.
        if len(text) > 500:
            text = text[:500] + "..."

        simplified.append({"role": role, "content": text})

    return simplified


def _extractive_summary(messages: list[dict[str, Any]], max_chars: int = 2000) -> str:
    """
    Simple extractive summary: grab assistant text snippets.
    Used as fallback when AI summarisation is unavailable.
    """
    parts = []
    total = 0
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            text = " ".join(
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        elif isinstance(content, str):
            text = content
        else:
            continue

        text = text.strip()
        if not text:
            continue

        # Take first 200 chars of each assistant message.
        snippet = text[:200]
        parts.append(f"- {snippet}")
        total += len(snippet)
        if total >= max_chars:
            break

    return "\n".join(parts) if parts else ""


async def save_messages(
    db: aiosqlite.Connection,
    project_id: str,
    messages: list[dict[str, Any]],
    phase: str = "coding",
) -> None:
    """
    Persist new messages to the database.

    Only saves messages that are not already stored (based on count
    comparison).  Call this after each AI conversation turn.
    """
    existing = await store.get_conversation(db, project_id, phase=phase, limit=1000)
    existing_count = len(existing)

    for msg in messages[existing_count:]:
        content = msg.get("content", "")
        tokens = _estimate_tokens(content)
        await store.add_conversation_message(
            db,
            project_id=project_id,
            role=msg["role"],
            content=content,
            token_count=tokens,
            phase=phase,
        )
