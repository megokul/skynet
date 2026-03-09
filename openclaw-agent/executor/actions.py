"""
CHATHAN Worker — Action Executors

Each public function in this module corresponds to exactly one permitted
action.  Functions receive validated, sanitised parameters and return a
plain dict that is serialised to JSON and sent back to the gateway.

SECURITY INVARIANTS
  - No function calls ``os.system``, ``eval``, ``exec``, or
    ``subprocess.Popen`` with user-controlled command strings.
  - Every ``subprocess`` invocation uses a **fixed argument list**.
  - Path parameters have already passed the jail check in the validator;
    they are used only as working-directory or target-file arguments.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import logging
import shutil
import subprocess
import sys
import time
import uuid
from html import unescape
from pathlib import Path
from urllib import parse, request
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skynet.settings.loader import get_component_settings  # noqa: E402

_settings_loader = get_component_settings("agent")


def _cfg_s(name: str, default: str = "") -> str:
    return _settings_loader.get_str(name, default)


def _cfg_b(name: str, default: bool = False) -> bool:
    return _settings_loader.get_bool(name, default)

logger = logging.getLogger("chathan.executor")

# Upper bound on how long any single subprocess may run (seconds).
_SUBPROCESS_TIMEOUT = 120
_ACTIVE_RUNTIME_SESSIONS: dict[str, dict[str, Any]] = {}
_ACTIVE_RUNTIME_SESSIONS_LOCK = asyncio.Lock()


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        text = _cfg_s(name).strip()
        if text:
            return text
    return default


def _env_bool(*names: str, default: bool = False) -> bool:
    for name in names:
        text = _cfg_s(name).strip()
        if not text:
            continue
        return _cfg_b(name, default)
    return default


async def _register_runtime_session(session_key: str, **fields: Any) -> None:
    key = str(session_key or "").strip()
    if not key:
        return
    async with _ACTIVE_RUNTIME_SESSIONS_LOCK:
        entry = dict(_ACTIVE_RUNTIME_SESSIONS.get(key) or {})
        entry.update(fields)
        entry["session_key"] = key
        _ACTIVE_RUNTIME_SESSIONS[key] = entry


async def _get_runtime_session(session_key: str) -> dict[str, Any]:
    key = str(session_key or "").strip()
    async with _ACTIVE_RUNTIME_SESSIONS_LOCK:
        entry = dict(_ACTIVE_RUNTIME_SESSIONS.get(key) or {})
    return entry


async def _pop_runtime_session(session_key: str) -> dict[str, Any]:
    key = str(session_key or "").strip()
    async with _ACTIVE_RUNTIME_SESSIONS_LOCK:
        entry = dict(_ACTIVE_RUNTIME_SESSIONS.pop(key, {}) or {})
    return entry


def _artifact_snapshot(working_dir: str, *, max_files: int = 25) -> list[dict[str, Any]]:
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


def _local_working_tree_snapshot(working_dir: str) -> dict[str, float]:
    """Return {relative_path: mtime} for all files under *working_dir*."""
    target = str(working_dir or "").strip()
    if not target or not os.path.isdir(target):
        return {}
    snap: dict[str, float] = {}
    base = Path(target)
    try:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SNAPSHOT_SKIP_DIRS]
            for fname in files:
                fp = Path(root) / fname
                try:
                    snap[str(fp.relative_to(base)).replace("\\", "/")] = fp.stat().st_mtime
                except OSError:
                    pass
    except Exception:
        pass
    return snap


def _diff_local_snapshots(
    before: dict[str, float], after: dict[str, float]
) -> list[str]:
    """Return paths that are new or modified between two snapshots."""
    changed: list[str] = []
    for path, mtime in after.items():
        prev = before.get(path)
        if prev is None or mtime > prev:
            changed.append(path)
    return sorted(changed)


_CODE_BLOCK_LANG_EXT = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
    "typescript": ".ts", "ts": ".ts", "json": ".json", "yaml": ".yaml",
    "yml": ".yaml", "bash": ".sh", "sh": ".sh", "html": ".html",
}
_CODE_BLOCK_KNOWN_FILENAMES = {"dockerfile", "makefile", "requirements.txt", "package.json"}


def _persist_local_code_blocks(stdout: str, working_dir: str) -> list[str]:
    """Parse fenced code blocks from stdout and write files to *working_dir*.

    Handles models that output code in markdown blocks instead of using tool calls.
    Returns list of relative paths written.
    """
    if not stdout or not working_dir or not os.path.isdir(working_dir):
        return []
    pattern = re.compile(r"```([^\n`]+)\r?\n(.*?)```", re.DOTALL)
    written: list[str] = []
    base = Path(working_dir)
    for match in pattern.finditer(stdout):
        tag = match.group(1).strip()
        content = match.group(2)
        # Determine if tag is a filename or a language hint.
        has_ext = "." in tag and "/" not in tag.split(".")[-1]
        has_path_sep = "/" in tag or "\\" in tag
        is_known = tag.lower() in _CODE_BLOCK_KNOWN_FILENAMES
        if has_ext or has_path_sep or is_known:
            filename = tag
        else:
            # Language-only tag — generate a default filename.
            lang = tag.lower().split()[0] if tag else ""
            ext = _CODE_BLOCK_LANG_EXT.get(lang)
            if not ext:
                continue
            # Use a numbered default name.
            idx = len(written)
            filename = f"generated_{idx}{ext}" if idx > 0 else f"main{ext}"
        # Sanitize: no absolute paths, no ..
        filename = filename.replace("\\", "/").lstrip("/")
        if ".." in filename:
            continue
        dest = base / filename
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            written.append(filename)
        except OSError:
            pass
    return written


def _session_probe_payload(session: dict[str, Any], *, working_dir: str = "") -> dict[str, Any]:
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
    artifacts = _artifact_snapshot(artifact_dir, max_files=25)
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


# CLI resolution for local coding agents.
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
_WEB_SEARCH_TIMEOUT_SECONDS = 15


class _TrackedPopen:
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

    async def communicate(self, timeout: int | None = None) -> tuple[bytes, bytes]:
        return await asyncio.to_thread(self._proc.communicate, timeout=timeout)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _run(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: int = _SUBPROCESS_TIMEOUT,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Run a fixed argument list as an async subprocess.

    Returns a dict with ``returncode``, ``stdout``, and ``stderr``.
    """
    logger.debug("exec: %s  (cwd=%s)", args, cwd)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
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


def _require_param(params: dict[str, Any], key: str) -> str:
    """Extract a required string parameter or raise."""
    value = params.get(key)
    if not value or not isinstance(value, str):
        raise ValueError(f"Missing required parameter: '{key}'")
    return value


def _python_module_missing(result: dict[str, Any], module: str) -> bool:
    text = f"{result.get('stderr', '')}\n{result.get('stdout', '')}".lower()
    return f"no module named {module.lower()}" in text


# ------------------------------------------------------------------
# AUTO-tier actions
# ------------------------------------------------------------------

async def git_status(params: dict[str, Any]) -> dict[str, Any]:
    """Run ``git status`` in the given project directory."""
    cwd = _require_param(params, "working_dir")
    return await _run(["git", "status", "--porcelain"], cwd=cwd)


async def run_tests(params: dict[str, Any]) -> dict[str, Any]:
    """
    Run the project test suite.

    Supports ``runner`` = "pytest" | "npm" (default: pytest).
    """
    cwd = _require_param(params, "working_dir")
    runner = params.get("runner", "pytest")

    if runner == "pytest":
        result = await _run(["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
        if result["returncode"] != 0 and _python_module_missing(result, "pytest"):
            install = await _run(
                ["python", "-m", "pip", "install", "pytest"],
                cwd=cwd,
                timeout=300,
            )
            if install["returncode"] != 0:
                detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                base_err = str(result.get("stderr") or "").strip()
                result["stderr"] = f"{base_err}\nPYTEST_SETUP_ERROR: {detail}".strip()
                return result
            retry = await _run(["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
            retry["stdout"] = f"Auto-installed pytest.\n{retry.get('stdout', '')}".strip()
            return retry
        return result
    elif runner == "npm":
        return await _run(["npm", "test"], cwd=cwd)
    else:
        return {"returncode": 1, "stdout": "", "stderr": f"Unknown runner: {runner}"}


async def lint_project(params: dict[str, Any]) -> dict[str, Any]:
    """
    Lint the project.

    Supports ``linter`` = "ruff" | "eslint" (default: ruff).
    """
    cwd = _require_param(params, "working_dir")
    linter = params.get("linter", "ruff")

    if linter == "ruff":
        result = await _run(["python", "-m", "ruff", "check", "."], cwd=cwd)
        if result["returncode"] != 0 and _python_module_missing(result, "ruff"):
            install = await _run(
                ["python", "-m", "pip", "install", "ruff"],
                cwd=cwd,
                timeout=300,
            )
            if install["returncode"] != 0:
                detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                base_err = str(result.get("stderr") or "").strip()
                result["stderr"] = f"{base_err}\nRUFF_SETUP_ERROR: {detail}".strip()
                return result
            retry = await _run(["python", "-m", "ruff", "check", "."], cwd=cwd)
            retry["stdout"] = f"Auto-installed ruff.\n{retry.get('stdout', '')}".strip()
            return retry
        return result
    elif linter == "eslint":
        return await _run(["npx", "eslint", "."], cwd=cwd)
    else:
        return {"returncode": 1, "stdout": "", "stderr": f"Unknown linter: {linter}"}


async def start_dev_server(params: dict[str, Any]) -> dict[str, Any]:
    """
    Start a dev server (non-blocking — returns immediately).

    Supports ``framework`` = "npm" | "uvicorn" (default: npm).
    """
    cwd = _require_param(params, "working_dir")
    framework = params.get("framework", "npm")

    if framework == "npm":
        # Fire-and-forget — just confirm it launched.
        proc = await asyncio.create_subprocess_exec(
            "npm", "run", "dev",
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return {"returncode": 0, "stdout": f"Dev server started (pid={proc.pid}).", "stderr": ""}
    elif framework == "uvicorn":
        app_module = params.get("app_module", "main:app")
        proc = await asyncio.create_subprocess_exec(
            "python", "-m", "uvicorn", app_module, "--reload",
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return {"returncode": 0, "stdout": f"Uvicorn started (pid={proc.pid}).", "stderr": ""}
    else:
        return {"returncode": 1, "stdout": "", "stderr": f"Unknown framework: {framework}"}


async def build_project(params: dict[str, Any]) -> dict[str, Any]:
    """
    Build the project.

    Supports ``build_tool`` = "npm" | "python" (default: npm).
    """
    cwd = _require_param(params, "working_dir")
    tool = params.get("build_tool", "npm")

    if tool == "npm":
        return await _run(["npm", "run", "build"], cwd=cwd)
    elif tool == "python":
        return await _run(["python", "-m", "build"], cwd=cwd)
    else:
        return {"returncode": 1, "stdout": "", "stderr": f"Unknown build tool: {tool}"}


async def exec_command(params: dict[str, Any]) -> dict[str, Any]:
    """
    Run a project script in a working directory.

    Accepted forms:
      command = "python script.py"   → runs the named Python script
      command = "node script.js"     → runs the named Node script

    Only ``python``, ``python3``, and ``node`` are permitted as the
    interpreter; arbitrary shell commands are rejected.
    """
    cwd = _require_param(params, "working_dir")
    argv = params.get("argv")
    parts: list[str]
    if isinstance(argv, list):
        parts = [str(item) for item in argv if str(item)]
        if not parts:
            return {"returncode": 1, "stdout": "", "stderr": "Empty argv."}
    else:
        command = _require_param(params, "command")
        parts = command.strip().split()
        if not parts:
            return {"returncode": 1, "stdout": "", "stderr": "Empty command."}

    interpreter = parts[0].lower().rstrip(".exe")
    if interpreter not in ("python", "python3", "node"):
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": (
                f"Interpreter {parts[0]!r} is not allowed. "
                "Use python, python3, or node."
            ),
        }

    # Normalise to the current interpreter so the script runs in the
    # same venv as the agent itself when python/python3 is requested.
    import sys
    actual = sys.executable if interpreter in ("python", "python3") else "node"
    return await _run([actual] + parts[1:], cwd=cwd)


async def file_read(params: dict[str, Any]) -> dict[str, Any]:
    """Read the contents of a file (path-jailed, 64 KB cap)."""
    filepath = _require_param(params, "file")
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, _read_file_sync, filepath)
        return {"returncode": 0, "stdout": content, "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


def _read_file_sync(filepath: str) -> str:
    """
    Read file sync.
    
    Purpose:
    - Implement `_read_file_sync` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `filepath`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    if len(content) > 65536:
        return content[:65536] + "\n... (truncated at 64 KB)"
    return content


async def list_directory(params: dict[str, Any]) -> dict[str, Any]:
    """List files and subdirectories (path-jailed)."""
    directory = _require_param(params, "directory")
    recursive = params.get("recursive", False) is True
    loop = asyncio.get_running_loop()
    try:
        listing = await loop.run_in_executor(
            None, _list_dir_sync, directory, recursive, 0,
        )
        return {"returncode": 0, "stdout": listing, "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


async def web_search(params: dict[str, Any]) -> dict[str, Any]:
    """Search the web from the laptop worker (Brave API with DDG fallback)."""
    query = _require_param(params, "query")
    raw_num = params.get("num_results", 5)
    try:
        num_results = int(raw_num)
    except (TypeError, ValueError):
        num_results = 5
    num_results = min(max(num_results, 1), 10)

    loop = asyncio.get_running_loop()
    if _BRAVE_SEARCH_API_KEY:
        try:
            output = await loop.run_in_executor(
                None, _brave_web_search_sync, query, num_results, _BRAVE_SEARCH_API_KEY,
            )
            return {"returncode": 0, "stdout": output, "stderr": ""}
        except Exception as exc:
            logger.warning("Brave web search failed: %s; falling back to DDG", exc)

    try:
        output = await loop.run_in_executor(None, _ddg_web_search_sync, query, num_results)
        return {"returncode": 0, "stdout": output, "stderr": ""}
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"Web search failed: {exc}"}


def _brave_web_search_sync(query: str, num_results: int, api_key: str) -> str:
    """
    Brave web search sync.
    
    Purpose:
    - Implement `_brave_web_search_sync` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `query`: input used by this function to compute or route work.
    - `num_results`: input used by this function to compute or route work.
    - `api_key`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    url = (
        "https://api.search.brave.com/res/v1/web/search?"
        f"q={parse.quote_plus(query)}&count={num_results}"
    )
    req = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
            "User-Agent": "SKYNET-Worker/1.0",
        },
    )
    with request.urlopen(req, timeout=_WEB_SEARCH_TIMEOUT_SECONDS) as resp:
        payload = resp.read().decode("utf-8", errors="replace")
    data = json.loads(payload)
    results = data.get("web", {}).get("results", []) if isinstance(data, dict) else []
    if not results:
        return "No results found."

    lines: list[str] = []
    for i, item in enumerate(results[:num_results], 1):
        title = str(item.get("title", "No title")).strip()
        link = str(item.get("url", "")).strip()
        desc = str(item.get("description", "No description")).strip()
        lines.append(f"{i}. {title}\n   URL: {link}\n   {desc}\n")
    return "\n".join(lines)


def _ddg_web_search_sync(query: str, num_results: int) -> str:
    """
    Ddg web search sync.
    
    Purpose:
    - Implement `_ddg_web_search_sync` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `query`: input used by this function to compute or route work.
    - `num_results`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    url = f"https://lite.duckduckgo.com/lite/?q={parse.quote_plus(query)}"
    req = request.Request(url, headers={"User-Agent": "SKYNET-Worker/1.0"})
    with request.urlopen(req, timeout=_WEB_SEARCH_TIMEOUT_SECONDS) as resp:
        page = resp.read().decode("utf-8", errors="replace")

    results: list[str] = []

    # DDG Lite result links vary in quote style/order:
    #   <a ... class='result-link' href='...'> OR href before class
    link_pattern = re.compile(
        r"<a(?=[^>]*class=['\"]result-link['\"])(?=[^>]*href=['\"]([^'\"]+)['\"])[^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in link_pattern.finditer(page):
        raw_link = (match.group(1) or "").strip()
        title_html = match.group(2) or ""
        if not raw_link:
            continue
        link = _normalize_ddg_result_url(raw_link)
        if not link:
            continue
        title_text = unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
        if not title_text:
            title_text = "No title"
        results.append(f"- {title_text}\n  URL: {link}")
        if len(results) >= num_results:
            break

    if results:
        return "\n".join(results)

    # Loose fallback for non-standard markup.
    links = re.findall(
        r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        page,
        re.IGNORECASE | re.DOTALL,
    )
    for raw_link, title_html in links:
        link = _normalize_ddg_result_url(raw_link)
        if not link or "duckduckgo.com" in link:
            continue
        title_text = unescape(re.sub(r"<[^>]+>", "", title_html)).strip() or "No title"
        results.append(f"- {title_text}\n  URL: {link}")
        if len(results) >= num_results:
            break

    return "\n".join(results) if results else "No results found."


def _normalize_ddg_result_url(raw_link: str) -> str:
    """Extract real destination URL from DDG redirect links."""
    link = (raw_link or "").strip()
    if not link:
        return ""

    if link.startswith("//"):
        link = f"https:{link}"
    elif link.startswith("/"):
        link = f"https://duckduckgo.com{link}"

    parsed = parse.urlparse(link)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse.parse_qs(parsed.query).get("uddg", [])
        if uddg:
            return parse.unquote(uddg[0])
    return link


def _list_dir_sync(directory: str, recursive: bool, depth: int) -> str:
    """
    List dir sync.
    
    Purpose:
    - Implement `_list_dir_sync` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `directory`: input used by this function to compute or route work.
    - `recursive`: input used by this function to compute or route work.
    - `depth`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    MAX_DEPTH = 3
    MAX_ENTRIES = 500
    entries: list[str] = []
    count = 0
    for entry in sorted(os.scandir(directory), key=lambda e: e.name):
        if count >= MAX_ENTRIES:
            entries.append("... (truncated)")
            break
        prefix = "  " * depth
        if entry.is_dir():
            entries.append(f"{prefix}[DIR] {entry.name}/")
            if recursive and depth < MAX_DEPTH:
                entries.append(_list_dir_sync(entry.path, True, depth + 1))
        else:
            size = entry.stat().st_size
            entries.append(f"{prefix}{entry.name}  ({size} bytes)")
        count += 1
    return "\n".join(entries)


# ------------------------------------------------------------------
# CONFIRM-tier actions
# ------------------------------------------------------------------

async def git_commit(params: dict[str, Any]) -> dict[str, Any]:
    """Stage all changes and commit with the supplied message."""
    cwd = _require_param(params, "working_dir")
    message = _require_param(params, "message")

    # Stage tracked changes only (no untracked files).
    stage_result = await _run(["git", "add", "-u"], cwd=cwd)
    if stage_result["returncode"] != 0:
        return stage_result

    return await _run(["git", "commit", "-m", message], cwd=cwd)


async def install_dependencies(params: dict[str, Any]) -> dict[str, Any]:
    """
    Install project dependencies.

    Supports ``manager`` = "pip" | "npm" (default: pip).
    """
    cwd = _require_param(params, "working_dir")
    manager = params.get("manager", "pip")

    if manager == "pip":
        req_file = os.path.join(cwd, "requirements.txt")
        return await _run(
            ["python", "-m", "pip", "install", "-r", req_file],
            cwd=cwd,
            timeout=300,
        )
    elif manager == "npm":
        return await _run(["npm", "install"], cwd=cwd, timeout=300)
    else:
        return {"returncode": 1, "stdout": "", "stderr": f"Unknown manager: {manager}"}


async def file_write(params: dict[str, Any]) -> dict[str, Any]:
    """
    Write content to a file inside the allowed roots.

    The ``file`` parameter must already have passed the path-jail check.
    """
    filepath = _require_param(params, "file")
    content = params.get("content", "")

    if not isinstance(content, str):
        return {"returncode": 1, "stdout": "", "stderr": "content must be a string."}

    # Limit file size to 1 MB to prevent abuse.
    if len(content.encode("utf-8")) > 1_048_576:
        return {"returncode": 1, "stdout": "", "stderr": "Content exceeds 1 MB limit."}

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _write_file_sync, filepath, content)
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}

    return {"returncode": 0, "stdout": f"Wrote {len(content)} bytes to {filepath}.", "stderr": ""}


def _write_file_sync(filepath: str, content: str) -> None:
    """
    Write file sync.
    
    Purpose:
    - Implement `_write_file_sync` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `filepath`: input used by this function to compute or route work.
    - `content`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `None` when available; otherwise side effects only.
    """

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(content)


async def create_directory(params: dict[str, Any]) -> dict[str, Any]:
    """Create a directory (and any missing parents)."""
    directory = _require_param(params, "directory")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, os.makedirs, directory, 0o755, True)
        return {"returncode": 0, "stdout": f"Created {directory}", "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


async def delete_directory(params: dict[str, Any]) -> dict[str, Any]:
    """Remove a directory and all its contents recursively (rollback / cleanup use)."""
    directory = _require_param(params, "directory")
    loop = asyncio.get_running_loop()
    try:
        exists = await loop.run_in_executor(None, os.path.isdir, directory)
        if not exists:
            return {"returncode": 0, "stdout": f"Directory '{directory}' does not exist.", "stderr": ""}
        await loop.run_in_executor(None, shutil.rmtree, directory)
        return {"returncode": 0, "stdout": f"Deleted '{directory}'.", "stderr": ""}
    except OSError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


async def git_init(params: dict[str, Any]) -> dict[str, Any]:
    """Initialize a new git repository and set default branch to main."""
    cwd = _require_param(params, "working_dir")
    result = await _run(["git", "init"], cwd=cwd)
    if result["returncode"] == 0:
        await _run(["git", "checkout", "-b", "main"], cwd=cwd)
    return result


async def git_add_all(params: dict[str, Any]) -> dict[str, Any]:
    """Stage all changes including untracked files."""
    cwd = _require_param(params, "working_dir")
    return await _run(["git", "add", "-A"], cwd=cwd)


async def git_push(params: dict[str, Any]) -> dict[str, Any]:
    """Push to remote repository."""
    cwd = _require_param(params, "working_dir")
    remote = params.get("remote", "origin")
    branch = params.get("branch", "main")
    return await _run(["git", "push", "-u", remote, branch], cwd=cwd)


async def gh_create_repo(params: dict[str, Any]) -> dict[str, Any]:
    """Create a GitHub repository and set it as remote origin."""
    cwd = _require_param(params, "working_dir")
    repo_name = _require_param(params, "repo_name")
    description = params.get("description", "")
    private = params.get("private", False) is True

    if not re.match(r"^[a-zA-Z0-9._-]+$", repo_name):
        return {"returncode": 1, "stdout": "", "stderr": "Invalid repo name characters."}

    visibility = "--private" if private else "--public"
    args = ["gh", "repo", "create", repo_name, visibility, "--source=.", "--push"]
    if description:
        args.extend(["--description", description])

    return await _run(args, cwd=cwd, timeout=60)


async def open_in_vscode(params: dict[str, Any]) -> dict[str, Any]:
    """Open a path in VS Code."""
    path = _require_param(params, "path")
    return await _run(["code", path])


async def check_coding_agents(params: dict[str, Any]) -> dict[str, Any]:
    """Detect available coding agent CLIs on the laptop."""
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


def _resolve_coding_binary(name: str) -> tuple[str, str]:
    """Resolve configured binary path for a coding agent."""
    binary = _CODING_AGENT_BINARIES[name]
    if os.path.isabs(binary):
        if os.path.exists(binary):
            return binary, binary
        return "", binary
    resolved = shutil.which(binary)
    return (resolved or "", binary)


def _bool_param(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


async def _run_tracked_coding_subprocess(
    *,
    args: list[str],
    cwd: str | None,
    timeout: int,
    session_key: str,
    agent: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    proc = _TrackedPopen(
        subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
    )
    await _register_runtime_session(
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
        stdout_bytes, stderr_bytes = await proc.communicate(timeout=timeout)
        status = "completed"
        cleanup_status = "completed"
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_bytes, stderr_bytes = await proc.communicate()
        status = "timed_out"
        cleanup_status = f"timed_out_after_{timeout}s"
    result = {
        "returncode": proc.returncode if status != "timed_out" else -1,
        "stdout": stdout_bytes.decode("utf-8", errors="replace")[:8192],
        "stderr": stderr_bytes.decode("utf-8", errors="replace")[:4096],
        "session_key": session_key,
        "remote_pid": str(getattr(proc, "pid", "") or ""),
    }
    if status == "timed_out":
        timeout_note = f"Process timed out after {timeout}s and was killed."
        result["stderr"] = f"{timeout_note}\n{result['stderr']}".strip()
    await _register_runtime_session(
        session_key,
        status=status,
        cleanup_status=cleanup_status,
        returncode=int(result["returncode"]),
        stdout_tail=str(result["stdout"] or "")[-2000:],
        stderr_tail=str(result["stderr"] or "")[-2000:],
        finished_at=time.time(),
    )
    await _pop_runtime_session(session_key)
    return result


async def run_coding_agent(params: dict[str, Any]) -> dict[str, Any]:
    """
    Run a local coding agent CLI in non-interactive mode.

    Supports: codex, claude, cline, qwen
    """
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

    # Snapshot working directory before execution to detect written files.
    before_snapshot = _local_working_tree_snapshot(cwd) if cwd else {}

    if agent == "claude":
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

    # Detect files written to disk by diffing working tree snapshots.
    if cwd:
        after_snapshot = _local_working_tree_snapshot(cwd)
        written = _diff_local_snapshots(before_snapshot, after_snapshot)
        # Fallback: if no files detected but stdout has code blocks, parse and write them.
        if not written and int(result.get("returncode", 1)) == 0:
            block_written = _persist_local_code_blocks(
                str(result.get("stdout") or ""), cwd
            )
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


async def docker_build(params: dict[str, Any]) -> dict[str, Any]:
    """Build a Docker image from the project directory."""
    cwd = _require_param(params, "working_dir")
    tag = params.get("tag", "chathan-build:latest")

    if not re.match(r"^[a-zA-Z0-9._/:@-]+$", tag):
        return {"returncode": 1, "stdout": "", "stderr": "Invalid Docker tag characters."}

    return await _run(["docker", "build", "-t", tag, "."], cwd=cwd, timeout=600)


async def docker_compose_up(params: dict[str, Any]) -> dict[str, Any]:
    """Run ``docker compose up -d`` in the project directory."""
    cwd = _require_param(params, "working_dir")
    return await _run(["docker", "compose", "up", "-d"], cwd=cwd, timeout=300)


async def close_app(params: dict[str, Any]) -> dict[str, Any]:
    """
    Close an application by its friendly name.

    Only applications in config.CLOSEABLE_APPS can be terminated.
    Uses ``taskkill /F /IM <process.exe>`` with a fixed argument list.
    """
    from agent_config import CLOSEABLE_APPS

    app_name = _require_param(params, "app").lower()

    if app_name not in CLOSEABLE_APPS:
        allowed = ", ".join(sorted(CLOSEABLE_APPS.keys()))
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"'{app_name}' is not in the allowed list. Allowed: {allowed}",
        }

    exe_name = CLOSEABLE_APPS[app_name]
    return await _run(["taskkill", "/F", "/IM", exe_name])


async def zip_project(params: dict[str, Any]) -> dict[str, Any]:
    """
    Create a zip archive of a project directory and return as base64.

    Excludes heavy/generated directories: node_modules, __pycache__,
    .git, venv, .venv, dist, build.
    Cap: 10 MB after compression.
    """
    import base64
    import io
    import zipfile

    working_dir = _require_param(params, "working_dir")

    if not os.path.isdir(working_dir):
        return {"returncode": 1, "stdout": "", "stderr": f"Not a directory: {working_dir}"}

    EXCLUDE_DIRS = {"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build", ".next"}
    MAX_ZIP_SIZE = 10 * 1024 * 1024  # 10 MB

    buf = io.BytesIO()
    file_count = 0

    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(working_dir):
                # Skip excluded directories.
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for fname in files:
                    fpath = os.path.join(root, fname)
                    arcname = os.path.relpath(fpath, working_dir)
                    try:
                        zf.write(fpath, arcname)
                        file_count += 1
                    except (PermissionError, OSError):
                        continue  # Skip unreadable files.

                    # Check size periodically.
                    if buf.tell() > MAX_ZIP_SIZE:
                        return {
                            "returncode": 1,
                            "stdout": "",
                            "stderr": f"Zip exceeds {MAX_ZIP_SIZE // (1024*1024)} MB limit.",
                        }
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"Zip error: {exc}"}

    zip_bytes = buf.getvalue()
    encoded = base64.b64encode(zip_bytes).decode("ascii")

    return {
        "returncode": 0,
        "stdout": encoded,
        "stderr": f"Zipped {file_count} files ({len(zip_bytes)} bytes)",
    }


# ------------------------------------------------------------------
# Action registry — maps action name → executor function.
# The router uses this to dispatch; if an action is not in this dict
# it cannot be executed regardless of tier.
# ------------------------------------------------------------------

# Import Ollama handler from its own module.
from executor.ollama import ollama_chat


ACTION_REGISTRY: dict[str, Any] = {
    # AUTO
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
    # CONFIRM
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
