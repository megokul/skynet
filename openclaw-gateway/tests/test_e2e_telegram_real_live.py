"""Fully real Telegram-network E2E (user account -> bot chat -> inline buttons)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Callable

import pytest

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency at runtime
    load_dotenv = None  # type: ignore[assignment]

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except Exception:  # pragma: no cover - optional dependency at runtime
    TelegramClient = None  # type: ignore[assignment]
    StringSession = None  # type: ignore[assignment]


def _load_live_env_from_dotenv() -> None:
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parents[2]
    candidates = []
    explicit = os.environ.get("SKYNET_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([repo_root / ".env", repo_root / "openclaw-gateway" / ".env"])
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
        except Exception:
            key = str(candidate).lower()
        if key in seen or not candidate.exists():
            continue
        seen.add(key)
        load_dotenv(candidate, override=False)


_load_live_env_from_dotenv()


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise AssertionError(f"Missing required env: {name}")
    return value


def _button_texts(message) -> list[str]:
    rows = getattr(message, "buttons", None) or []
    out: list[str] = []
    for row in rows:
        for btn in row:
            text = str(getattr(btn, "text", "")).strip()
            if text:
                out.append(text)
    return out


async def _wait_for_bot_message(
    client,
    bot_entity,
    after_id: int,
    *,
    timeout_s: int,
    predicate: Callable[[str, list[str]], bool],
) -> object:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msgs = await client.get_messages(bot_entity, limit=20)
        for msg in reversed(msgs):
            if int(getattr(msg, "id", 0)) <= after_id:
                continue
            if bool(getattr(msg, "out", False)):
                continue
            text = str(getattr(msg, "message", "") or "")
            btns = _button_texts(msg)
            if predicate(text, btns):
                return msg
        await asyncio.sleep(1.0)
    raise AssertionError("Timed out waiting for expected bot message.")


async def _click_button_contains(message, needle: str) -> str:
    rows = getattr(message, "buttons", None) or []
    for i, row in enumerate(rows):
        for j, btn in enumerate(row):
            text = str(getattr(btn, "text", "")).strip()
            if needle.lower() in text.lower():
                await message.click(i, j)
                return text
    raise AssertionError(f"Could not find button containing '{needle}'.")


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_telegram_chat_flow_no_github_repo_creation() -> None:
    if os.environ.get("SKYNET_E2E_LIVE") != "1":
        pytest.skip("Set SKYNET_E2E_LIVE=1 to run live Telegram E2E.")
    if TelegramClient is None or StringSession is None:
        pytest.skip("Telethon is not installed. Install with: pip install telethon")

    api_id = int(_require_env("SKYNET_E2E_TELEGRAM_API_ID"))
    api_hash = _require_env("SKYNET_E2E_TELEGRAM_API_HASH")
    session = _require_env("SKYNET_E2E_TELEGRAM_SESSION")
    bot_username = _require_env("SKYNET_E2E_TELEGRAM_BOT_USERNAME")

    project_slug = f"livee2e{int(time.time())}"
    requirement = (
        "python script windows terminal execution popup hi short beep sound; "
        "standard library only; create tests and run contract."
    )

    async with TelegramClient(StringSession(session), api_id, api_hash) as client:
        bot = await client.get_entity(bot_username)
        history = await client.get_messages(bot, limit=1)
        last_id = int(history[0].id) if history else 0

        await client.send_message(bot, "hi")
        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            predicate=lambda text, btns: any("start a project" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Start a Project")

        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            predicate=lambda text, _btns: "what should we call this project" in text.lower(),
        )
        last_id = int(msg.id)
        await client.send_message(bot, project_slug)

        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            predicate=lambda _text, btns: any("python app" in b.lower() for b in btns) or any("other" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        if any("python app" in b.lower() for b in _button_texts(msg)):
            await _click_button_contains(msg, "Python App")
        else:
            await _click_button_contains(msg, "Other")

        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            predicate=lambda text, _btns: "what are you building" in text.lower(),
        )
        last_id = int(msg.id)
        await client.send_message(bot, requirement)

        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=120,
            predicate=lambda _text, btns: any("done" in b.lower() and "generate plan" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Generate Plan")

        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=300,
            predicate=lambda _text, btns: any("approve" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Approve")

        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=180,
            predicate=lambda _text, btns: any("start coding" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Start Coding")

        msg = await _wait_for_bot_message(
            client,
            bot,
            last_id,
            timeout_s=180,
            predicate=lambda _text, btns: any("skip" in b.lower() and "start coding" in b.lower() for b in btns),
        )
        last_id = int(msg.id)
        await _click_button_contains(msg, "Skip")

        saw_run_button = False
        saw_finish_summary = False
        saw_no_github_push = True

        for _ in range(80):
            msg = await _wait_for_bot_message(
                client,
                bot,
                last_id,
                timeout_s=90,
                predicate=lambda text, btns: bool(text.strip()) or bool(btns),
            )
            last_id = int(msg.id)
            text = str(getattr(msg, "message", "") or "")
            btns = _button_texts(msg)

            if "github repo created and pushed" in text.lower():
                saw_no_github_push = False
                break

            if any("run it" in b.lower() for b in btns):
                await _click_button_contains(msg, "Run It")
                continue

            if "session finished" in text.lower() or "complete=" in text.lower():
                saw_finish_summary = True

            if any("run project" in b.lower() for b in btns):
                saw_run_button = True
                await _click_button_contains(msg, "Run Project")
                run_msg = await _wait_for_bot_message(
                    client,
                    bot,
                    last_id,
                    timeout_s=240,
                    predicate=lambda t, _b: "exit" in t.lower() or "finished" in t.lower(),
                )
                last_id = int(run_msg.id)
                break

        assert saw_no_github_push, "Live Telegram E2E unexpectedly created/pushed a GitHub repo."
        assert saw_finish_summary or saw_run_button, "Live Telegram E2E did not reach coding completion/run phase."

