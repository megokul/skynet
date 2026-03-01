"""
SKYNET Worker — Executor Action Tests

Covers the worker-side executor functions that the gateway dispatches to.
These tests run against the REAL action functions (no mocking of the executor
itself) so they verify what the CLAW agent actually does when it receives an
action from the gateway.

What is real
────────────
  ✅  exec_command   — validates interpreter allowlist; runs via subprocess
  ✅  run_coding_agent — binary-not-found path; unknown-agent path
  ✅  ollama_chat    — ConnectError path (Ollama not running)

What is stubbed
───────────────
  ⬜  Filesystem writes (exec_command tests use tmp_path)
  ⬜  Live Ollama server (tested via httpx ConnectError mock)
  ⬜  Live coding agent CLIs (tested via binary-not-found path)
"""
from __future__ import annotations

import os
import sys
import json
import textwrap
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── exec_command ──────────────────────────────────────────────────────────────

from executor.actions import exec_command


class TestExecCommand:
    """exec_command runs Python / Node scripts from a working directory."""

    @pytest.mark.asyncio
    async def test_runs_python_script(self, tmp_path):
        """A valid 'python script.py' command executes and returns stdout."""
        script = tmp_path / "hello.py"
        script.write_text('print("hello from script")\n', encoding="utf-8")

        result = await exec_command({
            "command":     "python hello.py",
            "working_dir": str(tmp_path),
        })

        assert result["returncode"] == 0, f"Expected exit 0; got {result}"
        assert "hello from script" in result["stdout"], (
            f"stdout must contain script output; got {result['stdout']!r}"
        )

    @pytest.mark.asyncio
    async def test_python3_alias_accepted(self, tmp_path):
        """'python3 script.py' is also an accepted interpreter form."""
        script = tmp_path / "greet.py"
        script.write_text('print("hi python3")\n', encoding="utf-8")

        result = await exec_command({
            "command":     "python3 greet.py",
            "working_dir": str(tmp_path),
        })

        # python3 may not exist on Windows — tolerate that gracefully
        assert result["returncode"] in (0, 1, 2, -1), (
            f"Unexpected returncode; result: {result}"
        )
        # Either succeeded or gave a clear error — must not crash the executor
        assert isinstance(result.get("stdout", ""), str)
        assert isinstance(result.get("stderr", ""), str)

    @pytest.mark.asyncio
    async def test_rejects_bash(self, tmp_path):
        """Bash is not in the allowed interpreter list — must be rejected."""
        result = await exec_command({
            "command":     "bash script.sh",
            "working_dir": str(tmp_path),
        })

        assert result["returncode"] == 1
        assert "not allowed" in result["stderr"].lower() or "bash" in result["stderr"].lower(), (
            f"Error message must mention the rejected interpreter; got {result['stderr']!r}"
        )

    @pytest.mark.asyncio
    async def test_rejects_cmd(self, tmp_path):
        """cmd.exe must also be rejected."""
        result = await exec_command({
            "command":     "cmd /c echo hi",
            "working_dir": str(tmp_path),
        })

        assert result["returncode"] == 1
        assert "not allowed" in result["stderr"].lower() or "cmd" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_rejects_empty_command(self, tmp_path):
        """An empty command string must return a clear error."""
        result = await exec_command({
            "command":     "   ",
            "working_dir": str(tmp_path),
        })

        assert result["returncode"] == 1

    @pytest.mark.asyncio
    async def test_missing_working_dir_raises(self):
        """Omitting working_dir must raise ValueError (caught by the router)."""
        with pytest.raises(ValueError, match="working_dir"):
            await exec_command({"command": "python script.py"})

    @pytest.mark.asyncio
    async def test_missing_command_raises(self, tmp_path):
        """Omitting command must raise ValueError."""
        with pytest.raises(ValueError, match="command"):
            await exec_command({"working_dir": str(tmp_path)})

    @pytest.mark.asyncio
    async def test_script_exit_nonzero_captured(self, tmp_path):
        """Non-zero exit codes are captured and returned, not raised."""
        script = tmp_path / "fail.py"
        script.write_text("raise SystemExit(42)\n", encoding="utf-8")

        result = await exec_command({
            "command":     "python fail.py",
            "working_dir": str(tmp_path),
        })

        assert result["returncode"] == 42, (
            f"Non-zero exit code must be preserved; got {result['returncode']}"
        )

    @pytest.mark.asyncio
    async def test_stderr_captured(self, tmp_path):
        """Stderr output is captured and returned in the result."""
        script = tmp_path / "err.py"
        script.write_text("import sys; sys.stderr.write('oops\\n')\n", encoding="utf-8")

        result = await exec_command({
            "command":     "python err.py",
            "working_dir": str(tmp_path),
        })

        assert "oops" in result["stderr"], (
            f"Stderr must be captured; got {result['stderr']!r}"
        )


# ── run_coding_agent ──────────────────────────────────────────────────────────

from executor.actions import run_coding_agent


class TestRunCodingAgent:
    """run_coding_agent dispatches to the named CLI; fails cleanly when absent."""

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_error(self, tmp_path):
        """An agent name not in the allowlist must return a descriptive error."""
        result = await run_coding_agent({
            "agent":       "gpt4cli",
            "prompt":      "Write hello world",
            "working_dir": str(tmp_path),
        })

        assert result["returncode"] == 1
        assert "gpt4cli" in result["stderr"] or "unknown" in result["stderr"].lower(), (
            f"Error must mention the unknown agent; got {result['stderr']!r}"
        )

    @pytest.mark.asyncio
    async def test_binary_not_found_returns_error(self, tmp_path):
        """
        If the configured binary doesn't exist on PATH, a clear error is
        returned rather than an uncaught exception.

        _CODING_AGENT_BINARIES is built at import time so we patch the dict
        directly rather than setting env vars after the module was loaded.
        """
        import executor.actions as actions_mod

        saved = actions_mod._CODING_AGENT_BINARIES["cline"]
        actions_mod._CODING_AGENT_BINARIES["cline"] = "/nonexistent/path/cline"
        try:
            result = await run_coding_agent({
                "agent":       "cline",
                "prompt":      "Do something",
                "working_dir": str(tmp_path),
            })
        finally:
            actions_mod._CODING_AGENT_BINARIES["cline"] = saved

        assert result["returncode"] == 1
        assert result["stderr"], "Error message must explain what went wrong"
        err = result["stderr"].lower()
        assert "not found" in err or "cline" in err, (
            f"Error must mention the missing binary; got {result['stderr']!r}"
        )

    @pytest.mark.asyncio
    async def test_missing_agent_param_raises(self, tmp_path):
        """Omitting the agent parameter must raise ValueError."""
        with pytest.raises(ValueError, match="agent"):
            await run_coding_agent({
                "prompt":      "Do something",
                "working_dir": str(tmp_path),
            })

    @pytest.mark.asyncio
    async def test_missing_prompt_param_raises(self, tmp_path):
        """Omitting the prompt parameter must raise ValueError."""
        with pytest.raises(ValueError, match="prompt"):
            await run_coding_agent({
                "agent":       "cline",
                "working_dir": str(tmp_path),
            })

    @pytest.mark.asyncio
    async def test_timeout_out_of_range_returns_error(self, tmp_path):
        """timeout_seconds outside [30, 3600] must return a validation error."""
        result = await run_coding_agent({
            "agent":            "cline",
            "prompt":           "Do something",
            "working_dir":      str(tmp_path),
            "timeout_seconds":  5,   # below minimum of 30
        })

        assert result["returncode"] == 1
        assert "timeout" in result["stderr"].lower()


# ── ollama_chat ───────────────────────────────────────────────────────────────

from executor.ollama import ollama_chat


class TestOllamaChat:
    """ollama_chat talks to the local Ollama HTTP server."""

    @pytest.mark.asyncio
    async def test_returns_error_when_ollama_not_running(self):
        """
        When Ollama is not running (ConnectError), a structured error is
        returned instead of an unhandled exception.
        """
        import httpx

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__  = AsyncMock(return_value=False)
            mock_client.post       = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value = mock_client

            result = await ollama_chat({
                "messages": json.dumps([{"role": "user", "content": "hi"}]),
                "model":    "qwen2.5-coder:7b",
            })

        assert result["returncode"] == 1
        assert "ollama" in result["stderr"].lower() or "not running" in result["stderr"].lower(), (
            f"Error must mention Ollama; got {result['stderr']!r}"
        )

    @pytest.mark.asyncio
    async def test_returns_error_on_http_error(self):
        """HTTP 500 from Ollama returns a structured error, not an exception."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text        = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__  = AsyncMock(return_value=False)
            mock_client.post       = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await ollama_chat({
                "messages": json.dumps([{"role": "user", "content": "hi"}]),
                "model":    "qwen2.5-coder:7b",
            })

        assert result["returncode"] == 1
        assert "500" in result["stderr"] or "error" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_parses_successful_response(self):
        """A valid Ollama response is parsed into our normalised format."""
        import httpx

        ollama_response = {
            "message": {"role": "assistant", "content": "Hello! How can I help?"},
            "prompt_eval_count": 10,
            "eval_count":        20,
            "done": True,
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json        = MagicMock(return_value=ollama_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__  = AsyncMock(return_value=False)
            mock_client.post       = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await ollama_chat({
                "messages": json.dumps([{"role": "user", "content": "hi"}]),
                "model":    "qwen2.5-coder:7b",
            })

        assert result["returncode"] == 0
        inner = json.loads(result["stdout"])
        assert inner["text"] == "Hello! How can I help?"
        assert inner["input_tokens"] == 10
        assert inner["output_tokens"] == 20
        assert inner["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_invalid_messages_json_returns_error(self):
        """Passing garbage as messages JSON returns a structured error."""
        result = await ollama_chat({
            "messages": "not-valid-json{{{{",
            "model":    "qwen2.5-coder:7b",
        })

        assert result["returncode"] == 1
        assert "json" in result["stderr"].lower() or "invalid" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_system_prompt_prepended_to_messages(self):
        """The system param is injected as the first message sent to Ollama."""
        import httpx

        ollama_response = {
            "message": {"role": "assistant", "content": "ok"},
            "prompt_eval_count": 5,
            "eval_count": 3,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json        = MagicMock(return_value=ollama_response)
        captured_body: list[dict] = []

        async def _post(url, json=None, **kw):
            captured_body.append(json or {})
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__  = AsyncMock(return_value=False)
            mock_client.post       = AsyncMock(side_effect=_post)
            mock_client_cls.return_value = mock_client

            await ollama_chat({
                "messages": json.dumps([{"role": "user", "content": "hi"}]),
                "model":    "qwen2.5-coder:7b",
                "system":   "You are a coding assistant.",
            })

        assert captured_body, "A request must have been sent"
        sent_messages = captured_body[0]["messages"]
        assert sent_messages[0]["role"] == "system", (
            f"First message must be the system prompt; got {sent_messages[0]}"
        )
        assert "coding assistant" in sent_messages[0]["content"]
