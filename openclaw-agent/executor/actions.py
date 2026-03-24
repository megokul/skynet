from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from executor.action_fs import (  # noqa: E402
    create_directory,
    delete_directory,
    file_read,
    file_write,
    list_directory,
    zip_project,
)
from executor.action_process import (  # noqa: E402
    build_project,
    close_app,
    docker_build,
    docker_compose_up,
    exec_command,
    gh_create_repo,
    git_add_all,
    git_commit,
    git_init,
    git_push,
    git_status,
    install_dependencies,
    lint_project,
    open_in_vscode,
    run_tests,
    start_dev_server,
)
from executor.action_search import web_search as _web_search  # noqa: E402
from executor.action_support import (  # noqa: E402
    python_module_missing as _python_module_missing,
    require_param as _require_param,
    run_subprocess as _run,
)
from executor.ollama import ollama_chat  # noqa: E402
from executor.qwen_runner import run_qwen_agent  # noqa: E402
from executor.runtime_sessions import (  # noqa: E402
    artifact_snapshot as _artifact_snapshot,
    diff_local_snapshots as _diff_local_snapshots,
    get_runtime_session as _get_runtime_session,
    local_working_tree_snapshot as _local_working_tree_snapshot,
    persist_local_code_blocks as _persist_local_code_blocks,
    pop_runtime_session as _pop_runtime_session,
    register_runtime_session as _register_runtime_session,
    run_tracked_coding_subprocess as _run_tracked_coding_subprocess,
    session_probe_payload as _session_probe_payload,
)
from skynet.settings.loader import get_component_settings  # noqa: E402


_settings_loader = get_component_settings("agent")
logger = logging.getLogger("chathan.executor")


def _cfg_s(name: str, default: str = "") -> str:
    return _settings_loader.get_str(name, default)


def _cfg_b(name: str, default: bool = False) -> bool:
    return _settings_loader.get_bool(name, default)


_CODING_AGENT_BINARIES: dict[str, str] = {
    "codex": _cfg_s("SKYNET_CODEX_BIN") or _cfg_s("OPENCLAW_CODEX_BIN", "codex"),
    "claude": _cfg_s("SKYNET_CLAUDE_BIN") or _cfg_s("OPENCLAW_CLAUDE_BIN", "claude"),
    "cline": _cfg_s("SKYNET_CLINE_BIN") or _cfg_s("OPENCLAW_CLINE_BIN", "cline"),
    "qwen": _cfg_s("SKYNET_QWEN_BIN") or _cfg_s("OPENCLAW_QWEN_BIN", "qwen"),
}
_CODING_AGENT_PREFIX_ARGS: dict[str, list[str]] = {
    "codex": ["exec", "--full-auto", "--skip-git-repo-check"],
    "claude": ["-p"],
    "cline": ["-p"],
    "qwen": ["--yolo", "-p"],
}
_CODING_AGENT_TIMEOUT_SECONDS = 1800
_BRAVE_SEARCH_API_KEY = _cfg_s("BRAVE_SEARCH_API_KEY")


async def web_search(params: dict[str, Any]) -> dict[str, Any]:
    return await _web_search(params, brave_api_key=_BRAVE_SEARCH_API_KEY)


async def check_coding_agents(params: dict[str, Any]) -> dict[str, Any]:
    del params
    lines = []
    for name, binary in _CODING_AGENT_BINARIES.items():
        resolved = shutil.which(binary)
        if resolved:
            lines.append(f"{name}: available ({resolved})")
        else:
            lines.append(f"{name}: unavailable (expected binary: {binary})")
    return {
        "returncode": 0,
        "stdout": "\n".join(lines),
        "stderr": "",
    }


def _resolve_cmd_to_node(cmd_path: str) -> list[str] | None:
    """Resolve an npm .cmd wrapper to [node, script.js].

    On Windows, subprocess.Popen with a .cmd file invokes cmd.exe which
    interprets | & < > in arguments as shell metacharacters, corrupting
    prompts that contain them.  Resolving to node.exe + the actual .js
    entry point bypasses cmd.exe entirely.
    """
    if not cmd_path or not cmd_path.lower().endswith((".cmd", ".bat")):
        return None
    try:
        body = Path(cmd_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # npm .cmd wrappers reference the .js entry point via %dp0% or %~dp0
    match = re.search(r'["\']?(?:%~dp0|%dp0%)[\\/ ]*([^"\r\n]+\.js)["\']?', body)
    if not match:
        return None
    script_rel = match.group(1).replace("\\", "/")
    script_abs = str(Path(cmd_path).parent / script_rel)
    if not os.path.isfile(script_abs):
        return None
    # Use the co-located node.exe if available, otherwise rely on PATH.
    co_node = str(Path(cmd_path).parent / "node.exe")
    node = co_node if os.path.isfile(co_node) else "node"
    return [node, script_abs]


def _resolve_coding_binary(name: str) -> tuple[str, str]:
    binary = _CODING_AGENT_BINARIES[name]
    if os.path.isabs(binary):
        if os.path.exists(binary):
            return binary, binary
        return "", binary
    resolved = shutil.which(binary)
    return (resolved or "", binary)


async def run_coding_agent(params: dict[str, Any]) -> dict[str, Any]:
    agent = _require_param(params, "agent").strip().lower()
    prompt = _require_param(params, "prompt")
    cwd = params.get("working_dir")
    timeout = params.get("timeout_seconds", _CODING_AGENT_TIMEOUT_SECONDS)
    session_key = str(params.get("session_key") or uuid.uuid4().hex).strip()
    model = str(params.get("model") or "").strip()

    if agent not in _CODING_AGENT_BINARIES:
        allowed = ", ".join(sorted(_CODING_AGENT_BINARIES.keys()))
        return {"returncode": 1, "stdout": "", "stderr": f"Unknown coding agent '{agent}'. Allowed: {allowed}"}
    if cwd is not None and not isinstance(cwd, str):
        return {"returncode": 1, "stdout": "", "stderr": "working_dir must be a string path."}
    if not isinstance(timeout, int) or timeout < 30 or timeout > 3600:
        return {"returncode": 1, "stdout": "", "stderr": "timeout_seconds must be an integer between 30 and 3600."}

    resolved, configured = _resolve_coding_binary(agent)
    if not resolved:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": (
                f"{agent} CLI not found (configured '{configured}'). "
                f"Set SKYNET_{agent.upper()}_BIN or OPENCLAW_{agent.upper()}_BIN to the executable path."
            ),
        }

    before_snapshot = _local_working_tree_snapshot(cwd) if cwd else {}

    if agent == "qwen":
        # On Windows, resolve .cmd wrappers to node + cli.js to avoid
        # cmd.exe interpreting | & < > in prompt text as metacharacters.
        node_args = _resolve_cmd_to_node(resolved) if sys.platform == "win32" else None
        result = await run_qwen_agent(
            params=params,
            resolved_binary=node_args[0] if node_args else resolved,
            cwd=cwd,
            timeout=timeout,
            session_key=session_key,
            model=model,
            get_str=_cfg_s,
            get_bool=_cfg_b,
            run_subprocess=_run_tracked_coding_subprocess,
            binary_prefix_args=node_args[1:] if node_args else None,
        )
    elif agent == "claude":
        args = [resolved]
        if model:
            args.extend(["--model", model])
        args.extend(["-p", prompt])
        result = await _run_tracked_coding_subprocess(
            args=args,
            cwd=cwd,
            timeout=timeout,
            session_key=session_key,
            agent=agent,
        )
    else:
        args = [resolved, *_CODING_AGENT_PREFIX_ARGS[agent], prompt]
        result = await _run_tracked_coding_subprocess(
            args=args,
            cwd=cwd,
            timeout=timeout,
            session_key=session_key,
            agent=agent,
        )

    if cwd:
        after_snapshot = _local_working_tree_snapshot(cwd)
        written = _diff_local_snapshots(before_snapshot, after_snapshot)
        if not written and int(result.get("returncode", 1)) == 0:
            block_written = _persist_local_code_blocks(str(result.get("stdout") or ""), cwd)
            if block_written:
                written = block_written
        if written:
            result["files_written"] = written

    return result


async def trace_runtime_probe(params: dict[str, Any]) -> dict[str, Any]:
    session_key = _require_param(params, "session_key")
    session = await _get_runtime_session(session_key)
    if not session:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"No active session found for {session_key}",
            "session_key": session_key,
            "artifact_snapshot": _artifact_snapshot(str(params.get("working_dir") or ""), max_files=25),
            "artifact_count": 0,
            "process_tree": [],
            "prompt_file": {"path": "", "exists": False, "backend": "worker_agent"},
            "remote_pid": "",
            "python_validation_processes": [],
        }
    payload = _session_probe_payload(session, working_dir=str(params.get("working_dir") or ""))
    payload["session_key"] = session_key
    return payload


async def cancel_runtime_session(params: dict[str, Any]) -> dict[str, Any]:
    session_key = _require_param(params, "session_key")
    session = await _get_runtime_session(session_key)
    if not session:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"No active session found for {session_key}",
            "session_key": session_key,
            "process_tree": [],
            "artifact_snapshot": _artifact_snapshot(str(params.get("working_dir") or ""), max_files=25),
            "artifact_count": 0,
            "prompt_file": {"path": "", "exists": False, "backend": "worker_agent"},
            "remote_pid": "",
            "cleanup_status": "missing",
        }
    proc = session.get("proc")
    cleanup_status = "already_exited"
    if proc is not None and getattr(proc, "returncode", None) is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
            cleanup_status = "terminated"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            cleanup_status = "killed"
    await _register_runtime_session(session_key, status="cancelled", cleanup_status=cleanup_status)
    updated = await _get_runtime_session(session_key)
    payload = _session_probe_payload(updated, working_dir=str(params.get("working_dir") or ""))
    payload["session_key"] = session_key
    payload["cleanup_status"] = cleanup_status
    await _pop_runtime_session(session_key)
    return payload


ACTION_REGISTRY: dict[str, Any] = {
    "git_status": git_status,
    "web_search": web_search,
    "run_tests": run_tests,
    "lint_project": lint_project,
    "start_dev_server": start_dev_server,
    "build_project": build_project,
    "file_read": file_read,
    "list_directory": list_directory,
    "ollama_chat": ollama_chat,
    "check_coding_agents": check_coding_agents,
    "trace_runtime_probe": trace_runtime_probe,
    "cancel_runtime_session": cancel_runtime_session,
    "git_commit": git_commit,
    "install_dependencies": install_dependencies,
    "file_write": file_write,
    "create_directory": create_directory,
    "delete_directory": delete_directory,
    "git_init": git_init,
    "git_add_all": git_add_all,
    "git_push": git_push,
    "gh_create_repo": gh_create_repo,
    "open_in_vscode": open_in_vscode,
    "run_coding_agent": run_coding_agent,
    "exec_command": exec_command,
    "docker_build": docker_build,
    "docker_compose_up": docker_compose_up,
    "close_app": close_app,
    "zip_project": zip_project,
}
