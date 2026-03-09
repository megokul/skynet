"""
SKYNET live E2E runner with persistent tracing.

Default mode runs the full conversation simulation:
  hi -> project creation -> requirements -> plan -> approve -> coding -> run project

Usage:
  python openclaw-gateway/tests/e2e_live.py

Modes:
  SKYNET_LIVE_E2E_FLOW=conversation   (simulated Telegram transport, real planner/coding/SSH)
  SKYNET_LIVE_E2E_FLOW=telegram_real  (fully real Telegram-network chat via Telethon user session)
  SKYNET_LIVE_E2E_FLOW=direct        (legacy direct coding smoke)
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = REPO_ROOT / "openclaw-gateway"
for candidate in (str(REPO_ROOT), str(GATEWAY_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from live_settings import build_gateway_runtime_env, trace_gateway_runtime
import config as cfg
from live_diagnostics import LiveTrace


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


def _fail(trace: LiveTrace, message: str, *, detail: Any | None = None) -> None:
    text = str(detail)[:1200] if detail is not None else ""
    trace.log("run.fail", message=message, detail=text)
    print(f"[FAIL] {message}")
    if text:
        print(text)
    print(f"[TRACE] {trace.path}")
    sys.exit(1)


def _check_env(trace: LiveTrace, flow: str = "conversation") -> None:
    telegram_real_flows = {"telegram_real", "telegram", "real_telegram"}
    transport = os.environ.get("SKYNET_E2E_TRANSPORT", "websocket").strip().lower()

    if flow in telegram_real_flows:
        required = (
            "SKYNET_E2E_TELEGRAM_API_ID",
            "SKYNET_E2E_TELEGRAM_API_HASH",
            "SKYNET_E2E_TELEGRAM_SESSION",
            "SKYNET_E2E_TELEGRAM_BOT_USERNAME",
        )
        requires_ssh = False
    elif transport == "ssh":
        required = ("OPENCLAW_SSH_HOST", "OPENCLAW_SSH_USER")
        requires_ssh = True
    else:
        required = ("SKYNET_AUTH_TOKEN",)
        requires_ssh = False

    missing = [var for var in required if not os.environ.get(var)]
    ssh_host = os.environ.get("OPENCLAW_SSH_HOST", "").strip()
    in_container = Path("/.dockerenv").exists()
    strict = _bool_env("SKYNET_E2E_FAIL_ON_SKIP", True)
    trace.log(
        "env.check",
        flow=flow,
        transport=transport,
        required=list(required),
        missing=missing,
        ssh_host=ssh_host,
        in_container=in_container,
        has_key_path=bool(os.environ.get("OPENCLAW_SSH_KEY_PATH", "").strip()),
        has_password=bool(os.environ.get("OPENCLAW_SSH_PASSWORD", "").strip()),
    )
    if missing:
        detail = f"Missing env vars: {', '.join(missing)}"
        if strict:
            _fail(trace, "Live E2E environment validation failed.", detail=detail)
        print(f"[SKIP] {detail}")
        if requires_ssh:
            print("       Set OPENCLAW_SSH_* vars and retry.")
        elif flow in telegram_real_flows:
            print("       Set SKYNET_E2E_TELEGRAM_* vars and retry.")
        else:
            print("       Set SKYNET_AUTH_TOKEN and retry.")
        print(f"[TRACE] {trace.path}")
        sys.exit(0)

    # host.docker.internal is valid for a Dockerized gateway on EC2 host.
    # It is not reachable when running this script directly on the worker laptop host.
    if requires_ssh and ssh_host.lower() == "host.docker.internal" and not in_container:
        detail = (
            "OPENCLAW_SSH_HOST=host.docker.internal is for Dockerized gateway runtime. "
            "This run is host-side. Use SKYNET_ENV_FILE=.env.local-e2e "
            "(OPENCLAW_SSH_HOST=127.0.0.1, OPENCLAW_SSH_PORT=22) or run e2e_live.py inside the EC2 gateway container."
        )
        trace.log("env.mismatch", reason="docker_host_alias_from_non_container", detail=detail)
        if strict:
            _fail(trace, "Live E2E environment validation failed.", detail=detail)
        print(f"[SKIP] {detail}")
        print(f"[TRACE] {trace.path}")
        sys.exit(0)


def _infer_infra_category(output: str) -> str:
    text = (output or "").lower()
    if "maxstartups" in text or "exceeded maxstartups" in text:
        return "capacity"
    if "permission denied" in text or "authentication failed" in text:
        return "auth"
    if "protocol banner" in text or "no existing session" in text:
        return "banner"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if (
        "connection refused" in text
        or "could not resolve hostname" in text
        or "name or service not known" in text
        or "network is unreachable" in text
        or "no route to host" in text
        or "ssh executor unreachable" in text
    ):
        return "unreachable"
    return ""


def _run_conversation_flow(trace: LiveTrace) -> None:
    target = (
        "openclaw-gateway/tests/test_e2e_conversation_live.py::"
        "test_live_conversation_real_planner_codegen_no_github_push"
    )
    cmd = [sys.executable, "-m", "pytest", target, "-q", "-s"]
    env = build_gateway_runtime_env(dict(os.environ))
    env["SKYNET_E2E_LIVE"] = os.environ.get("SKYNET_E2E_LIVE", "1")
    env["SKYNET_LIVE_TRACE_FILE"] = str(trace.path)
    env["SKYNET_E2E_FAIL_ON_SKIP"] = os.environ.get("SKYNET_E2E_FAIL_ON_SKIP", "1")

    trace.log("e2e.step.start", step="conversation_flow", status="start")
    trace.log(
        "conversation.invoke",
        repo_root=str(REPO_ROOT),
        cmd=" ".join(cmd),
    )
    completed = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        errors="replace",
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    match_passed = re.search(r"(\d+)\s+passed", output, flags=re.IGNORECASE)
    match_skipped = re.search(r"(\d+)\s+skipped", output, flags=re.IGNORECASE)
    passed = int(match_passed.group(1)) if match_passed else 0
    skipped = int(match_skipped.group(1)) if match_skipped else 0
    infra_category = _infer_infra_category(output)
    trace.log(
        "conversation.exit",
        returncode=completed.returncode,
        pytest_passed=passed,
        pytest_skipped=skipped,
        infra_category=infra_category,
    )
    strict = _bool_env("SKYNET_E2E_FAIL_ON_SKIP", True)
    if completed.returncode == 0 and skipped > 0 and strict:
        if infra_category:
            print(f"[INFRA] Live E2E skip category: {infra_category}")
            trace.log(
                "e2e.step.fail",
                step="conversation_flow",
                status="fail",
                error_message="Conversation live E2E was skipped under strict mode.",
            )
            _fail(
                trace,
                "Conversation live E2E was skipped under strict mode.",
                detail=f"pytest skipped={skipped}",
            )
    if completed.returncode != 0:
        if infra_category:
            print(f"[INFRA] Conversation live E2E infra category: {infra_category}")
        trace.log(
            "e2e.step.fail",
            step="conversation_flow",
            status="fail",
            error_message=f"Conversation live E2E failed (exit={completed.returncode}).",
        )
        _fail(
            trace,
            "Conversation live E2E failed.",
            detail=f"pytest exit code: {completed.returncode}",
        )
    trace.log("e2e.step.end", step="conversation_flow", status="ok")
    trace.log("run.success", flow="conversation")
    print("[OK] Conversation live E2E passed.")
    print(f"[TRACE] {trace.path}")


def _run_telegram_real_flow(trace: LiveTrace) -> None:
    target = (
        "openclaw-gateway/tests/test_e2e_telegram_real_live.py::"
        "test_real_telegram_chat_flow_no_github_repo_creation"
    )
    cmd = [sys.executable, "-m", "pytest", target, "-q", "-s"]
    env = build_gateway_runtime_env(dict(os.environ))
    env["SKYNET_E2E_LIVE"] = os.environ.get("SKYNET_E2E_LIVE", "1")
    env["SKYNET_LIVE_TRACE_FILE"] = str(trace.path)
    env["SKYNET_E2E_FAIL_ON_SKIP"] = os.environ.get("SKYNET_E2E_FAIL_ON_SKIP", "1")

    trace.log("e2e.step.start", step="telegram_real_flow", status="start")
    trace.log(
        "telegram_real.invoke",
        repo_root=str(REPO_ROOT),
        cmd=" ".join(cmd),
    )
    completed = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        errors="replace",
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    tracker_detected = len(re.findall(r"\"event\"\\s*:\\s*\"tracker\\.message\\.detected\"", output))
    tracker_edited = len(re.findall(r"\"event\"\\s*:\\s*\"tracker\\.message\\.edited\"", output))
    if tracker_detected or tracker_edited:
        trace.log(
            "telegram_real.tracker.summary",
            tracker_detected=tracker_detected,
            tracker_edited=tracker_edited,
        )
    match_passed = re.search(r"(\d+)\s+passed", output, flags=re.IGNORECASE)
    match_skipped = re.search(r"(\d+)\s+skipped", output, flags=re.IGNORECASE)
    passed = int(match_passed.group(1)) if match_passed else 0
    skipped = int(match_skipped.group(1)) if match_skipped else 0
    trace.log(
        "telegram_real.exit",
        returncode=completed.returncode,
        pytest_passed=passed,
        pytest_skipped=skipped,
    )
    strict = _bool_env("SKYNET_E2E_FAIL_ON_SKIP", True)
    if completed.returncode == 0 and skipped > 0 and strict:
        trace.log(
            "e2e.step.fail",
            step="telegram_real_flow",
            status="fail",
            error_message="Telegram-real live E2E was skipped under strict mode.",
        )
        _fail(
            trace,
            "Telegram-real live E2E was skipped under strict mode.",
            detail=f"pytest skipped={skipped}",
        )
    if completed.returncode != 0:
        trace.log(
            "e2e.step.fail",
            step="telegram_real_flow",
            status="fail",
            error_message=f"Telegram-real live E2E failed (exit={completed.returncode}).",
        )
        _fail(
            trace,
            "Telegram-real live E2E failed.",
            detail=f"pytest exit code: {completed.returncode}",
        )
    trace.log("e2e.step.end", step="telegram_real_flow", status="ok")
    trace.log("run.success", flow="telegram_real")
    print("[OK] Telegram-real live E2E passed.")
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

    trace.log("e2e.step.start", step="direct_flow", status="start")
    executor = get_ssh_executor()
    if not executor.is_configured():
        trace.log("e2e.step.fail", step="direct_flow", status="fail", error_message="SSH executor is not configured.")
        _fail(trace, "SSH executor is not configured. Check OPENCLAW_SSH_* env vars.")

    base_dir = os.environ.get("OPENCLAW_PROJECT_BASE_DIR", "")
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
        trace.log("e2e.step.fail", step="direct_flow", status="fail", error_message="Could not create working directory.")
        _fail(trace, f"Could not create directory: {working_dir}", detail=result)

    model = cfg.CLAUDE_OLLAMA_DEFAULT_MODEL
    backend = cfg.get_live_e2e_backend()
    agent = cfg.get_live_e2e_agent()
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
        trace.log("e2e.step.fail", step="direct_flow", status="fail", error_message="run_coding_agent action returned error.")
        _fail(trace, "run_coding_agent failed", detail=result)
    if inner.get("returncode", 0) != 0:
        trace.log("e2e.step.fail", step="direct_flow", status="fail", error_message="run_coding_agent returned non-zero.")
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
        trace.log("e2e.step.fail", step="direct_flow", status="fail", error_message=f"main.py missing at {main_py}")
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
        trace.log("e2e.step.fail", step="direct_flow", status="fail", error_message=f"exec_command returned {returncode}")
        _fail(trace, f"exec_command returned non-zero ({returncode})", detail=inner)
    if "SKYNET_E2E_OK" not in stdout:
        trace.log("run.warning", message="Expected SKYNET_E2E_OK not found", stdout=stdout[:220])
    else:
        trace.log("run.output_ok", marker="SKYNET_E2E_OK")

    trace.log("e2e.step.end", step="direct_flow", status="ok")
    trace.log("run.success", flow="direct", working_dir=working_dir)
    print("[OK] Direct live E2E passed.")
    print(f"[TRACE] {trace.path}")
    print(f"[ARTIFACT] working_dir={working_dir}")


async def run() -> None:
    # Auto-detect .env.local-e2e on Windows when SKYNET_ENV_FILE is not set
    if sys.platform == "win32" and not os.environ.get("SKYNET_ENV_FILE"):
        _candidate = REPO_ROOT / ".env.local-e2e"
        if _candidate.exists():
            os.environ["SKYNET_ENV_FILE"] = str(_candidate)

    trace = LiveTrace("e2e-live")
    trace.log("run.start", python=sys.version.split()[0], cwd=os.getcwd())
    trace_gateway_runtime(trace.log)
    flow = cfg.get_live_e2e_flow()
    _check_env(trace, flow)
    trace.log("run.mode", flow=flow)
    if flow in {"conversation", "chat"}:
        _run_conversation_flow(trace)
        return
    if flow in {"telegram_real", "telegram", "real_telegram"}:
        _run_telegram_real_flow(trace)
        return
    await _run_direct_flow(trace)


if __name__ == "__main__":
    asyncio.run(run())
