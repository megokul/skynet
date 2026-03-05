from __future__ import annotations

from unittest.mock import patch

import pytest

from orchestration.openclaw_runner import OpenClawRunner


def test_available_stages_filters_missing_binaries() -> None:
    runner = OpenClawRunner()
    with patch.object(runner, "_resolve_binary", return_value=""):
        available, reasons = runner.available_stages(["codex", "claude_ollama", "cline"])
    assert available == []
    assert "codex" in reasons
    assert "claude_ollama" in reasons
    assert "cline" in reasons


@pytest.mark.asyncio
async def test_run_prompt_returns_setup_error_when_binary_missing() -> None:
    runner = OpenClawRunner()
    session = await runner.start_session(
        phase="coding",
        project_id="proj-1",
        task_id=1,
        stage="codex",
        runtime="acp",
        queue_mode="require_empty_queue",
    )
    with patch.object(runner, "_resolve_binary", return_value=""):
        result = await runner.run_prompt(
            session_id=session["session_id"],
            prompt="print hello",
            timeout_seconds=60,
            stage="codex",
        )
    assert int(result.get("returncode", 0)) != 0
    assert "OPENCLAW_SETUP_ERROR" in str(result.get("stderr") or "")

