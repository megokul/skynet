"""
SKYNET live E2E runner with persistent tracing.

Default mode runs the full conversation simulation:
  hi -> project creation -> requirements -> plan -> approve -> coding -> run project

Usage:
  python openclaw-gateway/tests/e2e_live.py

Modes:
  SKYNET_LIVE_E2E_FLOW=conversation  (default)
  SKYNET_LIVE_E2E_FLOW=direct        (legacy direct coding smoke)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency at runtime
    load_dotenv = None  # type: ignore[assignment]

# Make sure we can import from the gateway package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROMPT = (
    "Create a minimal Python project with a single file called main.py. "
    "The script should print exactly: SKYNET_E2E_OK\n"
    "No dependencies, no imports beyond stdlib. "
    "Output only the file in a fenced code block: ```main.py"
)

SLUG = f"e2e-test-{int(time.time())}"


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class LiveTrace:
    def __init__(self, label: str) -> None:
        env_path = os.environ.get("SKYNET_LIVE_TRACE_FILE", "").strip()
        if env_path:
            path = Path(env_path)
        else:
            artifacts = Path(__file__).resolve().parent / ".artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            path = artifacts / f"{label}-{int(time.time())}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._started = time.monotonic()
        self.log("trace.start", trace_file=str(self.path))

    def log(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_s": round(time.monotonic() - self._started, 1),
            "event": event,
        }
        payload.update(fields)
        line = json.dumps(payload, ensure_ascii=True, default=str)
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _fail(trace: LiveTrace, message: str, *, detail: Any | None = None) -> None:
    text = str(detail)[:1200] if detail is not None else ""
    trace.log("run.fail", message=message, detail=text)
    print(f"[FAIL] {message}")
    if text:
        print(text)
    print(f"[TRACE] {trace.path}")
    sys.exit(1)


def _check_env(trace: LiveTrace) -> None:
    required = ("OPENCLAW_SSH_HOST", "OPENCLAW_SSH_USER")
    missing = [var for var in required if not os.environ.get(var)]
    trace.log(
        "env.check",
        required=list(required),
        missing=missing,
        has_key_path=bool(os.environ.get("OPENCLAW_SSH_KEY_PATH", "").strip()),
        has_password=bool(os.environ.get("OPENCLAW_SSH_PASSWORD", "").strip()),
    )
    if missing:
        print(f"[SKIP] Missing env vars: {', '.join(missing)}")
        print("       Set OPENCLAW_SSH_* vars and retry.")
        print(f"[TRACE] {trace.path}")
        sys.exit(0)


def _load_live_env(trace: LiveTrace) -> None:
    if load_dotenv is None:
        trace.log("env.dotenv", status="unavailable", reason="python-dotenv not installed")
        return

    repo_root = Path(__file__).resolve().parents[2]
    candidates = []
    explicit = os.environ.get("SKYNET_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            repo_root / ".env",
            repo_root / "openclaw-gateway" / ".env",
        ]
    )

    loaded: list[str] = []
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
        loaded.append(str(candidate))

    trace.log("env.dotenv", loaded_files=loaded)


def _run_conversation_flow(trace: LiveTrace) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    target = (
        "openclaw-gateway/tests/test_e2e_conversation_live.py::"
        "test_live_conversation_real_planner_codegen_and_github_push"
    )
    cmd = [sys.executable, "-m", "pytest", target, "-q", "-s"]
    env = os.environ.copy()
    env["SKYNET_E2E_LIVE"] = os.environ.get("SKYNET_E2E_LIVE", "1")
    env["SKYNET_LIVE_TRACE_FILE"] = str(trace.path)

    trace.log(
        "conversation.invoke",
        repo_root=str(repo_root),
        cmd=" ".join(cmd),
    )
    rc = subprocess.call(cmd, cwd=str(repo_root), env=env)
    trace.log("conversation.exit", returncode=rc)
    if rc != 0:
        _fail(trace, "Conversation live E2E failed.", detail=f"pytest exit code: {rc}")
    trace.log("run.success", flow="conversation")
    print("[OK] Conversation live E2E passed.")
    print(f"[TRACE] {trace.path}")


async def _execute_action_with_trace(
    *,
    trace: LiveTrace,
    executor,
    action: str,
    params: dict[str, Any],
    timeout_s: int = 600,
    confirmed: bool = False,
) -> dict[str, Any]:
    trace.log(
        "action.start",
        action=action,
        timeout_s=timeout_s,
        confirmed=confirmed,
        param_keys=sorted(params.keys()),
    )
    started = time.monotonic()
    pending = asyncio.create_task(
        executor.execute_action(action, params, confirmed=confirmed)
    )
    try:
        while True:
            try:
                result = await asyncio.wait_for(asyncio.shield(pending), timeout=15)
                break
            except asyncio.TimeoutError:
                trace.log(
                    "action.waiting",
                    action=action,
                    wait_s=round(time.monotonic() - started, 1),
                )
                if (time.monotonic() - started) >= timeout_s:
                    raise TimeoutError(f"Action '{action}' exceeded {timeout_s}s")
    finally:
        if not pending.done():
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass

    elapsed = round(time.monotonic() - started, 2)
    inner = result.get("result", result)
    trace.log(
        "action.done",
        action=action,
        elapsed_s=elapsed,
        status=result.get("status"),
        returncode=inner.get("returncode", inner.get("exit_code")),
        error=str(result.get("error", ""))[:220],
    )
    return result


async def _run_direct_flow(trace: LiveTrace) -> None:
    from ssh_tunnel_executor import get_ssh_executor

    executor = get_ssh_executor()
    if not executor.is_configured():
        _fail(trace, "SSH executor is not configured. Check OPENCLAW_SSH_* env vars.")

    base_dir = os.environ.get("OPENCLAW_PROJECT_BASE_DIR", "E:\\SKYNET-SANDBOX\\Projects")
    working_dir = base_dir.rstrip("\\") + "\\" + SLUG
    trace.log("run.config", base_dir=base_dir, working_dir=working_dir)

    result = await _execute_action_with_trace(
        trace=trace,
        executor=executor,
        action="create_directory",
        params={"directory": working_dir},
        timeout_s=120,
    )
    if result.get("status") == "error" or result.get("result", {}).get("returncode", 0) != 0:
        _fail(trace, f"Could not create directory: {working_dir}", detail=result)

    model = os.environ.get("SKYNET_CLAUDE_OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:7b")
    backend = os.environ.get("SKYNET_LIVE_E2E_BACKEND", "ollama")
    agent = os.environ.get("SKYNET_LIVE_E2E_AGENT", "claude")
    auto_pull = _bool_env("SKYNET_CLAUDE_OLLAMA_AUTO_PULL", True)
    trace.log(
        "coding.invoke",
        agent=agent,
        backend=backend,
        model=model,
        auto_pull_model=auto_pull,
    )
    result = await _execute_action_with_trace(
        trace=trace,
        executor=executor,
        action="run_coding_agent",
        params={
            "agent": agent,
            "backend": backend,
            "model": model,
            "prompt": PROMPT,
            "working_dir": working_dir,
            "timeout_seconds": 1800,
            "auto_pull_model": auto_pull,
        },
        timeout_s=1900,
        confirmed=True,
    )
    inner = result.get("result", result)
    if result.get("status") == "error":
        _fail(trace, "run_coding_agent failed", detail=result)
    if inner.get("returncode", 0) != 0:
        _fail(trace, "run_coding_agent returned non-zero", detail=inner)

    main_py = working_dir.rstrip("\\") + "\\main.py"
    result = await _execute_action_with_trace(
        trace=trace,
        executor=executor,
        action="file_read",
        params={"file": main_py},
        timeout_s=90,
    )
    inner = result.get("result", result)
    content = inner.get("content", inner.get("stdout", ""))
    if result.get("status") == "error" or not content:
        _fail(trace, f"main.py not found or empty at {main_py}", detail=result)
    trace.log("file.main_py", path=main_py, content_len=len(content))

    result = await _execute_action_with_trace(
        trace=trace,
        executor=executor,
        action="exec_command",
        params={"working_dir": working_dir, "command": "python main.py"},
        timeout_s=180,
    )
    inner = result.get("result", result)
    stdout = str(inner.get("stdout", ""))
    returncode = int(inner.get("returncode", inner.get("exit_code", -1)))
    if returncode != 0:
        _fail(trace, f"exec_command returned non-zero ({returncode})", detail=inner)
    if "SKYNET_E2E_OK" not in stdout:
        trace.log("run.warning", message="Expected SKYNET_E2E_OK not found", stdout=stdout[:220])
    else:
        trace.log("run.output_ok", marker="SKYNET_E2E_OK")

    trace.log("run.success", flow="direct", working_dir=working_dir)
    print("[OK] Direct live E2E passed.")
    print(f"[TRACE] {trace.path}")
    print(f"[ARTIFACT] working_dir={working_dir}")


async def run() -> None:
    trace = LiveTrace("e2e-live")
    trace.log("run.start", python=sys.version.split()[0], cwd=os.getcwd())
    _load_live_env(trace)
    _check_env(trace)

    flow = os.environ.get("SKYNET_LIVE_E2E_FLOW", "conversation").strip().lower()
    trace.log("run.mode", flow=flow)
    if flow in {"conversation", "chat"}:
        _run_conversation_flow(trace)
        return
    await _run_direct_flow(trace)


if __name__ == "__main__":
    asyncio.run(run())
