"""Regression coverage for coding preflight behavior used in chat flows."""

from unittest.mock import AsyncMock, patch

import pytest

from bot.handlers.coding import _preflight_coding_environment
from db.schema import init_db
from db.store import create_project, ensure_user


def _worker_ok(returncode: int = 0, stdout: str = "", stderr: str = "") -> dict:
    return {
        "status": "success",
        "result": {
            "returncode": int(returncode),
            "stdout": stdout,
            "stderr": stderr,
        },
    }


@pytest.mark.asyncio
async def test_chat_flow_preflight_reports_missing_codex():
    db = await init_db(":memory:")
    try:
        user = await ensure_user(
            db,
            telegram_user_id=42,
            username="tester",
            first_name="Test",
            last_name="User",
        )
        project = await create_project(
            db,
            user_id=user["id"],
            name="joo",
            project_type="Other",
            description="popup + beep",
            coding_profile="codex_primary",
            quality_profile="strict",
            control_loop_profile="legacy",
        )

        async def fake_send_action(action, params, **_kwargs):
            if action == "check_coding_agents":
                return _worker_ok(
                    0,
                    (
                        "codex: unavailable (expected binary: codex)\n"
                        "claude: unavailable (expected binary: claude)\n"
                        "cline: unavailable (expected binary: cline)"
                    ),
                    "",
                )
            raise AssertionError(f"Unexpected action in simulation: {action}")

        with (
            patch("bot.handlers.coding.send_action", new=AsyncMock(side_effect=fake_send_action)),
            patch("bot.handlers.coding.cfg.CODING_FORCE_PRIMARY_FOR_ALL", False),
            patch("bot.handlers.coding.cfg.CONTROL_LOOP_FORCE_FOR_ALL", False),
        ):
            ok, error, chain = await _preflight_coding_environment(
                project=project,
                working_dir="E:/SKYNET-SANDBOX/Projects/joo",
            )

        assert ok is False
        assert chain == ["codex"]
        assert "no coding agents available for chain codex" in error.lower()
        assert "codex: unavailable" in error.lower()
    finally:
        await db.close()
