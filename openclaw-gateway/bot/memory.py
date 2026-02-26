"""
bot/memory.py -- User profile, memory store/load, no-store commands.
"""
from __future__ import annotations

import html
import logging
import re
from enum import IntEnum

from telegram import Update

from core.prompt_library import load_prompt, render_prompt

from . import state

logger = logging.getLogger("skynet.telegram")


# ---------------------------------------------------------------------------
# Session gap tiers
# ---------------------------------------------------------------------------

class GapTier(IntEnum):
    """How long since the user last spoke — controls history depth and LLM briefing."""
    ACTIVE   = 0  # < 10 min   — same conversation, full context
    SHORT    = 1  # 10–60 min  — short break, most context
    MEDIUM   = 2  # 1–4 hours  — returning, reduced context
    LONG     = 3  # 4–24 hours — new session, no old messages
    DAY      = 4  # 1–7 days   — next day, warm re-entry
    EXTENDED = 5  # > 7 days   — long absence, clean slate


# seconds → (upper_bound, tier)
_GAP_THRESHOLDS: list[tuple[int, GapTier]] = [
    (600,    GapTier.ACTIVE),
    (3600,   GapTier.SHORT),
    (14400,  GapTier.MEDIUM),
    (86400,  GapTier.LONG),
    (604800, GapTier.DAY),
]

# How many conversation messages to inject per tier
_GAP_MESSAGE_LIMIT: dict[GapTier, int] = {
    GapTier.ACTIVE:   24,
    GapTier.SHORT:    16,
    GapTier.MEDIUM:    8,
    GapTier.LONG:      0,
    GapTier.DAY:       0,
    GapTier.EXTENDED:  0,
}

# Number of new messages since the last summary that triggers a new summarization pass
_SUMMARIZE_THRESHOLD: int = 12

# Number of raw (verbatim) messages to keep at the tail regardless of summary coverage
_SUMMARY_RAW_TAIL: int = 6


def _compute_gap_tier(gap_seconds: float | None) -> GapTier:
    if gap_seconds is None:
        return GapTier.EXTENDED
    for upper, tier in _GAP_THRESHOLDS:
        if gap_seconds < upper:
            return tier
    return GapTier.EXTENDED


async def compute_session_gap(update: Update) -> GapTier:
    """
    Query the DB for the user's last message time and return the session GapTier.
    Falls back to EXTENDED (clean slate) if DB is unavailable.
    """
    if update is None or state._project_manager is None:
        return GapTier.EXTENDED
    try:
        user_row = await _ensure_memory_user(update)
        if not user_row:
            return GapTier.EXTENDED
        from db import store
        from datetime import datetime, timezone
        last_ts = await store.get_last_user_message_time(
            state._project_manager.db,
            user_id=int(user_row["id"]),
        )
        if not last_ts:
            return GapTier.EXTENDED
        dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        gap_secs = (datetime.now(timezone.utc) - dt).total_seconds()
        return _compute_gap_tier(gap_secs)
    except Exception:
        logger.debug("Gap computation failed; defaulting to EXTENDED.", exc_info=True)
        return GapTier.EXTENDED


async def _ensure_memory_user(update: Update) -> dict | None:
    user = update.effective_user
    if user is None or state._project_manager is None:
        return None
    try:
        from db import store

        return await store.ensure_user(
            state._project_manager.db,
            telegram_user_id=int(user.id),
            username=user.username or "",
            first_name=user.first_name or "",
            last_name=user.last_name or "",
        )
    except Exception:
        logger.exception("Failed to ensure user profile record.")
        return None


async def _count_messages_since(
    db,
    user_id: int,
    after_id: int,
) -> int:
    """Return how many user_conversations rows exist for user with id > after_id."""
    try:
        async with db.execute(
            "SELECT COUNT(*) FROM user_conversations WHERE user_id = ? AND id > ?",
            (int(user_id), int(after_id)),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _build_summarization_prompt(messages: list[dict]) -> str:
    """Build a prompt asking the LLM to compress the conversation into a brief summary."""
    transcript = "\n".join(
        f"{m['role'].upper()}: {m['content'][:500]}"
        for m in messages
    )
    return render_prompt(
        "bot/memory/summarize_user.md",
        transcript=transcript,
    )


async def _summarize_and_store(
    db,
    user_id: int,
    up_to_message_id: int,
) -> None:
    """
    Background task: load messages up to up_to_message_id, ask the LLM to compress
    them, and store the result in conversation_summaries.
    """
    try:
        from db import store

        rows = await store.list_user_conversations(db, user_id=user_id, limit=200)
        rows = [r for r in rows if r["id"] <= up_to_message_id]
        if len(rows) < 4:
            return

        prompt = _build_summarization_prompt(rows)
        if state._provider_router is None:
            return
        response = await state._provider_router.chat(
            [{"role": "user", "content": prompt}],
            system=load_prompt("bot/memory/summarize_system.md"),
            max_tokens=300,
            task_type="general",
            allowed_providers=["groq", "gemini", "claude"],
        )
        summary_text = (response.text or "").strip()
        if not summary_text:
            return

        await store.add_conversation_summary(
            db,
            user_id=user_id,
            summary=summary_text,
            covered_up_to_id=up_to_message_id,
            message_count=len(rows),
        )
        logger.debug(
            "Conversation summarized for user %s up to message id %s (%d msgs)",
            user_id, up_to_message_id, len(rows),
        )
    except Exception:
        logger.exception("Failed to summarize conversation for user %s", user_id)


async def _append_user_conversation(
    update: Update,
    *,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> None:
    if state._project_manager is None:
        return
    user_row = await _ensure_memory_user(update)
    if not user_row:
        return
    try:
        from db import store

        msg = update.message
        new_id = await store.add_user_conversation(
            state._project_manager.db,
            user_id=int(user_row["id"]),
            role=role,
            content=content,
            chat_id=str(getattr(msg, "chat_id", "")),
            telegram_message_id=str(getattr(msg, "message_id", "")),
            metadata=metadata or {},
        )

        # After a full exchange (assistant turn), check if summarization threshold is crossed.
        if role == "assistant":
            try:
                db = state._project_manager.db
                user_id = int(user_row["id"])
                latest = await store.get_latest_conversation_summary(db, user_id=user_id)
                after_id = int(latest["covered_up_to_id"]) if latest else 0
                count = await _count_messages_since(db, user_id, after_id)
                if count >= _SUMMARIZE_THRESHOLD:
                    from bot.helpers import _spawn_background_task
                    _spawn_background_task(
                        _summarize_and_store(db, user_id, up_to_message_id=new_id),
                        tag="conversation-summarizer",
                    )
            except Exception:
                logger.debug("Summarization trigger check failed.", exc_info=True)
    except Exception:
        logger.exception("Failed to write user conversation record.")


async def _load_recent_conversation_messages(
    update: Update | None,
    *,
    gap_tier: GapTier = GapTier.EXTENDED,
) -> list[dict]:
    """
    Load conversation context using three-tier memory:

    Tier 1 — Working memory: last _SUMMARY_RAW_TAIL raw messages (verbatim).
    Tier 2 — Rolling summary: LLM-compressed paragraph injected before the raw tail
              if a summary exists and covers earlier messages.
    Tier 3 — Project state: injected separately via _build_project_context_block
              (not handled here).

    The number of raw messages loaded is controlled by gap_tier. For LONG+ gaps,
    returns empty (gap briefing handles session context instead).
    Falls back to process-local history when DB lookup is unavailable.
    """
    max_items = _GAP_MESSAGE_LIMIT.get(gap_tier, 0)

    # For zero-message tiers, skip the DB entirely.
    if max_items == 0:
        return []

    if (
        update is None
        or state._project_manager is None
        or not hasattr(state._project_manager, "db")
    ):
        return state._chat_history[-max_items:]

    try:
        user_row = await _ensure_memory_user(update)
        if not user_row:
            return state._chat_history[-max_items:]
        from db import store

        db = state._project_manager.db
        user_id = int(user_row["id"])

        # Load the latest rolling summary (if any).
        latest_summary = await store.get_latest_conversation_summary(db, user_id=user_id)

        # Determine how many raw messages to keep verbatim.
        keep_raw = min(max_items, _SUMMARY_RAW_TAIL)

        # Load raw tail: only messages AFTER the summary's coverage (if a summary exists),
        # otherwise load the most recent max_items within the tier time window.
        tier_seconds = {
            GapTier.ACTIVE:  600,
            GapTier.SHORT:   3600,
            GapTier.MEDIUM:  14400,
        }.get(gap_tier)

        if latest_summary:
            raw_rows = await store.list_user_conversations(
                db,
                user_id=user_id,
                limit=keep_raw,
                after_id=int(latest_summary["covered_up_to_id"]),
            )
        else:
            raw_rows = await store.list_user_conversations(
                db,
                user_id=user_id,
                limit=max_items,
                since_seconds=tier_seconds,
            )

    except Exception:
        logger.exception("Failed to load persistent conversation history.")
        return state._chat_history[-max_items:]

    # Build raw message list.
    raw_messages: list[dict] = []
    for row in raw_rows:
        role = str(row.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        raw_messages.append({"role": role, "content": content[:4000]})

    # If nothing came back, fall through to in-memory history.
    if not raw_messages and not latest_summary:
        return state._chat_history[-max_items:]

    # Prepend the rolling summary as a synthetic exchange if it exists.
    messages: list[dict] = []
    if latest_summary and raw_messages:
        messages.append({
            "role": "user",
            "content": f"[Conversation summary — earlier in this session]\n{latest_summary['summary']}",
        })
        messages.append({
            "role": "assistant",
            "content": "Understood, I have the context from earlier.",
        })

    messages.extend(raw_messages)
    return messages


async def _profile_prompt_context(update: Update) -> str:
    if state._project_manager is None:
        return ""
    user_row = await _ensure_memory_user(update)
    if not user_row:
        return ""
    try:
        from db import store

        user_id = int(user_row["id"])
        facts = await store.list_profile_facts(state._project_manager.db, user_id=user_id, active_only=True)
        prefs = await store.get_user_preferences(state._project_manager.db, user_id=user_id)
        chunks: list[str] = []
        if user_row.get("timezone"):
            chunks.append(f"timezone={user_row['timezone']}")
        if user_row.get("region"):
            chunks.append(f"region={user_row['region']}")
        for pref in prefs[:12]:
            chunks.append(f"pref:{pref['pref_key']}={pref['pref_value']}")
        for fact in facts[:16]:
            chunks.append(f"fact:{fact['fact_key']}={fact['fact_value']}")
        return "\n".join(chunks)
    except Exception:
        logger.exception("Failed to build profile prompt context.")
        return ""


def _extract_memory_candidates(text: str) -> tuple[list[tuple[str, str, float]], list[tuple[str, str]]]:
    lowered = text.lower()
    facts: list[tuple[str, str, float]] = []
    prefs: list[tuple[str, str]] = []

    name_match = re.search(r"\bmy name is\s+([A-Za-z][A-Za-z0-9 _-]{1,40})\b", text, re.IGNORECASE)
    if name_match:
        facts.append(("name", name_match.group(1).strip(), 0.95))

    call_me = re.search(r"\bcall me\s+([A-Za-z][A-Za-z0-9 _-]{1,40})\b", text, re.IGNORECASE)
    if call_me:
        facts.append(("preferred_name", call_me.group(1).strip(), 0.9))

    tz_match = re.search(r"\b(?:timezone is|tz is|i am in timezone)\s+([A-Za-z0-9_/\-+:.]{2,32})\b", text, re.IGNORECASE)
    if tz_match:
        facts.append(("timezone", tz_match.group(1).strip(), 0.9))
    else:
        utc_match = re.search(r"\butc\s*([+-]\d{1,2}(?::\d{2})?)\b", lowered)
        if utc_match:
            facts.append(("timezone", f"UTC{utc_match.group(1)}", 0.85))

    region_match = re.search(r"\b(?:i live in|i am in|i'm in|based in)\s+([A-Za-z0-9 ,._-]{2,60})\b", text, re.IGNORECASE)
    if region_match:
        facts.append(("region", region_match.group(1).strip(" .,"), 0.75))

    if "no emoji" in lowered or "no emojis" in lowered:
        prefs.append(("tone.no_emojis", "true"))
    if "be concise" in lowered or "short answers" in lowered:
        prefs.append(("response.verbosity", "concise"))
    if "be detailed" in lowered or "more detail" in lowered:
        prefs.append(("response.verbosity", "detailed"))
    if "no fluff" in lowered:
        prefs.append(("tone.no_fluff", "true"))

    for token, key in (
        ("ec2", "environment.ec2"),
        ("docker", "environment.docker"),
        ("windows", "environment.windows"),
        ("linux", "environment.linux"),
    ):
        if token in lowered:
            facts.append((key, "true", 0.6))

    return facts, prefs


def _is_no_store_once_message(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in state._NO_STORE_ONCE_MARKERS)


def _is_no_store_chat_message(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in state._NO_STORE_CHAT_MARKERS)


async def _capture_profile_memory(
    update: Update,
    text: str,
    *,
    skip_store: bool,
    persist_message: bool = True,
) -> None:
    if state._project_manager is None:
        return
    user_row = await _ensure_memory_user(update)
    if not user_row:
        return

    try:
        from db import store

        user_id = int(user_row["id"])
        if persist_message:
            await _append_user_conversation(update, role="user", content=text)

        if skip_store:
            await store.add_memory_audit_log(
                state._project_manager.db,
                user_id=user_id,
                action="skip_store_once",
                target_type="message",
                target_key="user_text",
                detail="User requested no-store for this message.",
            )
            return

        if int(user_row.get("memory_enabled", 1)) != 1:
            return

        facts, prefs = _extract_memory_candidates(text)
        for key, value, confidence in facts:
            await store.add_or_update_profile_fact(
                state._project_manager.db,
                user_id=user_id,
                fact_key=key,
                fact_value=value,
                confidence=confidence,
                source="telegram_text",
            )
            await store.add_memory_audit_log(
                state._project_manager.db,
                user_id=user_id,
                action="fact_upsert",
                target_type="fact",
                target_key=key,
                detail=f"{key}={value}",
            )
            if key == "timezone":
                await store.update_user_core_fields(state._project_manager.db, user_id=user_id, timezone=value)
            if key == "region":
                await store.update_user_core_fields(state._project_manager.db, user_id=user_id, region=value)

        for pref_key, pref_value in prefs:
            await store.upsert_user_preference(
                state._project_manager.db,
                user_id=user_id,
                pref_key=pref_key,
                pref_value=pref_value,
                source="telegram_text",
            )
            await store.add_memory_audit_log(
                state._project_manager.db,
                user_id=user_id,
                action="preference_upsert",
                target_type="preference",
                target_key=pref_key,
                detail=f"{pref_key}={pref_value}",
            )
    except Exception:
        logger.exception("Failed memory capture pipeline.")


async def _format_profile_summary(update: Update) -> str:
    if state._project_manager is None:
        return "User profile is unavailable."
    user_row = await _ensure_memory_user(update)
    if not user_row:
        return "User profile is unavailable."

    from db import store

    user_id = int(user_row["id"])
    facts = await store.list_profile_facts(state._project_manager.db, user_id=user_id, active_only=True)
    prefs = await store.get_user_preferences(state._project_manager.db, user_id=user_id)

    lines = [
        "<b>User Profile</b>",
        f"Memory enabled: <b>{'yes' if int(user_row.get('memory_enabled', 1)) == 1 else 'no'}</b>",
    ]
    if user_row.get("timezone"):
        lines.append(f"Timezone: <code>{html.escape(str(user_row['timezone']))}</code>")
    if user_row.get("region"):
        lines.append(f"Region: <code>{html.escape(str(user_row['region']))}</code>")

    lines.append("")
    lines.append("<b>Facts</b>")
    if facts:
        for fact in facts[:20]:
            lines.append(
                f"- <code>{html.escape(str(fact['fact_key']))}</code>: "
                f"{html.escape(str(fact['fact_value']))} "
                f"(conf={float(fact.get('confidence', 0.0)):.2f})"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("<b>Preferences</b>")
    if prefs:
        for pref in prefs:
            lines.append(
                f"- <code>{html.escape(str(pref['pref_key']))}</code>: "
                f"{html.escape(str(pref['pref_value']))}"
            )
    else:
        lines.append("- none")

    return "\n".join(lines)


async def _forget_profile_target(update: Update, target: str) -> str:
    if state._project_manager is None:
        return "Profile store is not available."
    user_row = await _ensure_memory_user(update)
    if not user_row:
        return "Profile store is not available."

    from db import store

    user_id = int(user_row["id"])
    removed = await store.forget_profile_facts(
        state._project_manager.db,
        user_id=user_id,
        key_or_text=target,
    )
    await store.add_memory_audit_log(
        state._project_manager.db,
        user_id=user_id,
        action="forget",
        target_type="fact",
        target_key=target,
        detail=f"Removed facts: {removed}",
    )
    if removed <= 0:
        return f"No stored facts matched '{target}'."
    return f"Forgot {removed} fact(s) matching '{target}'."

async def _set_memory_enabled_for_user(update: Update, enabled: bool, *, reason: str) -> str:
    if state._project_manager is None:
        return "Profile store is not available."
    user_row = await _ensure_memory_user(update)
    if not user_row:
        return "Profile store is not available."

    from db import store

    user_id = int(user_row["id"])
    await store.set_user_memory_enabled(state._project_manager.db, user_id=user_id, enabled=enabled)
    await store.add_memory_audit_log(
        state._project_manager.db,
        user_id=user_id,
        action="memory_enabled" if enabled else "memory_disabled",
        target_type="policy",
        target_key="memory_enabled",
        detail=reason,
    )
    if enabled:
        return "Memory capture enabled."
    return "Memory capture disabled for this user. Use /store_on to re-enable."

async def _maybe_handle_memory_text_command(update: Update, text: str) -> bool:
    lowered = text.strip().lower()

    if lowered in {"show my profile", "show profile", "what do you know about me"}:
        summary = await _format_profile_summary(update)
        await update.message.reply_text(summary, parse_mode="HTML")
        return True

    if lowered.startswith("forget "):
        target = text.strip()[7:].strip()
        if target:
            await update.message.reply_text(await _forget_profile_target(update, target))
            return True

    if _is_no_store_chat_message(text):
        await update.message.reply_text(
            await _set_memory_enabled_for_user(
                update,
                enabled=False,
                reason="Disabled by natural-language request.",
            )
        )
        return True

    return False


# ------------------------------------------------------------------
