from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


ACTIVE_RUNTIME_SESSIONS: dict[str, dict[str, Any]] = {}
ACTIVE_RUNTIME_SESSIONS_LOCK = asyncio.Lock()


async def register_runtime_session(session_key: str, **fields: Any) -> None:
    key = str(session_key or "").strip()
    if not key:
        return
    async with ACTIVE_RUNTIME_SESSIONS_LOCK:
        entry = dict(ACTIVE_RUNTIME_SESSIONS.get(key) or {})
        entry.update(fields)
        entry["session_key"] = key
        ACTIVE_RUNTIME_SESSIONS[key] = entry


async def get_runtime_session(session_key: str) -> dict[str, Any]:
    key = str(session_key or "").strip()
    async with ACTIVE_RUNTIME_SESSIONS_LOCK:
        entry = dict(ACTIVE_RUNTIME_SESSIONS.get(key) or {})
    return entry


async def pop_runtime_session(session_key: str) -> dict[str, Any]:
    key = str(session_key or "").strip()
    async with ACTIVE_RUNTIME_SESSIONS_LOCK:
        entry = dict(ACTIVE_RUNTIME_SESSIONS.pop(key, {}) or {})
    return entry


def artifact_snapshot(working_dir: str, *, max_files: int = 25) -> list[dict[str, Any]]:
    target = str(working_dir or "").strip()
    if not target or not os.path.isdir(target):
        return []
    rows: list[dict[str, Any]] = []
    base = Path(target)
    try:
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append(
                {
                    "path": str(path.relative_to(base)).replace("\\", "/"),
                    "size_bytes": int(stat.st_size),
                    "mtime": float(stat.st_mtime),
                }
            )
    except Exception:
        return []
    rows.sort(key=lambda item: item.get("mtime", 0.0), reverse=True)
    return rows[:max_files]


_SNAPSHOT_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}


def local_working_tree_snapshot(working_dir: str) -> dict[str, float]:
    """Return `{relative_path: mtime}` for files under `working_dir`."""
    target = str(working_dir or "").strip()
    if not target or not os.path.isdir(target):
        return {}
    snapshot: dict[str, float] = {}
    base = Path(target)
    try:
        for root, dirs, files in os.walk(base):
            dirs[:] = [directory for directory in dirs if directory not in _SNAPSHOT_SKIP_DIRS]
            for filename in files:
                path = Path(root) / filename
                try:
                    snapshot[str(path.relative_to(base)).replace("\\", "/")] = path.stat().st_mtime
                except OSError:
                    pass
    except Exception:
        pass
    return snapshot


def diff_local_snapshots(before: dict[str, float], after: dict[str, float]) -> list[str]:
    changed: list[str] = []
    for path, mtime in after.items():
        previous = before.get(path)
        if previous is None or mtime > previous:
            changed.append(path)
    return sorted(changed)


_CODE_BLOCK_LANG_EXT = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yaml",
    "bash": ".sh",
    "sh": ".sh",
    "html": ".html",
}
_CODE_BLOCK_KNOWN_FILENAMES = {"dockerfile", "makefile", "requirements.txt", "package.json"}


def persist_local_code_blocks(stdout: str, working_dir: str) -> list[str]:
    """Write markdown code blocks to disk when a model emitted code instead of tool calls."""
    if not stdout or not working_dir or not os.path.isdir(working_dir):
        return []
    pattern = re.compile(r"```([^\n`]+)\r?\n(.*?)```", re.DOTALL)
    written: list[str] = []
    base = Path(working_dir)
    for match in pattern.finditer(stdout):
        tag = match.group(1).strip()
        content = match.group(2)
        has_ext = "." in tag and "/" not in tag.split(".")[-1]
        has_path_sep = "/" in tag or "\\" in tag
        is_known = tag.lower() in _CODE_BLOCK_KNOWN_FILENAMES
        if has_ext or has_path_sep or is_known:
            filename = tag
        else:
            lang = tag.lower().split()[0] if tag else ""
            ext = _CODE_BLOCK_LANG_EXT.get(lang)
            if not ext:
                continue
            idx = len(written)
            filename = f"generated_{idx}{ext}" if idx > 0 else f"main{ext}"
        filename = filename.replace("\\", "/").lstrip("/")
        if ".." in filename:
            continue
        destination = base / filename
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            written.append(filename)
        except OSError:
            pass
    return written


def session_probe_payload(session: dict[str, Any], *, working_dir: str = "") -> dict[str, Any]:
    proc = session.get("proc")
    pid = ""
    status = str(session.get("status") or "")
    if proc is not None:
        pid = str(getattr(proc, "pid", "") or "")
        if getattr(proc, "returncode", None) is None and status != "cancelled":
            status = "running"
        elif getattr(proc, "returncode", None) is not None and status not in {"cancelled", "completed"}:
            status = "completed"
    artifact_dir = str(session.get("working_dir") or working_dir or "")
    artifacts = artifact_snapshot(artifact_dir, max_files=25)
    return {
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "remote_pid": pid,
        "process_tree": [
            {
                "pid": pid,
                "name": str(session.get("agent") or ""),
                "status": status,
                "working_dir": artifact_dir,
            }
        ]
        if pid
        else [],
        "prompt_file": {
            "path": "",
            "exists": False,
            "backend": "worker_agent",
        },
        "artifact_snapshot": artifacts,
        "artifact_count": len(artifacts),
        "python_validation_processes": [],
        "cleanup_status": str(session.get("cleanup_status") or ""),
    }


class TrackedPopen:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc

    @property
    def pid(self) -> int | None:
        return getattr(self._proc, "pid", None)

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def terminate(self) -> None:
        self._proc.terminate()

    def kill(self) -> None:
        self._proc.kill()

    async def wait(self) -> int:
        return await asyncio.to_thread(self._proc.wait)

    async def communicate(
        self, timeout: int | None = None, input: bytes | None = None,
    ) -> tuple[bytes, bytes]:
        return await asyncio.to_thread(self._proc.communicate, input=input, timeout=timeout)


async def run_tracked_coding_subprocess(
    *,
    args: list[str],
    cwd: str | None,
    timeout: int,
    session_key: str,
    agent: str,
    env: dict[str, str] | None = None,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    proc = TrackedPopen(
        subprocess.Popen(
            args,
            stdin=subprocess.PIPE if stdin_data else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
    )
    await register_runtime_session(
        session_key,
        proc=proc,
        agent=agent,
        status="running",
        started_at=time.time(),
        working_dir=str(cwd or ""),
        remote_pid=str(getattr(proc, "pid", "") or ""),
        cleanup_status="",
    )
    try:
        input_bytes = stdin_data.encode("utf-8") if stdin_data else None
        stdout_bytes, stderr_bytes = await proc.communicate(
            timeout=timeout, input=input_bytes,
        )
        status = "completed"
        cleanup_status = "completed"
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_bytes, stderr_bytes = await proc.communicate()
        status = "timed_out"
        cleanup_status = f"timed_out_after_{timeout}s"
    result = {
        "returncode": proc.returncode if status != "timed_out" else -1,
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "session_key": session_key,
        "remote_pid": str(getattr(proc, "pid", "") or ""),
    }
    if status == "timed_out":
        timeout_note = f"Process timed out after {timeout}s and was killed."
        result["stderr"] = f"{timeout_note}\n{result['stderr']}".strip()
    await register_runtime_session(
        session_key,
        status=status,
        cleanup_status=cleanup_status,
        returncode=int(result["returncode"]),
        stdout_tail=str(result["stdout"] or "")[-2000:],
        stderr_tail=str(result["stderr"] or "")[-2000:],
        finished_at=time.time(),
    )
    await pop_runtime_session(session_key)
    return result
