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
import importlib
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

from live_settings import auto_detect_env_file, build_gateway_runtime_env, trace_gateway_runtime
from live_diagnostics import (
    LiveRunCleanupManager,
    LiveTrace,
    fetch_local_gateway_status,
    fetch_remote_gateway_status,
)


PROMPT = (
    "Create a minimal Python project with a single file called main.py. "
    "The script should print exactly: SKYNET_E2E_OK\n"
    "No dependencies, no imports beyond stdlib. "
    "Output only the file in a fenced code block: ```main.py"
)

SLUG = f"e2e-test-{int(time.time())}"
cfg: Any | None = None


def _get_cfg(*, reload_module: bool = False) -> Any:
    global cfg
    if cfg is None:
        cfg = importlib.import_module("config")
    elif reload_module:
        cfg = importlib.reload(cfg)
    return cfg


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


def _check_env(trace: LiveTrace, flow: str = "conversation", policy: dict[str, Any] | None = None) -> None:
    cfg_module = _get_cfg()
    live_policy = dict(policy or cfg_module.get_live_e2e_policy(flow))
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
        required_transport=str(live_policy.get("required_transport") or ""),
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

    if flow not in {"direct"} and str(live_policy.get("required_transport") or "") == "websocket_primary":
        exec_mode = (os.environ.get("OPENCLAW_EXECUTION_MODE") or "").strip().lower()
        if exec_mode in {"ssh", "ssh_tunnel", "tunnel", "ssh-only"}:
            detail = (
                "Live E2E policy requires websocket_primary, but OPENCLAW_EXECUTION_MODE "
                f"is forced to '{exec_mode}'."
            )
            trace.log("env.mismatch", reason="forced_ssh_under_websocket_policy", detail=detail)
            if strict:
                _fail(trace, "Live E2E environment validation failed.", detail=detail)
            print(f"[SKIP] {detail}")
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


def _apply_live_policy_env(env: dict[str, str], flow: str) -> dict[str, str]:
    runtime_env = _get_cfg().get_live_e2e_runtime_env(flow)
    for key, value in runtime_env.items():
        env[key] = str(value)
    return env


def _worker_bootstrap_log_path() -> Path:
    return REPO_ROOT / "logs" / "worker-agent.bootstrap.log"


def _read_worker_bootstrap_log_tail(*, max_chars: int = 4000) -> str:
    path = _worker_bootstrap_log_path()
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"<could not read {path}: {type(exc).__name__}: {exc}>"
    return text[-max_chars:]


def _resolve_worker_bootstrap_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    script = Path(str(config.get("script") or "").strip())
    env_file = Path(str(config.get("env_file") or "").strip())
    if not script.is_absolute():
        script = REPO_ROOT / script
    if not env_file.is_absolute():
        env_file = REPO_ROOT / env_file
    return script, env_file


def _select_worker_bootstrap_shell(script: Path) -> str:
    if script.suffix.lower() == ".ps1":
        return "powershell" if sys.platform == "win32" else "pwsh"
    return str(script)


def _worker_status_missing_agents(status_payload: dict[str, Any], required_agents: list[str]) -> list[str]:
    coding_agents = status_payload.get("coding_agents") or {}
    available = {str(name).strip().lower() for name in dict(coding_agents).keys()}
    missing: list[str] = []
    for raw in required_agents:
        agent = str(raw or "").strip().lower()
        if agent and agent not in available:
            missing.append(agent)
    return missing


async def _fetch_gateway_status_for_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if str(policy.get("status_probe_mode") or "") == "remote_container_http":
        return await fetch_remote_gateway_status(
            container_name=str(policy.get("remote_gateway_container") or "openclaw-gateway"),
            status_url=str(policy.get("remote_status_url") or "http://localhost:8766/status"),
            diagnostics_profile=str(policy.get("diagnostics_profile") or ""),
        )
    cfg_module = _get_cfg()
    return await fetch_local_gateway_status(
        url=f"http://127.0.0.1:{int(getattr(cfg_module, 'HTTP_PORT', 8766) or 8766)}/status",
    )


def _worker_status_ready(policy: dict[str, Any], status_payload: dict[str, Any]) -> tuple[bool, list[str]]:
    required_transport = str(policy.get("required_transport") or "").strip().lower()
    actual_transport = str(status_payload.get("primary_transport_mode") or "").strip().lower()
    missing_agents = _worker_status_missing_agents(
        status_payload,
        list(policy.get("required_worker_agents") or []),
    )
    if not bool(status_payload.get("live_e2e_active", False)):
        return False, missing_agents
    if required_transport and actual_transport != required_transport:
        return False, missing_agents
    if required_transport == "websocket_primary":
        if not bool(status_payload.get("agent_connected", False)):
            return False, missing_agents
        if not bool(status_payload.get("websocket_health_ok", False)):
            return False, missing_agents
    if missing_agents:
        return False, missing_agents
    return True, []


async def _maybe_bootstrap_worker(
    trace: LiveTrace,
    cleanup: LiveRunCleanupManager,
    *,
    flow: str,
    policy: dict[str, Any],
) -> None:
    if flow in {"direct"}:
        trace.log("worker.bootstrap.skip", reason="direct_flow")
        return
    if str(policy.get("required_transport") or "").strip().lower() != "websocket_primary":
        trace.log("worker.bootstrap.skip", reason="transport_not_websocket_primary")
        return

    bootstrap_cfg = dict(policy.get("worker_bootstrap") or {})
    if not bool(bootstrap_cfg.get("enabled", True)):
        trace.log("worker.bootstrap.skip", reason="disabled")
        return

    try:
        status_payload = await _fetch_gateway_status_for_policy(policy)
    except Exception as exc:
        status_payload = {}
        trace.log(
            "worker.bootstrap.status",
            status="retry",
            error=f"{type(exc).__name__}: {exc}",
        )
    else:
        ready, missing_agents = _worker_status_ready(policy, status_payload)
        trace.log(
            "worker.bootstrap.status",
            status="ready" if ready else "pending",
            primary_transport_mode=str(status_payload.get("primary_transport_mode") or ""),
            agent_connected=bool(status_payload.get("agent_connected", False)),
            websocket_health_ok=bool(status_payload.get("websocket_health_ok", False)),
            missing_agents=missing_agents,
            coding_agents=sorted(dict(status_payload.get("coding_agents") or {}).keys()),
        )
        if ready:
            trace.log("worker.bootstrap.skip", reason="already_ready")
            return

    script_path, env_file = _resolve_worker_bootstrap_paths(bootstrap_cfg)
    if not script_path.exists():
        raise AssertionError(f"WORKER_BOOTSTRAP_CONFIG_ERROR: bootstrap script not found ({script_path})")
    if not env_file.exists():
        raise AssertionError(f"WORKER_BOOTSTRAP_CONFIG_ERROR: bootstrap env file not found ({env_file})")

    python_path = str(bootstrap_cfg.get("python_path") or "").strip() or sys.executable
    shell = _select_worker_bootstrap_shell(script_path)
    if script_path.suffix.lower() == ".ps1":
        cmd = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-RepoRoot",
            str(REPO_ROOT),
            "-EnvFile",
            str(env_file),
            "-PythonPath",
            python_path,
        ]
    else:
        cmd = [str(script_path), str(REPO_ROOT), str(env_file), python_path]

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    cleanup.register_subprocess(proc, label="worker_bootstrap")
    trace.log(
        "worker.bootstrap.start",
        pid=int(getattr(proc, "pid", 0) or 0),
        script=str(script_path),
        env_file=str(env_file),
        python_path=python_path,
        wait_seconds=int(bootstrap_cfg.get("wait_seconds") or 60),
        poll_seconds=int(bootstrap_cfg.get("poll_seconds") or 3),
    )

    deadline = time.monotonic() + max(10, int(bootstrap_cfg.get("wait_seconds") or 60))
    poll_seconds = max(1, int(bootstrap_cfg.get("poll_seconds") or 3))
    last_status: dict[str, Any] = {}
    last_error = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_tail = _read_worker_bootstrap_log_tail()
            raise AssertionError(
                "WORKER_BOOTSTRAP_FAILED: launcher exited early "
                f"(exit={int(proc.returncode or 0)}). {log_tail[-1500:] if log_tail else ''}"
            )
        try:
            status_payload = await _fetch_gateway_status_for_policy(policy)
            last_status = status_payload
            last_error = ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            trace.log("worker.bootstrap.status", status="retry", error=last_error)
            await asyncio.sleep(poll_seconds)
            continue

        ready, missing_agents = _worker_status_ready(policy, status_payload)
        trace.log(
            "worker.bootstrap.status",
            status="ready" if ready else "pending",
            primary_transport_mode=str(status_payload.get("primary_transport_mode") or ""),
            agent_connected=bool(status_payload.get("agent_connected", False)),
            websocket_health_ok=bool(status_payload.get("websocket_health_ok", False)),
            missing_agents=missing_agents,
            coding_agents=sorted(dict(status_payload.get("coding_agents") or {}).keys()),
        )
        if ready:
            trace.log(
                "worker.bootstrap.ready",
                pid=int(getattr(proc, "pid", 0) or 0),
                worker_id=str(status_payload.get("worker_id") or ""),
            )
            return
        await asyncio.sleep(poll_seconds)

    log_tail = _read_worker_bootstrap_log_tail()
    detail = {
        "last_error": last_error,
        "primary_transport_mode": str(last_status.get("primary_transport_mode") or ""),
        "agent_connected": bool(last_status.get("agent_connected", False)),
        "websocket_health_ok": bool(last_status.get("websocket_health_ok", False)),
        "coding_agents": sorted(dict(last_status.get("coding_agents") or {}).keys()),
        "log_tail": log_tail[-1500:] if log_tail else "",
    }
    raise AssertionError(f"WORKER_BOOTSTRAP_TIMEOUT: {detail}")


def _run_subprocess_with_cleanup(
    *,
    cmd: list[str],
    env: dict[str, str],
    cleanup: LiveRunCleanupManager,
    label: str,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
    )
    cleanup.register_subprocess(proc, label=label)
    try:
        stdout, stderr = proc.communicate()
    finally:
        cleanup.unregister_subprocess(proc)
    return subprocess.CompletedProcess(cmd, int(proc.returncode or 0), stdout, stderr)


def _run_conversation_flow(trace: LiveTrace, cleanup: LiveRunCleanupManager) -> None:
    target = (
        "openclaw-gateway/tests/test_e2e_conversation_live.py::"
        "test_live_conversation_real_planner_codegen_no_github_push"
    )
    cmd = [sys.executable, "-m", "pytest", target, "-q", "-s"]
    env = build_gateway_runtime_env(dict(os.environ))
    env["SKYNET_E2E_LIVE"] = os.environ.get("SKYNET_E2E_LIVE", "1")
    env["SKYNET_LIVE_TRACE_FILE"] = str(trace.path)
    env = _apply_live_policy_env(env, "conversation")

    trace.log("e2e.step.start", step="conversation_flow", status="start")
    trace.log(
        "conversation.invoke",
        repo_root=str(REPO_ROOT),
        cmd=" ".join(cmd),
    )
    completed = _run_subprocess_with_cleanup(
        cmd=cmd,
        env=env,
        cleanup=cleanup,
        label="conversation_pytest",
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


def _run_telegram_real_flow(trace: LiveTrace, cleanup: LiveRunCleanupManager) -> None:
    target = (
        "openclaw-gateway/tests/test_e2e_telegram_real_live.py::"
        "test_real_telegram_chat_flow_no_github_repo_creation"
    )
    cmd = [sys.executable, "-m", "pytest", target, "-q", "-s"]
    env = build_gateway_runtime_env(dict(os.environ))
    env["SKYNET_E2E_LIVE"] = os.environ.get("SKYNET_E2E_LIVE", "1")
    env["SKYNET_LIVE_TRACE_FILE"] = str(trace.path)
    env = _apply_live_policy_env(env, "telegram_real")

    trace.log("e2e.step.start", step="telegram_real_flow", status="start")
    trace.log(
        "telegram_real.invoke",
        repo_root=str(REPO_ROOT),
        cmd=" ".join(cmd),
    )
    completed = _run_subprocess_with_cleanup(
        cmd=cmd,
        env=env,
        cleanup=cleanup,
        label="telegram_real_pytest",
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
            **({"task_mode": "coding_implementation"} if agent == "qwen" else {}),
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
    auto_detect_env_file()

    trace = LiveTrace("e2e-live")
    trace.log("run.start", python=sys.version.split()[0], cwd=os.getcwd())
    trace_gateway_runtime(trace.log)
    cfg_module = _get_cfg(reload_module=True)
    flow = cfg_module.get_live_e2e_flow()
    policy = cfg_module.get_live_e2e_policy(flow)
    cleanup = LiveRunCleanupManager(
        trace_fn=trace.log,
        config_override=dict(policy.get("cleanup") or {}),
    )
    trace.log("run.policy", **policy)
    try:
        _check_env(trace, flow, policy)
        try:
            await _maybe_bootstrap_worker(trace, cleanup, flow=flow, policy=policy)
        except AssertionError as exc:
            trace.log(
                "e2e.step.fail",
                step="worker_bootstrap",
                status="fail",
                error_message=str(exc)[:400],
            )
            _fail(trace, "Live E2E worker bootstrap failed.", detail=str(exc))
        trace.log("run.mode", flow=flow)
        if flow in {"conversation", "chat"}:
            _run_conversation_flow(trace, cleanup)
            return
        if flow in {"telegram_real", "telegram", "real_telegram"}:
            _run_telegram_real_flow(trace, cleanup)
            return
        await _run_direct_flow(trace)
    finally:
        cleanup.cleanup(reason="run_exit")


if __name__ == "__main__":
    asyncio.run(run())
