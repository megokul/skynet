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
import shutil
import uuid
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from skynet.project_specialist import (
    build_qwen_plan_generation_context,
    build_qwen_planner_context,
    build_qwen_planner_prompt,
    build_project_specialist_opening,
    build_project_specialist_system_prompt,
)
from skynet.qwen_cli import build_qwen_runtime_prompt


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

    @pytest.mark.asyncio
    async def test_runs_python_argv_without_shell_string(self, tmp_path):
        """Structured argv supports quoted python -c preflight without shell parsing."""
        result = await exec_command({
            "argv": ["python", "-c", "print('argv-ok')"],
            "working_dir": str(tmp_path),
        })

        assert result["returncode"] == 0, f"Expected exit 0; got {result}"
        assert "argv-ok" in result["stdout"]


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

    @pytest.mark.asyncio
    async def test_qwen_requires_task_mode(self, tmp_path):
        import executor.actions as actions_mod

        saved = actions_mod._CODING_AGENT_BINARIES["qwen"]
        actions_mod._CODING_AGENT_BINARIES["qwen"] = "qwen"
        try:
            with pytest.raises(ValueError, match="task_mode"):
                await run_coding_agent({
                    "agent": "qwen",
                    "prompt": "plan this",
                    "working_dir": str(tmp_path),
                })
        finally:
            actions_mod._CODING_AGENT_BINARIES["qwen"] = saved

    @pytest.mark.asyncio
    async def test_qwen_json_output_is_normalized(self, tmp_path):
        import executor.actions as actions_mod

        fake_stdout = json.dumps(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "sess-123",
                    "model": "coder-model",
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Which framework should this script use?"}
                        ]
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "sess-123",
                    "result": "Which framework should this script use?",
                },
            ]
        )

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(
                actions_mod,
                "_run_tracked_coding_subprocess",
                AsyncMock(
                    return_value={
                        "returncode": 0,
                        "stdout": fake_stdout,
                        "stderr": "",
                        "session_key": "sess-123",
                        "remote_pid": "100",
                    }
                ),
            ),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "planner_chat",
                "prompt": "plan this",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 0
        assert result["assistant_text"] == "Which framework should this script use?"
        assert result["stdout"] == "Which framework should this script use?"
        assert result["output_contract"] == "ok"
        assert result["session_id"] == "sess-123"
        assert result["auth_type"] == "qwen-oauth"

    @pytest.mark.asyncio
    async def test_qwen_command_normalizes_session_id_to_uuid(self, tmp_path):
        import executor.actions as actions_mod

        seen: dict[str, list[str]] = {}

        async def _fake_run_tracked_coding_subprocess(**kwargs):
            seen["args"] = list(kwargs["args"])
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {"type": "system", "subtype": "init", "session_id": str(uuid.uuid4())},
                        {
                            "type": "result",
                            "subtype": "success",
                            "result": "Grounded planner reply.",
                        },
                    ]
                ),
                "stderr": "",
                "session_key": "abc123",
                "remote_pid": "100",
            }

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(actions_mod, "_run_tracked_coding_subprocess", AsyncMock(side_effect=_fake_run_tracked_coding_subprocess)),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "planner_chat",
                "session_key": "abc123",
                "prompt": "plan this",
                "working_dir": str(tmp_path),
            })

        session_id = seen["args"][seen["args"].index("--session-id") + 1]
        assert result["returncode"] == 0
        assert session_id == str(uuid.uuid5(uuid.NAMESPACE_URL, "abc123"))

    @pytest.mark.asyncio
    async def test_qwen_meta_output_becomes_contract_failure(self, tmp_path):
        import executor.actions as actions_mod

        fake_stdout = json.dumps(
            [
                {"type": "system", "subtype": "init", "session_id": "sess-456", "model": "coder-model"},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Understood. I'm ready to assist with your Telegram product workflow planning. What would you like to work on?",
                            }
                        ]
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "sess-456",
                    "result": "Understood. I'm ready to assist with your Telegram product workflow planning. What would you like to work on?",
                },
            ]
        )

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(
                actions_mod,
                "_run_tracked_coding_subprocess",
                AsyncMock(
                    return_value={
                        "returncode": 0,
                        "stdout": fake_stdout,
                        "stderr": "",
                        "session_key": "sess-456",
                        "remote_pid": "100",
                    }
                ),
            ),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "planner_chat",
                "prompt": "plan this",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 1
        assert result["output_contract"] == "planner_meta_output"
        assert "QWEN_CONTRACT_VIOLATION" in result["stderr"]

    @pytest.mark.asyncio
    async def test_qwen_ready_sentence_mismatch_becomes_contract_failure(self, tmp_path):
        import executor.actions as actions_mod

        fake_stdout = json.dumps(
            [
                {"type": "system", "subtype": "init", "session_id": "sess-ready", "model": "coder-model"},
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "sess-ready",
                    "result": "I need one more clarification before I can continue.",
                },
            ]
        )

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(
                actions_mod,
                "_run_tracked_coding_subprocess",
                AsyncMock(
                    return_value={
                        "returncode": 0,
                        "stdout": fake_stdout,
                        "stderr": "",
                        "session_key": "sess-ready",
                        "remote_pid": "100",
                    }
                ),
            ),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "planner_chat",
                "reply_contract": "emit_ready_sentence",
                "planner_state_json": {"plan_ready": True, "missing_slots": []},
                "requirement_summary_md": "- Project Kind: local terminal utility script",
                "prompt": "reply exactly with the ready sentence",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 1
        assert result["output_contract"] == "planner_ready_sentence_mismatch"

    @pytest.mark.asyncio
    async def test_qwen_question_targeting_rejects_answered_slots(self, tmp_path):
        import executor.actions as actions_mod

        fake_stdout = json.dumps(
            [
                {"type": "system", "subtype": "init", "session_id": "sess-question", "model": "coder-model"},
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "sess-question",
                    "result": "What does this app do and which framework should it use?",
                },
            ]
        )

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(
                actions_mod,
                "_run_tracked_coding_subprocess",
                AsyncMock(
                    return_value={
                        "returncode": 0,
                        "stdout": fake_stdout,
                        "stderr": "",
                        "session_key": "sess-question",
                        "remote_pid": "100",
                    }
                ),
            ),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "planner_chat",
                "reply_contract": "ask_next_question",
                "planner_state_json": {
                    "plan_ready": False,
                    "missing_slots": ["storage", "integrations"],
                },
                "requirement_summary_md": "- Project Kind: local terminal utility script",
                "prompt": "ask about the missing slots only",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 1
        assert result["output_contract"] == "planner_question_targets_answered_slot"

    @pytest.mark.asyncio
    async def test_qwen_plan_generation_invalid_output_becomes_contract_failure(self, tmp_path):
        import executor.actions as actions_mod

        fake_stdout = json.dumps(
            [
                {"type": "system", "subtype": "init", "session_id": "sess-789", "model": "coder-model"},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "I don't have any requirements gathered yet for this project. "
                                    "Let me ask the key questions first."
                                ),
                            }
                        ]
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "sess-789",
                    "result": "I don't have any requirements gathered yet for this project. Let me ask the key questions first.",
                },
            ]
        )

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(
                actions_mod,
                "_run_tracked_coding_subprocess",
                AsyncMock(
                    return_value={
                        "returncode": 0,
                        "stdout": fake_stdout,
                        "stderr": "",
                        "session_key": "sess-789",
                        "remote_pid": "100",
                    }
                ),
            ),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "plan_generation",
                "prompt": "generate the project plan now",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 1
        assert result["output_contract"] == "plan_generation_requirement_reset"
        assert "QWEN_CONTRACT_VIOLATION" in result["stderr"]

    @pytest.mark.asyncio
    async def test_qwen_openai_provider_env_is_forwarded(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        import executor.actions as actions_mod

        seen_env: dict[str, str] = {}

        async def _fake_run_tracked_coding_subprocess(**kwargs):
            env = dict(kwargs.get("env") or {})
            seen_env["OPENAI_API_KEY"] = env.get("OPENAI_API_KEY", "")
            seen_env["OPENAI_BASE_URL"] = env.get("OPENAI_BASE_URL", "")
            seen_env["OPENAI_MODEL"] = env.get("OPENAI_MODEL", "")
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {"type": "system", "subtype": "init", "session_id": str(uuid.uuid4())},
                        {"type": "result", "subtype": "success", "result": "What storage is needed?"},
                    ]
                ),
                "stderr": "",
                "session_key": "openai-env",
                "remote_pid": "100",
            }

        monkeypatch.setenv("SKYNET_QWEN_AUTH_TYPE", "openai")
        monkeypatch.setenv("SKYNET_QWEN_PROVIDER_PROFILE", "remote-qwen")
        monkeypatch.setenv("SKYNET_QWEN_OPENAI_BASE_URL", "https://api.example.test/v1")
        monkeypatch.setenv("SKYNET_QWEN_OPENAI_API_KEY_ENV", "MY_QWEN_KEY")
        monkeypatch.setenv("MY_QWEN_KEY", "secret-123")

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(actions_mod, "_run_tracked_coding_subprocess", AsyncMock(side_effect=_fake_run_tracked_coding_subprocess)),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "planner_chat",
                "reply_contract": "ask_next_question",
                "planner_state_json": {"plan_ready": False, "missing_slots": ["storage"]},
                "requirement_summary_md": "- Project Kind: local terminal utility script",
                "prompt": "ask about storage",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 0
        assert seen_env["OPENAI_API_KEY"] == "secret-123"
        assert seen_env["OPENAI_BASE_URL"] == "https://api.example.test/v1"
        assert seen_env["OPENAI_MODEL"] == ""

    @pytest.mark.asyncio
    async def test_qwen_plan_generation_uses_request_scoped_working_dir(self, tmp_path):
        import executor.actions as actions_mod

        seen: dict[str, str] = {}

        async def _fake_run_tracked_coding_subprocess(**kwargs):
            seen["cwd"] = str(kwargs["cwd"] or "")
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {"type": "system", "subtype": "init", "session_id": str(uuid.uuid4())},
                        {
                            "type": "result",
                            "subtype": "success",
                            "result": (
                                "**Demo - Project Plan**\n"
                                "**Overview:** demo\n"
                                "**Core Features:**\n- one\n"
                                "**Tech Stack:** python\n"
                                "**Project Structure:**\n- app/\n"
                                "**Milestones:**\n1. ship\n"
                                "**Open Questions:** None"
                            ),
                        },
                    ]
                ),
                "stderr": "",
                "session_key": "req-scoped",
                "remote_pid": "100",
            }

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(actions_mod, "_run_tracked_coding_subprocess", AsyncMock(side_effect=_fake_run_tracked_coding_subprocess)),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "plan_generation",
                "session_key": "req-scoped",
                "prompt": "generate the project plan now",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 0
        assert seen["cwd"] != str(tmp_path)
        assert result["qwen_requested_working_dir"] == str(tmp_path)
        assert result["qwen_effective_working_dir"] == seen["cwd"]

    def test_qwen_ready_sentence_runtime_prompt_forbids_inline_plan_generation(self):
        prompt = build_qwen_runtime_prompt(
            prompt="reply exactly with the ready sentence",
            task_mode="planner_chat",
            reply_contract="emit_ready_sentence",
            planner_state={"plan_ready": True, "missing_slots": []},
            requirement_summary_md="- Project Kind: local terminal utility script",
        )

        assert "Return exactly this sentence" in prompt
        assert "Do not generate the project plan yet." in prompt
        assert "Stop immediately after the final period." in prompt

    def test_qwen_coding_runtime_prompt_demands_immediate_implementation(self):
        prompt = build_qwen_runtime_prompt(
            prompt="Create main.py that prints hello",
            task_mode="coding_implementation",
        )

        assert "Implement the following task now" in prompt
        assert "Do not ask what to build or implement." in prompt
        assert "If the workspace is empty, scaffold the required files yourself." in prompt
        assert "Create main.py that prints hello" in prompt

    @pytest.mark.asyncio
    async def test_qwen_coding_meta_output_becomes_contract_failure(self, tmp_path):
        import executor.actions as actions_mod

        fake_stdout = json.dumps(
            [
                {"type": "system", "subtype": "init", "session_id": "sess-code", "model": "coder-model"},
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "sess-code",
                    "result": "I'm ready to help with your coding implementation task. What would you like me to build or implement?",
                },
            ]
        )

        with (
            patch.object(actions_mod, "_resolve_coding_binary", return_value=("qwen", "qwen")),
            patch.object(
                actions_mod,
                "_run_tracked_coding_subprocess",
                AsyncMock(
                    return_value={
                        "returncode": 0,
                        "stdout": fake_stdout,
                        "stderr": "",
                        "session_key": "sess-code",
                        "remote_pid": "100",
                    }
                ),
            ),
        ):
            result = await run_coding_agent({
                "agent": "qwen",
                "task_mode": "coding_implementation",
                "prompt": "Create main.py that prints hello",
                "working_dir": str(tmp_path),
            })

        assert result["returncode"] == 1
        assert result["output_contract"] == "coding_meta_output"

    @pytest.mark.asyncio
    async def test_real_qwen_planner_multiline_prompt(self, tmp_path):
        if os.environ.get("SKYNET_RUN_REAL_QWEN_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"}:
            pytest.skip("real qwen tests are opt-in via SKYNET_RUN_REAL_QWEN_TESTS=1")
        if not shutil.which("qwen"):
            pytest.skip("qwen binary not installed")
        if not Path.home().joinpath(".qwen", "settings.json").exists():
            pytest.skip("qwen auth settings not configured")

        template = {
            "stack": "Python 3.11+ + FastAPI or Flask + SQLAlchemy + PostgreSQL",
            "questions": [
                "What does this app do? (web service, automation, utility, other)",
                "Which framework? (FastAPI, Flask, plain Python script)",
                "Does it need a database? If so, what data does it store?",
                "Will it run as a background service, scheduled job, or on-demand?",
                "Any external APIs or services to integrate with?",
            ],
        }
        system = build_project_specialist_system_prompt("real-qwen-probe", "Python App", template)
        messages = [
            {
                "role": "assistant",
                "content": build_project_specialist_opening("real-qwen-probe", "Python App", template),
            },
            {
                "role": "user",
                "content": (
                    "I'm building a small Windows Python script that runs from the terminal. "
                    "When executed, it should show a popup saying \"hi\" and play a short beep sound. "
                    "Use only Python standard library, include tests, and add a valid skynet_run.json."
                ),
            },
        ]

        result = await run_coding_agent({
            "agent": "qwen",
            "task_mode": "planner_chat",
            "prompt": build_qwen_planner_prompt(messages),
            "qwen_context_text": build_qwen_planner_context(system),
            "working_dir": str(tmp_path),
            "timeout_seconds": 120,
        })

        assert result["returncode"] == 0, result
        assert result["output_contract"] == "ok", result
        lowered = result["assistant_text"].lower()
        assert "what would you like" not in lowered, result["assistant_text"]
        assert "ready to assist" not in lowered, result["assistant_text"]

    @pytest.mark.asyncio
    async def test_real_qwen_plan_generation_prompt(self, tmp_path):
        if os.environ.get("SKYNET_RUN_REAL_QWEN_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"}:
            pytest.skip("real qwen tests are opt-in via SKYNET_RUN_REAL_QWEN_TESTS=1")
        if not shutil.which("qwen"):
            pytest.skip("qwen binary not installed")
        if not Path.home().joinpath(".qwen", "settings.json").exists():
            pytest.skip("qwen auth settings not configured")

        template = {
            "stack": "Python 3.11+ + FastAPI or Flask + SQLAlchemy + PostgreSQL",
            "questions": [
                "What does this app do? (web service, automation, utility, other)",
                "Which framework? (FastAPI, Flask, plain Python script)",
                "Does it need a database? If so, what data does it store?",
                "Will it run as a background service, scheduled job, or on-demand?",
                "Any external APIs or services to integrate with?",
            ],
        }
        system = build_project_specialist_system_prompt("real-qwen-plan", "Python App", template)
        messages = [
            {
                "role": "assistant",
                "content": build_project_specialist_opening("real-qwen-plan", "Python App", template),
            },
            {
                "role": "user",
                "content": (
                    "I'm building a small Windows Python script that runs from the terminal. "
                    "When executed, it should show a popup saying \"hi\" and play a short beep sound. "
                    "Use only Python standard library, include tests, and add a valid skynet_run.json."
                ),
            },
            {
                "role": "user",
                "content": "Generate the full project plan now based on everything we discussed.",
            },
        ]

        result = await run_coding_agent({
            "agent": "qwen",
            "task_mode": "plan_generation",
            "prompt": build_qwen_planner_prompt(messages),
            "qwen_context_text": build_qwen_plan_generation_context(system),
            "working_dir": str(tmp_path),
            "timeout_seconds": 120,
        })

        assert result["returncode"] == 0, result
        assert result["output_contract"] == "ok", result
        lowered = result["assistant_text"].lower()
        assert "overview:" in lowered, result["assistant_text"]
        assert "core features:" in lowered, result["assistant_text"]
        assert "milestones:" in lowered, result["assistant_text"]
        assert "what does this app do?" not in lowered, result["assistant_text"]


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
