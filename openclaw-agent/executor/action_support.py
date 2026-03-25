from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Any


logger = logging.getLogger("chathan.executor")
SUBPROCESS_TIMEOUT = 120


def _kill_tree(pid: int) -> None:
    """Kill a process and all its children (best-effort)."""
    if sys.platform == "win32":
        # taskkill /T kills the entire process tree
        os.system(f"taskkill /F /T /PID {pid} >nul 2>&1")
    else:
        # Send SIGKILL to the process group
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


async def run_subprocess(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: int = SUBPROCESS_TIMEOUT,
    env: dict[str, str] | None = None,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    """Run a fixed argument list and capture bounded stdout/stderr."""
    logger.debug("exec: %s  (cwd=%s)", args, cwd)
    # start_new_session so we can kill the entire process group on timeout
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_data else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        input_bytes = stdin_data.encode("utf-8") if stdin_data else None
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=input_bytes), timeout=timeout
        )
    except asyncio.TimeoutError:
        _kill_tree(proc.pid)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Process timed out after {timeout}s and was killed.",
        }

    return {
        "returncode": proc.returncode,
        "stdout": stdout_bytes.decode("utf-8", errors="replace")[:8192],
        "stderr": stderr_bytes.decode("utf-8", errors="replace")[:4096],
    }


def require_param(params: dict[str, Any], key: str) -> str:
    """Return a required string parameter or raise `ValueError`."""
    value = params.get(key)
    if not value or not isinstance(value, str):
        raise ValueError(f"Missing required parameter: '{key}'")
    return value


def python_module_missing(result: dict[str, Any], module: str) -> bool:
    text = f"{result.get('stderr', '')}\n{result.get('stdout', '')}".lower()
    return f"no module named {module.lower()}" in text
