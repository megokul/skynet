"""
WebSocket round-trip integration tests — real gateway ↔ real agent.

Spins up a minimal in-process WebSocket "gateway" and connects the real CLAW
agent code to it, exercising the full protocol chain end-to-end:

    test-gateway → WebSocket → real agent → real action router → real executor
                                                                      ↓
    test-gateway ← WebSocket ← agent ←──────────── response

The inline gateway is intentionally minimal (auth check + request/response
routing) and does NOT import openclaw-gateway/gateway.py — that avoids the
cross-package 'config' module collision.

What IS tested (no mocks on transport or executor layers):
  - Bearer-token auth: correct token accepted, wrong/absent token rejected.
  - exec_command → real Python subprocess → stdout/returncode in response.
  - Security: disallowed interpreter (bash) → returncode != 0 in response.
  - run_coding_agent(cline) when cline is absent → structured error, NOT fake ✅.
  - Unknown/blocked action → status=error propagated via WebSocket.

What is INTENTIONALLY skipped (external services not available in CI):
  - Real Cline building a project  (skip if 'cline' binary is present)
  - Real Ollama chat               (skip if Ollama is not reachable)
  - Real GitHub repo creation      (skip always — requires credentials)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from contextlib import suppress
from typing import Any

import pytest
import websockets

# ---------------------------------------------------------------------------
# Minimal in-process test gateway
# ---------------------------------------------------------------------------

_TEST_TOKEN = "ws-roundtrip-test-token"


class _TestGateway:
    """
    Tiny WebSocket server that:
      1. Checks the Authorization: Bearer <token> header.
      2. Forwards action_request messages to the connected agent.
      3. Resolves per-request Futures when action_response arrives.
    """

    def __init__(self, token: str = _TEST_TOKEN):
        self._token = token
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._agent_ws = None
        self._connected = asyncio.Event()
        self._server = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await websockets.serve(self._handler, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for_agent(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    async def send_action(
        self,
        action: str,
        params: dict[str, Any],
        *,
        confirmed: bool = True,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        assert self._agent_ws, "No agent connected"
        rid = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rid] = fut
        await self._agent_ws.send(json.dumps({
            "type":       "action_request",
            "request_id": rid,
            "action":     action,
            "params":     params,
            "confirmed":  confirmed,
        }))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def _handler(self, ws) -> None:
        auth = ws.request.headers.get("Authorization", "")
        tok = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if tok != self._token:
            await ws.close(4001, "Unauthorized")
            return

        self._agent_ws = ws
        self._connected.set()
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "action_response":
                    rid = msg.get("request_id", "")
                    fut = self._pending.pop(rid, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
        finally:
            self._agent_ws = None
            self._connected.clear()


# ---------------------------------------------------------------------------
# "Connect once and process" coroutine — uses the real agent code
# ---------------------------------------------------------------------------

async def _run_agent_once(url: str, token: str, *, ready: asyncio.Event) -> None:
    """
    Connect to the test gateway using the real agent WebSocket code, send the
    agent_hello handshake, then process messages until the connection closes.
    """
    from connection.websocket_client import _handle_message, _send_hello  # noqa: PLC0415

    headers = {"Authorization": f"Bearer {token}"}
    async with websockets.connect(url, additional_headers=headers) as ws:
        await _send_hello(ws)
        ready.set()
        async for raw in ws:
            await _handle_message(ws, raw)


# ---------------------------------------------------------------------------
# Shared fixture — gateway + live agent
# ---------------------------------------------------------------------------

@pytest.fixture
async def ws_pair(tmp_path):
    """
    Start the test gateway, connect the real CLAW agent, yield (gateway, tmp_path).

    The agent executes actions using the REAL action router and executors.
    No mocks on the transport or execution layer.
    """
    gw = _TestGateway()
    port = await gw.start()

    agent_ready = asyncio.Event()
    agent_task = asyncio.create_task(
        _run_agent_once(f"ws://127.0.0.1:{port}", _TEST_TOKEN, ready=agent_ready)
    )

    # Wait for both sides to confirm the connection.
    await asyncio.wait_for(agent_ready.wait(), timeout=5)
    await gw.wait_for_agent(timeout=5)

    yield gw, tmp_path

    agent_task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await agent_task
    await gw.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWebSocketAuthentication:
    """Auth handshake tests — no full agent needed."""

    async def test_wrong_token_rejected(self):
        """Connection with a bad token is closed by the gateway (code 4001)."""
        gw = _TestGateway(token="correct-token")
        port = await gw.start()
        try:
            with pytest.raises(Exception):
                async with websockets.connect(
                    f"ws://127.0.0.1:{port}",
                    additional_headers={"Authorization": "Bearer WRONG"},
                ) as ws:
                    await ws.recv()  # Should never reach here.
        finally:
            await gw.close()

    async def test_no_token_rejected(self):
        """Connection with no Authorization header is rejected."""
        gw = _TestGateway()
        port = await gw.start()
        try:
            with pytest.raises(Exception):
                async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                    await ws.recv()
        finally:
            await gw.close()

    async def test_correct_token_accepted(self):
        """Agent connects successfully with the right token."""
        gw = _TestGateway()
        port = await gw.start()
        agent_ready = asyncio.Event()
        task = asyncio.create_task(
            _run_agent_once(f"ws://127.0.0.1:{port}", _TEST_TOKEN, ready=agent_ready)
        )
        try:
            await asyncio.wait_for(agent_ready.wait(), timeout=5)
            await gw.wait_for_agent(timeout=5)
            # If we reach here the agent connected and sent hello successfully.
            assert gw._agent_ws is not None
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            await gw.close()


class TestExecCommandViaWebSocket:
    """
    Full gateway→WebSocket→agent→real-subprocess round-trip.

    These prove that a real action traverses the entire stack:
      send_action(gateway) → WS frame → route() → exec_command() → subprocess
      → result dict → WS frame → Future resolved in gateway.
    """

    async def test_python_script_runs_and_returns_stdout(self, ws_pair):
        """
        exec_command dispatched via WebSocket executes a real Python script and
        returns its stdout to the gateway — same envelope the bot reads.
        """
        gw, tmp_path = ws_pair
        script = tmp_path / "hello.py"
        script.write_text('print("ws-roundtrip-ok")\n')

        response = await gw.send_action(
            "exec_command",
            {"working_dir": str(tmp_path), "command": f"python {script.name}"},
        )

        # Top-level envelope (what gateway.send_action returns).
        assert response["type"] == "action_response"
        assert response["status"] == "success", f"error: {response.get('error')}"

        # Inner result (what bot/handlers/coding.py reads via result["result"]).
        inner = response["result"]
        assert inner["returncode"] == 0, f"stderr: {inner['stderr']}"
        assert "ws-roundtrip-ok" in inner["stdout"]
        assert inner["stderr"] == "" or inner["stderr"] is not None  # always present

    async def test_nonzero_exit_propagated(self, ws_pair):
        """
        A script that exits non-zero returns that exit code through the full
        WS chain — the bot must NOT show ✅ for a non-zero returncode.
        """
        gw, tmp_path = ws_pair
        script = tmp_path / "fail.py"
        script.write_text("raise SystemExit(42)\n")

        response = await gw.send_action(
            "exec_command",
            {"working_dir": str(tmp_path), "command": f"python {script.name}"},
        )

        assert response["status"] == "success"  # routing succeeded; subprocess failed
        assert response["result"]["returncode"] == 42

    async def test_stderr_captured_and_returned(self, ws_pair):
        """stderr from the subprocess is captured and returned to the gateway."""
        gw, tmp_path = ws_pair
        script = tmp_path / "err.py"
        script.write_text("import sys; sys.stderr.write('error-output\\n')\n")

        response = await gw.send_action(
            "exec_command",
            {"working_dir": str(tmp_path), "command": f"python {script.name}"},
        )

        assert response["status"] == "success"
        assert "error-output" in response["result"]["stderr"]

    async def test_disallowed_interpreter_blocked_by_executor(self, ws_pair):
        """
        Sending exec_command with 'bash' is rejected by the executor's
        allowlist — NOT by the WS layer.  Security policy is enforced inside
        the agent, not at the network boundary.
        """
        gw, tmp_path = ws_pair

        response = await gw.send_action(
            "exec_command",
            {"working_dir": str(tmp_path), "command": "bash -c echo hi"},
        )

        # The action was dispatched and executed — the executor returned the error.
        assert response["status"] == "success"
        assert response["result"]["returncode"] != 0
        assert "not allowed" in response["result"]["stderr"].lower()


class TestCodingAgentViaWebSocket:
    """
    run_coding_agent dispatched over a real WebSocket — no mocks.

    In CI, Cline is not installed, so we verify that the 'not found' error
    propagates correctly all the way back to the gateway caller.  This is the
    real-infrastructure equivalent of TestCodingLoopErrorSurfacing in test_e2e.py.
    """

    async def test_cline_not_installed_returns_structured_error(self, ws_pair):
        """
        When cline is absent, run_coding_agent returns returncode != 0 and a
        descriptive stderr message.  The gateway must NOT interpret this as
        success (and must NOT show "✅ Milestone complete!").
        """
        if shutil.which("cline"):
            pytest.skip("cline is installed on this machine — skipping 'not found' path")

        gw, tmp_path = ws_pair

        response = await gw.send_action(
            "run_coding_agent",
            {
                "agent":           "cline",
                "prompt":          "Write a hello-world Python script",
                "working_dir":     str(tmp_path),
                "timeout_seconds": 30,
            },
        )

        # Outer envelope — action was routed and executed.
        assert response["type"] == "action_response"
        assert response["status"] == "success", f"unexpected error: {response.get('error')}"

        # Inner result — executor returned failure.
        inner = response["result"]
        assert inner["returncode"] != 0, (
            "Expected non-zero returncode when cline binary is missing.\n"
            f"Got stdout={inner.get('stdout')!r}, stderr={inner.get('stderr')!r}"
        )
        stderr = inner["stderr"].lower()
        assert "cline" in stderr, f"Expected 'cline' in stderr: {inner['stderr']!r}"
        assert "not found" in stderr, f"Expected 'not found' in stderr: {inner['stderr']!r}"

    async def test_unknown_agent_returns_error_via_ws(self, ws_pair):
        """Unknown agent name returns an error response through the WS channel."""
        gw, tmp_path = ws_pair

        response = await gw.send_action(
            "run_coding_agent",
            {
                "agent":  "unknownagent",
                "prompt": "do something",
            },
        )

        # The executor rejects an unknown agent; router wraps it as success
        # (action executed) with a non-zero returncode.
        assert response["status"] == "success"
        assert response["result"]["returncode"] != 0
        assert "unknown" in response["result"]["stderr"].lower()

    async def test_qwen_planner_summary_payload_passes_security_validation(self, ws_pair):
        """
        Structured planner summary payloads must reach the executor.

        This guards the live preflight path where requirement_summary_md contains
        quotes and markdown bullets. The request should not be rejected by the
        shell-metacharacter validator.
        """
        if shutil.which("cline"):
            pytest.skip("cline is installed on this machine - skipping missing-binary path")

        gw, tmp_path = ws_pair

        response = await gw.send_action(
            "run_coding_agent",
            {
                "agent": "cline",
                "prompt": "Write a hello-world Python script",
                "working_dir": str(tmp_path),
                "timeout_seconds": 30,
                "reply_contract": "emit_ready_sentence",
                "planner_state_json": {"plan_ready": True, "missing_slots": []},
                "requirement_summary_md": (
                    "- Project Kind: local terminal utility script\n"
                    "- Constraints: show a popup saying \"hi\" and play a beep\n"
                    "- Integrations: none"
                ),
            },
        )

        assert response["type"] == "action_response"
        assert response["status"] == "success"
        assert response["result"]["returncode"] != 0
        assert "disallowed shell metacharacters" not in response["result"].get("stderr", "").lower()


class TestSecurityPolicyViaWebSocket:
    """
    Security constraints enforced end-to-end through the WebSocket channel.
    These tests confirm that the security layer cannot be bypassed by the transport.
    """

    async def test_blocked_action_returns_error(self, ws_pair):
        """
        An action not in AUTO_ACTIONS or CONFIRM_ACTIONS is BLOCKED by the
        security validator and returns status=error, regardless of transport.
        """
        gw, _ = ws_pair

        response = await gw.send_action(
            "shell_exec",       # Explicitly blocked action.
            {"cmd": "echo hi"},
        )

        assert response["type"] == "action_response"
        assert response["status"] == "error"
        assert "blocked" in response.get("error", "").lower()

    async def test_unrecognised_action_blocked(self, ws_pair):
        """Completely unknown action names are implicitly blocked."""
        gw, _ = ws_pair

        response = await gw.send_action("totally_made_up_action", {})

        assert response["status"] == "error"
        assert "blocked" in response.get("error", "").lower()
