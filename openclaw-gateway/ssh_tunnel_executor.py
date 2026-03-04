"""
SKYNET Gateway - SSH Tunnel Action Executor

Fallback execution path when no OpenClaw worker is connected.
Runs allowlisted actions directly on a remote laptop over SSH.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import base64
import threading
import time
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import paramiko

_log = logging.getLogger(__name__)

import config as bot_cfg
from search.web_search import WebSearcher


def _env_bool(name: str, default: bool = False) -> bool:
    """
    Env bool.
    
    Purpose:
    - Implement `_env_bool` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `name`: input used by this function to compute or route work.
    - `default`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `bool` when available; otherwise side effects only.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """
    Env int.
    
    Purpose:
    - Implement `_env_int` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `name`: input used by this function to compute or route work.
    - `default`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `int` when available; otherwise side effects only.
    """

    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _looks_like_ssh_infra_error(detail: str) -> bool:
    text = (detail or "").strip().lower()
    if not text:
        return False
    tokens = (
        "ssh",
        "timed out",
        "timeout",
        "banner",
        "authentication",
        "permission denied",
        "connection refused",
        "no route to host",
        "could not resolve",
        "maxstartups",
        "concurrency limit reached",
        "max_parallel",
        "no existing session",
        "name or service not known",
        "network is unreachable",
    )
    return any(token in text for token in tokens)


def _python_module_missing(result: dict[str, Any], module: str) -> bool:
    text = f"{result.get('stderr', '')}\n{result.get('stdout', '')}".lower()
    return f"no module named {module.lower()}" in text


def _parse_roots(raw: str, remote_os: str) -> list[str]:
    """
    Parse roots.
    
    Purpose:
    - Implement `_parse_roots` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `raw`: input used by this function to compute or route work.
    - `remote_os`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[str]` when available; otherwise side effects only.
    """

    parts = [p.strip() for p in raw.replace(",", ";").split(";") if p.strip()]
    if parts:
        return parts
    # No OPENCLAW_SSH_ALLOWED_ROOTS configured - use safe OS-level defaults.
    # Set OPENCLAW_SSH_ALLOWED_ROOTS to restrict access to specific directories.
    if remote_os == "windows":
        return [r"%USERPROFILE%\Projects", r"%USERPROFILE%\Documents"]
    return ["/home", "/tmp"]


def _parse_provider_priority(raw: str) -> list[str]:
    """
    Parse provider priority.
    
    Purpose:
    - Implement `_parse_provider_priority` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `raw`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `list[str]` when available; otherwise side effects only.
    """

    allowed = {"gemini", "deepseek", "groq", "openrouter", "openai", "anthropic"}
    parts = [p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()]
    ordered: list[str] = []
    for part in parts:
        if part in allowed and part not in ordered:
            ordered.append(part)
    if ordered:
        return ordered
    return ["gemini", "deepseek", "groq", "openrouter"]


def _norm_remote_path(path: str, remote_os: str) -> str:
    """
    Norm remote path.
    
    Purpose:
    - Implement `_norm_remote_path` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `path`: input used by this function to compute or route work.
    - `remote_os`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    if remote_os == "windows":
        return str(PureWindowsPath(path))
    return str(PurePosixPath(path))


def _is_allowed_path(path: str, allowed_roots: list[str], remote_os: str) -> bool:
    """
    Is allowed path.
    
    Purpose:
    - Implement `_is_allowed_path` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `path`: input used by this function to compute or route work.
    - `allowed_roots`: input used by this function to compute or route work.
    - `remote_os`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `bool` when available; otherwise side effects only.
    """

    candidate = _norm_remote_path(path, remote_os)
    if remote_os == "windows":
        cand = candidate.replace("/", "\\").rstrip("\\").lower()
        for root in allowed_roots:
            r = _norm_remote_path(root, remote_os).replace("/", "\\").rstrip("\\").lower()
            if cand == r or cand.startswith(r + "\\"):
                return True
        return False

    cand = candidate.rstrip("/")
    for root in allowed_roots:
        r = _norm_remote_path(root, remote_os).rstrip("/")
        if cand == r or cand.startswith(r + "/"):
            return True
    return False


def _ps_quote(value: str) -> str:
    """
    Ps quote.
    
    Purpose:
    - Implement `_ps_quote` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `value`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    return "'" + value.replace("'", "''") + "'"


def _build_windows_command(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """
    Build windows command.
    
    Purpose:
    - Implement `_build_windows_command` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `args`: input used by this function to compute or route work.
    - `cwd`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    cmd = " ".join(_ps_quote(str(a)) for a in args)
    script_lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
    ]
    if cwd:
        script_lines.append(f"Set-Location -LiteralPath {_ps_quote(cwd)}")
    if env:
        for key, value in env.items():
            script_lines.append(f"$env:{key} = {_ps_quote(str(value))}")
    script_lines.append(f"& {cmd}")
    script_lines.append("$code = $LASTEXITCODE")
    script_lines.append("if ($null -eq $code) { $code = 0 }")
    script_lines.append("exit $code")
    script = "\n".join(script_lines)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return (
        "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}"
    )


def _sanitize_powershell_output(text: str) -> str:
    """
    Sanitize powershell output.
    
    Purpose:
    - Implement `_sanitize_powershell_output` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `text`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    if not text:
        return text
    cleaned = text.replace("_x000D__x000A_", "\n").replace("_x000D_", "\r").replace("_x000A_", "\n")
    # Strip ANSI escape/control sequences (common when commands require a PTY).
    cleaned = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", cleaned)
    cleaned = re.sub(r"\x1B\][^\x07]*\x07", "", cleaned)
    cleaned = cleaned.replace("\x1b", "")
    if "<Objs Version=" in cleaned and "</Objs>" in cleaned:
        # Keep only message payloads from CLIXML blocks.
        parts = re.findall(r"<S S=\"(?:Error|Warning|Verbose)\">(.*?)</S>", cleaned, flags=re.DOTALL)
        if parts:
            cleaned = "\n".join(parts)
        else:
            cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return cleaned.strip()


def _build_linux_command(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """
    Build linux command.
    
    Purpose:
    - Implement `_build_linux_command` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - `args`: input used by this function to compute or route work.
    - `cwd`: input used by this function to compute or route work.
    
    Returns:
    - Return value typed as `str` when available; otherwise side effects only.
    """

    import shlex

    run = " ".join(shlex.quote(str(a)) for a in args)
    export = ""
    if env:
        export = " ".join(
            f"{key}={shlex.quote(str(value))}" for key, value in env.items()
        )
        if export:
            run = f"{export} {run}"
    if cwd:
        return f"cd {shlex.quote(cwd)} && {run}"
    return run


class SSHTunnelExecutor:
    """Remote action executor using SSH."""

    _PATH_KEYS = {"working_dir", "directory", "file", "path", "project_dir"}

    def __init__(self) -> None:
        """
        Initialize runtime dependencies and object state.
        
        Purpose:
        - Implement `__init__` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        mode = os.environ.get("OPENCLAW_EXECUTION_MODE", "").strip().lower()
        self.enabled = mode in {"ssh", "ssh_tunnel", "tunnel", "ssh-only"} or _env_bool("OPENCLAW_SSH_FALLBACK_ENABLED", False)

        self.host = os.environ.get("OPENCLAW_SSH_HOST", "127.0.0.1").strip()
        self.port = _env_int("OPENCLAW_SSH_PORT", 2222)
        self.username = os.environ.get("OPENCLAW_SSH_USER", "").strip()
        self.password = os.environ.get("OPENCLAW_SSH_PASSWORD", "")
        self.key_path = os.environ.get("OPENCLAW_SSH_KEY_PATH", "").strip()
        running_in_container = os.path.exists("/.dockerenv")
        if self.key_path.startswith("/app/") and not running_in_container:
            _log.warning(
                "OPENCLAW_SSH_KEY_PATH=%s looks container-scoped, but process appears host-local.",
                self.key_path,
            )
        self.connect_timeout = _env_int("OPENCLAW_SSH_CONNECT_TIMEOUT", 4)
        self.command_timeout = _env_int("OPENCLAW_SSH_COMMAND_TIMEOUT", 180)
        self.remote_os = os.environ.get("OPENCLAW_SSH_REMOTE_OS", "windows").strip().lower()
        self.strict_host_key = _env_bool("OPENCLAW_SSH_STRICT_HOST_KEY", False)
        roots_raw = os.environ.get("OPENCLAW_SSH_ALLOWED_ROOTS", "")
        self.allowed_roots = _parse_roots(roots_raw, self.remote_os)

        self._searcher = WebSearcher(bot_cfg.BRAVE_SEARCH_API_KEY)
        self._coding_bins = {
            "codex": os.environ.get("OPENCLAW_SSH_CODEX_BIN", "codex"),
            "claude": os.environ.get("OPENCLAW_SSH_CLAUDE_BIN", "claude"),
            "cline": os.environ.get("OPENCLAW_SSH_CLINE_BIN", "cline"),
        }
        self._coding_prefix = {
            "codex": ["exec"],
            "claude": ["-p"],
            "cline": ["-p"],
        }
        self._closeable_apps = {
            "chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "notepad": "notepad.exe",
            "code": "Code.exe",
            "explorer": "explorer.exe",
            "slack": "slack.exe",
            "discord": "Discord.exe",
            "spotify": "Spotify.exe",
            "teams": "Teams.exe",
        }
        self._health_cache_seconds = _env_int("OPENCLAW_SSH_HEALTH_CACHE_SECONDS", 15)
        self._last_health_at = 0.0
        self._last_health: tuple[bool, str] = (False, "SSH health not checked yet")
        self._max_parallel = max(1, _env_int("OPENCLAW_SSH_MAX_PARALLEL", bot_cfg.SSH_MAX_PARALLEL))
        self._circuit_breaker_seconds = max(
            0,
            _env_int("OPENCLAW_SSH_CIRCUIT_BREAKER_SECONDS", bot_cfg.SSH_CIRCUIT_BREAKER_SECONDS),
        )
        self._capacity_backoff_seconds = max(
            1,
            _env_int("OPENCLAW_SSH_CAPACITY_BACKOFF_SECONDS", bot_cfg.SSH_CAPACITY_BACKOFF_SECONDS),
        )
        self._health_probe_timeout = max(
            1,
            _env_int("OPENCLAW_SSH_HEALTH_PROBE_TIMEOUT", bot_cfg.SSH_HEALTH_PROBE_TIMEOUT),
        )
        self._parallel_sem = threading.BoundedSemaphore(self._max_parallel)
        self._diag_lock = threading.Lock()
        self._failure_streak = 0
        self._last_error_category = ""
        self._circuit_open_until = 0.0
        self._cline_auto_switch = _env_bool("OPENCLAW_CLINE_AUTO_SWITCH", True)
        self._cline_provider_priority = _parse_provider_priority(
            os.environ.get("OPENCLAW_CLINE_PROVIDER_PRIORITY", ""),
        )
        self._cline_provider_base_urls = {
            "gemini": os.environ.get("OPENCLAW_CLINE_GEMINI_BASE_URL", "").strip(),
            "deepseek": os.environ.get("OPENCLAW_CLINE_DEEPSEEK_BASE_URL", "").strip(),
            "groq": os.environ.get("OPENCLAW_CLINE_GROQ_BASE_URL", "").strip(),
            "openrouter": os.environ.get("OPENCLAW_CLINE_OPENROUTER_BASE_URL", "").strip(),
            "openai": os.environ.get("OPENCLAW_CLINE_OPENAI_BASE_URL", "").strip(),
            "anthropic": os.environ.get("OPENCLAW_CLINE_ANTHROPIC_BASE_URL", "").strip(),
        }
        allowed_permission_modes = {
            "acceptEdits",
            "bypassPermissions",
            "default",
            "dontAsk",
            "plan",
        }
        configured_permission_mode = os.environ.get(
            "OPENCLAW_SSH_CLAUDE_PERMISSION_MODE",
            "bypassPermissions",
        ).strip()
        if configured_permission_mode and configured_permission_mode not in allowed_permission_modes:
            _log.warning(
                "Invalid OPENCLAW_SSH_CLAUDE_PERMISSION_MODE=%r, falling back to bypassPermissions",
                configured_permission_mode,
            )
            configured_permission_mode = "bypassPermissions"
        self._claude_permission_mode = configured_permission_mode
        self._claude_disable_slash_commands = _env_bool(
            "OPENCLAW_SSH_CLAUDE_DISABLE_SLASH_COMMANDS",
            False,
        )
        self._claude_dangerously_skip_permissions = _env_bool(
            "OPENCLAW_SSH_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS",
            False,
        )

    def is_configured(self) -> bool:
        """
        Is configured.
        
        Purpose:
        - Implement `is_configured` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

        return self.enabled and bool(self.username and self.host)

    def _classify_ssh_error(self, detail: str) -> str:
        text = (detail or "").strip().lower()
        if not text:
            return "unknown"
        if (
            "maxstartups" in text
            or "exceeded maxstartups" in text
            or "concurrency limit reached" in text
            or "max_parallel" in text
        ):
            return "capacity"
        if (
            "permission denied" in text
            or "authentication failed" in text
            or "no authentication methods available" in text
        ):
            return "auth"
        if (
            "error reading ssh protocol banner" in text
            or "no existing session" in text
            or "ssh protocol banner" in text
        ):
            return "banner"
        if "timed out" in text or "timeout" in text:
            return "timeout"
        if (
            "connection refused" in text
            or "name or service not known" in text
            or "could not resolve hostname" in text
            or "network is unreachable" in text
            or "no route to host" in text
            or "host unreachable" in text
            or "unable to connect to port" in text
        ):
            return "unreachable"
        return "unknown"

    def _retry_delay_for_category(self, category: str, attempt: int) -> int:
        if category == "capacity":
            return self._capacity_backoff_seconds
        if category == "banner":
            return 5 if attempt <= 1 else 10
        if category in {"timeout", "unreachable"}:
            return 2 if attempt <= 1 else 4
        return min(8, max(1, attempt * 2))

    def _record_ssh_success(self) -> None:
        with self._diag_lock:
            self._failure_streak = 0
            self._last_error_category = ""
            self._circuit_open_until = 0.0

    def _record_ssh_failure(self, category: str) -> None:
        now = time.time()
        with self._diag_lock:
            self._failure_streak += 1
            self._last_error_category = category or "unknown"
            if self._circuit_breaker_seconds <= 0:
                return
            should_open = False
            if category == "capacity" and self._failure_streak >= 2:
                should_open = True
            elif category in {"banner", "timeout"} and self._failure_streak >= 3:
                should_open = True
            if should_open:
                self._circuit_open_until = max(
                    self._circuit_open_until,
                    now + float(self._circuit_breaker_seconds),
                )

    def _circuit_remaining_seconds(self) -> int:
        now = time.time()
        with self._diag_lock:
            if self._circuit_open_until <= now:
                self._circuit_open_until = 0.0
                return 0
            return int(max(1.0, self._circuit_open_until - now))

    def get_diagnostics(self) -> dict[str, Any]:
        remaining = self._circuit_remaining_seconds()
        with self._diag_lock:
            category = self._last_error_category
            streak = int(self._failure_streak)
            open_until = int(self._circuit_open_until) if remaining > 0 else 0
        configured = self.is_configured()
        health_ok = bool(configured and self._last_health[0] and remaining <= 0)
        return {
            "ssh_health_ok": health_ok,
            "ssh_error_category": category,
            "ssh_failure_streak": streak,
            "ssh_circuit_open_until": open_until,
            "ssh_endpoint": f"{self.host}:{self.port}",
        }

    async def health_check(self) -> tuple[bool, str]:
        """
        Health check.
        
        Purpose:
        - Implement `health_check` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `tuple[bool, str]` when available; otherwise side effects only.
        """

        if not self.is_configured():
            self._last_health = (False, "SSH executor not configured")
            return self._last_health

        remaining = self._circuit_remaining_seconds()
        if remaining > 0:
            detail = f"SSH circuit open ({self.host}:{self.port}), retry after {remaining}s"
            self._last_health = (False, detail)
            self._last_health_at = time.time()
            return self._last_health

        now = time.time()
        if now - self._last_health_at < max(self._health_cache_seconds, 1):
            return self._last_health

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._probe_sync)
            self._record_ssh_success()
            self._last_health = (True, f"{self.username}@{self.host}:{self.port}")
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            category = self._classify_ssh_error(detail)
            self._record_ssh_failure(category)
            self._last_health = (False, detail)
        self._last_health_at = now
        return self._last_health

    async def execute_action(
        self,
        action: str,
        params: dict[str, Any],
        confirmed: bool = True,
    ) -> dict[str, Any]:
        """
        Execute action.
        
        Purpose:
        - Implement `execute_action` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `action`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        - `confirmed`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        del confirmed
        if not self.is_configured():
            return {"status": "error", "action": action, "error": "SSH fallback is not configured."}

        remaining = self._circuit_remaining_seconds()
        if remaining > 0:
            diag = self.get_diagnostics()
            category = str(diag.get("ssh_error_category") or "unknown")
            endpoint = str(diag.get("ssh_endpoint") or f"{self.host}:{self.port}")
            return {
                "status": "error",
                "action": action,
                "error_category": category,
                "retry_after_s": remaining,
                "error": (
                    "SSH action failed: "
                    f"SSH_INFRA_CIRCUIT {endpoint} - circuit open after {category} failures. "
                    f"Retry after {remaining}s."
                ),
            }

        params = dict(params or {})
        for key in self._PATH_KEYS:
            if isinstance(params.get(key), str):
                val = _norm_remote_path(params[key], self.remote_os)
                if not _is_allowed_path(val, self.allowed_roots, self.remote_os):
                    return {
                        "status": "error",
                        "action": action,
                        "error": f"Path '{params[key]}' is outside OPENCLAW_SSH_ALLOWED_ROOTS.",
                    }
                params[key] = val

        if action == "web_search":
            try:
                query = str(params.get("query") or "").strip()
                if not query:
                    raise ValueError("Missing required parameter: 'query'")
                raw_num = params.get("num_results", 5)
                num = int(raw_num) if isinstance(raw_num, (int, str)) else 5
                num = min(max(num, 1), 10)
                output = await self._searcher.search(query, num)
                return {
                    "status": "ok",
                    "action": action,
                    "result": {"returncode": 0, "stdout": output, "stderr": ""},
                }
            except Exception as exc:
                return {
                    "status": "ok",
                    "action": action,
                    "result": {"returncode": 1, "stdout": "", "stderr": f"Web search failed: {exc}"},
                }

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._execute_sync, action, params)
            self._record_ssh_success()
            return {"status": "ok", "action": action, "result": result}
        except Exception as exc:
            err_type = type(exc).__name__
            err_msg = str(exc).strip()
            if not err_msg:
                err_msg = err_type
            else:
                err_msg = f"{err_type}: {err_msg}"
            is_infra = isinstance(exc, (OSError, TimeoutError, paramiko.SSHException)) or _looks_like_ssh_infra_error(err_msg)
            if is_infra:
                category = self._classify_ssh_error(err_msg)
                self._record_ssh_failure(category)
                remaining_after = self._circuit_remaining_seconds()
                endpoint = f"{self.host}:{self.port}"
                infra_code = f"SSH_INFRA_{category.upper()}"
                err_msg = f"{infra_code} {endpoint} - {err_msg}"
                payload: dict[str, Any] = {
                    "status": "error",
                    "action": action,
                    "error_category": category,
                    "error": f"SSH action failed: {err_msg}",
                }
                if remaining_after > 0:
                    payload["retry_after_s"] = remaining_after
                _log.error("SSH infra action '%s' failed (%s): %s", action, category, err_msg, exc_info=True)
                return payload

            _log.error("SSH action '%s' failed: %s", action, err_msg, exc_info=True)
            return {"status": "error", "action": action, "error": f"SSH action failed: {err_msg}"}

    def _probe_sync(self) -> None:
        """
        Probe sync.
        
        Purpose:
        - Implement `_probe_sync` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        remaining = self._circuit_remaining_seconds()
        if remaining > 0:
            raise RuntimeError(f"SSH circuit open, retry after {remaining}s")

        slot_timeout = max(1, self._health_probe_timeout)
        acquired = self._parallel_sem.acquire(timeout=slot_timeout)
        if not acquired:
            raise RuntimeError(
                "SSH health probe skipped: OPENCLAW_SSH_MAX_PARALLEL limit reached."
            )
        try:
            client = self._connect(max_retries=1, timeout_override=self._health_probe_timeout)
            try:
                probe_args = ["cmd", "/c", "echo", "ok"] if self.remote_os == "windows" else ["sh", "-lc", "echo ok"]
                command = self._build_command(probe_args, cwd=None)
                _, stdout, stderr = client.exec_command(command, timeout=self._health_probe_timeout)
                _ = stdout.read()
                _ = stderr.read()
            finally:
                client.close()
        finally:
            self._parallel_sem.release()

    def _connect(self, *, max_retries: int = 3, timeout_override: int | None = None) -> paramiko.SSHClient:
        """
        Connect.
        
        Purpose:
        - Implement `_connect` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - None.
        
        Returns:
        - Return value typed as `paramiko.SSHClient` when available; otherwise side effects only.
        """

        retries = max(1, int(max_retries))
        last_exc: Exception | None = None
        connect_timeout = max(1, int(timeout_override or self.connect_timeout))

        for attempt in range(1, retries + 1):
            client = paramiko.SSHClient()
            if self.strict_host_key:
                client.load_system_host_keys()
            else:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            kwargs: dict[str, Any] = {
                "hostname": self.host,
                "port": self.port,
                "username": self.username,
                "timeout": connect_timeout,
                "auth_timeout": connect_timeout,
                "banner_timeout": connect_timeout,
                "look_for_keys": True,
                "allow_agent": True,
            }
            if self.key_path:
                kwargs["key_filename"] = self.key_path
            if self.password:
                kwargs["password"] = self.password

            try:
                client.connect(**kwargs)
            except (OSError, paramiko.SSHException) as exc:
                last_exc = exc
                client.close()
                err_detail = str(exc).strip() or type(exc).__name__
                category = self._classify_ssh_error(err_detail)
                if category == "auth":
                    _log.error("SSH authentication failed to %s:%s (%s)", self.host, self.port, err_detail)
                    raise RuntimeError(
                        f"SSH authentication failed to {self.host}:{self.port}: {err_detail}"
                    ) from exc
                if attempt < retries:
                    delay = self._retry_delay_for_category(category, attempt)
                    _log.warning(
                        "SSH connect attempt %d/%d failed [%s] (%s), retrying in %ds...",
                        attempt, retries, category, err_detail, delay,
                    )
                    time.sleep(delay)
                    continue
                _log.error("SSH connect failed after %d attempts [%s]: %s", retries, category, err_detail)
                raise RuntimeError(
                    f"SSH connect failed to {self.host}:{self.port} after {retries} attempts: {err_detail}"
                ) from exc

            # Warm up the transport by opening (then closing) an SFTP session.
            # Some SSH servers/tunnels close the transport after the first exec
            # channel exits, causing "No existing session" on subsequent calls.
            # Opening SFTP first stabilises the transport for the session lifetime.
            try:
                sftp = client.open_sftp()
                sftp.close()
            except Exception:
                pass  # best effort - if SFTP probe fails, proceed anyway

            return client

        raise last_exc  # unreachable, but keeps mypy happy

    def _execute_sync(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sync.
        
        Purpose:
        - Implement `_execute_sync` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `action`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        slot_timeout = max(1, self.connect_timeout + 1)
        acquired = self._parallel_sem.acquire(timeout=slot_timeout)
        if not acquired:
            raise RuntimeError("SSH concurrency limit reached; try again shortly.")
        try:
            client = self._connect()
            try:
                if action == "file_read":
                    return self._file_read(client, params)
                if action == "file_write":
                    return self._file_write(client, params)
                if action == "create_directory":
                    return self._create_directory(client, params)
                if action == "list_directory":
                    return self._list_directory(client, params)
                return self._run_command_action(client, action, params)
            finally:
                client.close()
        finally:
            self._parallel_sem.release()

    def _build_command(
        self,
        args: list[str],
        cwd: str | None,
        env: dict[str, str] | None = None,
    ) -> str:
        """
        Build command.
        
        Purpose:
        - Implement `_build_command` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `args`: input used by this function to compute or route work.
        - `cwd`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        if self.remote_os == "windows":
            return _build_windows_command(args, cwd=cwd, env=env)
        return _build_linux_command(args, cwd=cwd, env=env)

    def _require_str(self, params: dict[str, Any], key: str) -> str:
        """
        Require str.
        
        Purpose:
        - Implement `_require_str` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `params`: input used by this function to compute or route work.
        - `key`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        value = params.get(key)
        if not value or not isinstance(value, str):
            raise ValueError(f"Missing required parameter: '{key}'")
        return value

    def _run_command(
        self,
        client: paramiko.SSHClient,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
        use_pty: bool = False,
    ) -> dict[str, Any]:
        """
        Run command.
        
        Purpose:
        - Implement `_run_command` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `args`: input used by this function to compute or route work.
        - `cwd`: input used by this function to compute or route work.
        - `timeout`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        command = self._build_command(args, cwd=cwd, env=env)
        _, stdout, stderr = client.exec_command(
            command,
            timeout=timeout or self.command_timeout,
            get_pty=use_pty,
        )
        out = stdout.read().decode("utf-8", errors="replace")[:8192]
        err = stderr.read().decode("utf-8", errors="replace")[:4096]
        if self.remote_os == "windows":
            out = _sanitize_powershell_output(out)
            err = _sanitize_powershell_output(err)
        rc = stdout.channel.recv_exit_status()
        return {"returncode": int(rc), "stdout": out, "stderr": err}

    def _persist_generated_files_from_blocks(
        self,
        *,
        client: paramiko.SSHClient,
        generated: str,
        working_dir: str | None,
    ) -> tuple[list[str], list[str]]:
        pattern = re.compile(r"```([^\n`]+)\r?\n(.*?)```", re.DOTALL)
        files_written: list[str] = []
        errors: list[str] = []

        # Language tag -> file extension for fallback naming.
        lang_ext = {
            "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
            "typescript": ".ts", "ts": ".ts", "java": ".java", "c": ".c",
            "cpp": ".cpp", "c++": ".cpp", "go": ".go", "rust": ".rs",
            "ruby": ".rb", "bash": ".sh", "sh": ".sh", "html": ".html",
            "css": ".css", "json": ".json", "yaml": ".yaml", "yml": ".yaml",
            "toml": ".toml", "sql": ".sql", "r": ".r", "swift": ".swift",
            "kotlin": ".kt", "dart": ".dart", "lua": ".lua", "perl": ".pl",
        }

        cwd_norm = _norm_remote_path(working_dir, self.remote_os) if working_dir else ""

        file_blocks: list[tuple[str, str]] = []
        lang_blocks: list[tuple[str, str]] = []
        for match in pattern.finditer(generated or ""):
            tag = match.group(1).strip()
            content = match.group(2)
            looks_like_file = (
                "." in tag
                or "/" in tag
                or "\\" in tag
                or (
                    " " not in tag
                    and tag.lower() not in lang_ext
                    and re.match(r"^[A-Za-z0-9_.\\/-]+$", tag) is not None
                )
            )
            if looks_like_file:
                file_blocks.append((tag, content))
            else:
                lang_blocks.append((tag, content))

        if not file_blocks and lang_blocks:
            for idx, (tag, content) in enumerate(lang_blocks):
                ext = lang_ext.get(tag.lower(), f".{tag.lower()}")
                fallback = f"main{ext}" if idx == 0 else f"file{idx}{ext}"
                file_blocks.append((fallback, content))

        # Avoid foo.py + foo/bar.py import shadowing.
        top_level_stems = set()
        for fn, _ in file_blocks:
            parts = fn.replace("\\", "/").split("/")
            if len(parts) == 1 and parts[0].endswith(".py"):
                top_level_stems.add(parts[0].rsplit(".", 1)[0])

        shadow_renames: dict[str, str] = {}
        for stem in top_level_stems:
            for fn, _ in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] == stem:
                    shadow_renames[stem] = "lib"
                    break

        if shadow_renames:
            fixed_blocks: list[tuple[str, str]] = []
            for fn, content in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] in shadow_renames:
                    parts[0] = shadow_renames[parts[0]]
                    fn = "/".join(parts)
                for old_name, new_name in shadow_renames.items():
                    content = content.replace(f"from {old_name}.", f"from {new_name}.")
                    content = content.replace(f"import {old_name}.", f"import {new_name}.")
                fixed_blocks.append((fn, content))
            file_blocks = fixed_blocks

        sftp = client.open_sftp()
        try:
            for filename, content in file_blocks:
                if cwd_norm and not os.path.isabs(filename):
                    if self.remote_os == "windows":
                        remote_path = cwd_norm.rstrip("\\") + "\\" + filename.replace("/", "\\")
                    else:
                        remote_path = cwd_norm.rstrip("/") + "/" + filename.lstrip("/")
                else:
                    remote_path = _norm_remote_path(filename, self.remote_os)

                try:
                    if self.remote_os == "windows":
                        parent = str(PureWindowsPath(remote_path).parent)
                    else:
                        parent = str(PurePosixPath(remote_path).parent)
                    self._sftp_makedirs(sftp, parent)
                    with sftp.open(remote_path, "w") as fh:
                        fh.write(content)
                    files_written.append(filename)
                except Exception as exc:
                    errors.append(f"{filename}: {exc}")
        finally:
            sftp.close()

        return files_written, errors

    def _snapshot_working_tree(
        self,
        *,
        client: paramiko.SSHClient,
        working_dir: str | None,
    ) -> dict[str, tuple[int, int]]:
        if not working_dir:
            return {}
        root = _norm_remote_path(working_dir, self.remote_os)
        snapshot: dict[str, tuple[int, int]] = {}
        max_entries = 5000
        max_depth = 10
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}

        sftp = client.open_sftp()
        try:
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack and len(snapshot) < max_entries:
                current, depth = stack.pop()
                try:
                    entries = sftp.listdir_attr(current)
                except OSError:
                    continue
                for entry in entries:
                    if len(snapshot) >= max_entries:
                        break
                    name = entry.filename
                    path = self._norm_join(current, name)
                    if stat.S_ISDIR(entry.st_mode):
                        if depth < max_depth and name not in skip_dirs:
                            stack.append((path, depth + 1))
                        continue
                    rel = self._relative_to_working_root(root, path)
                    if rel:
                        snapshot[rel] = (int(entry.st_size), int(getattr(entry, "st_mtime", 0) or 0))
        finally:
            sftp.close()
        return snapshot

    def _relative_to_working_root(self, root: str, path: str) -> str:
        root_norm = _norm_remote_path(root, self.remote_os).replace("\\", "/").rstrip("/")
        path_norm = _norm_remote_path(path, self.remote_os).replace("\\", "/")
        if path_norm == root_norm:
            return ""
        prefix = root_norm + "/"
        if path_norm.startswith(prefix):
            return path_norm[len(prefix):]
        return path_norm.rsplit("/", 1)[-1]

    @staticmethod
    def _diff_snapshots(
        before: dict[str, tuple[int, int]],
        after: dict[str, tuple[int, int]],
    ) -> list[str]:
        changed: list[str] = []
        for rel_path, meta in after.items():
            if before.get(rel_path) != meta:
                changed.append(rel_path)
        return sorted(changed)

    # ------------------------------------------------------------------
    # Ollama coding agent - EC2 calls Ollama on laptop via SSH, all
    # intelligence (prompt building, code parsing, file writing) stays on EC2.
    # ------------------------------------------------------------------

    _OLLAMA_SYSTEM_PROMPT = (
        "You are an expert coding agent. Implement the task completely.\n"
        "For EVERY file you create or modify, output it in a fenced code block.\n"
        "The opening fence MUST be the filename (not a language name).\n\n"
        "Example:\n"
        "```main.py\n"
        "print('hello')\n"
        "```\n\n"
        "```utils/helper.py\n"
        "def add(a, b): return a + b\n"
        "```\n\n"
        "Rules:\n"
        "- The opening ``` MUST be followed by the actual filename, NEVER a language like python or js.\n"
        "- Write complete, working code - no placeholders, no '...'.\n"
        "- Include every file needed (source, config, requirements, etc.).\n"
        "- Do NOT add explanations outside code blocks.\n"
        "- Use the working directory as the project root.\n"
        "- NEVER create a subdirectory with the same name as a top-level .py file. "
        "For example, if you have main.py, do NOT also create main/utils.py - "
        "use a different directory name like lib/ or helpers/ instead.\n"
        "- Name the main entry-point file after the project name given in the task.\n"
    )

    def _run_ollama_coding_agent(
        self,
        client: paramiko.SSHClient,
        prompt: str,
        working_dir: str | None,
        model: str,
        ollama_url: str,
        timeout: int,
    ) -> dict[str, Any]:
        """
        1. Write a one-shot Python script to the laptop via SFTP.
        2. Run it via SSH exec - it calls the local Ollama API and prints the response.
        3. Parse the response on EC2 for fenced code blocks with file paths.
        4. Write each file to the laptop via SFTP.
        5. Return a summary.

        Laptop is a dumb executor: it runs Python standard library + Ollama.
        All logic lives here on EC2.
        """
        full_prompt = f"{self._OLLAMA_SYSTEM_PROMPT}\nTask: {prompt}"
        b64_prompt  = base64.b64encode(full_prompt.encode("utf-8")).decode("ascii")
        b64_url     = base64.b64encode(ollama_url.encode("utf-8")).decode("ascii")
        b64_model   = base64.b64encode(model.encode("utf-8")).decode("ascii")

        # One-shot Python script - uses only stdlib (no pip installs needed).
        script_body = (
            "import base64, json, urllib.request, sys\n"
            f"prompt = base64.b64decode('{b64_prompt}').decode('utf-8')\n"
            f"url    = base64.b64decode('{b64_url}').decode('utf-8') + '/api/generate'\n"
            f"model  = base64.b64decode('{b64_model}').decode('utf-8')\n"
            f"options = {{'num_ctx': {int(os.environ.get('OLLAMA_NUM_CTX', '8192'))}, 'temperature': 0.2}}\n"
            "payload = json.dumps({'model': model, 'prompt': prompt, 'stream': False, 'options': options}).encode()\n"
            "req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})\n"
            "try:\n"
            "    resp = urllib.request.urlopen(req, timeout=600).read().decode()\n"
            "    print(json.loads(resp).get('response', ''))\n"
            "except Exception as e:\n"
            "    print(f'OLLAMA_ERROR: {e}', file=__import__('sys').stderr)\n"
            "    sys.exit(1)\n"
        )

        # Write the script to a temp file inside the sandbox temp folder.
        if self.remote_os == "windows":
            tmp_script = "E:\\SKYNET-SANDBOX\\temp\\_skynet_ollama_coder.py"
        else:
            tmp_script = "/tmp/_skynet_ollama_coder.py"

        sftp = client.open_sftp()
        try:
            # Ensure the parent directory exists (e.g. E:\SKYNET-SANDBOX\temp\)
            if self.remote_os == "windows":
                tmp_parent = str(PureWindowsPath(tmp_script).parent)
            else:
                tmp_parent = str(PurePosixPath(tmp_script).parent)
            self._sftp_makedirs(sftp, tmp_parent)
            with sftp.open(tmp_script, "w") as fh:
                fh.write(script_body)
        except Exception as exc:
            sftp.close()
            return {"returncode": 1, "stdout": "", "stderr": f"Cannot write temp script: {exc}"}

        # Run it - stdout is the Ollama-generated text.
        # Close SFTP first - long-running exec can invalidate the session.
        sftp.close()
        result = self._run_command(client, ["python", tmp_script], cwd=None, timeout=timeout)

        # Re-open SFTP for file operations after exec.
        sftp = client.open_sftp()

        # Clean up temp script (best effort).
        try:
            sftp.remove(tmp_script)
        except Exception:
            pass

        if result["returncode"] != 0:
            sftp.close()
            return result

        generated = result["stdout"]

        # Parse fenced code blocks: ```path/to/file.ext\n<content>\n```
        pattern = re.compile(r"```([^\n`]+)\n(.*?)```", re.DOTALL)
        files_written: list[str] = []
        errors: list[str] = []

        # Language tag -> file extension mapping for fallback naming.
        _LANG_EXT = {
            "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
            "typescript": ".ts", "ts": ".ts", "java": ".java", "c": ".c",
            "cpp": ".cpp", "c++": ".cpp", "go": ".go", "rust": ".rs",
            "ruby": ".rb", "bash": ".sh", "sh": ".sh", "html": ".html",
            "css": ".css", "json": ".json", "yaml": ".yaml", "yml": ".yaml",
            "toml": ".toml", "sql": ".sql", "r": ".r", "swift": ".swift",
            "kotlin": ".kt", "dart": ".dart", "lua": ".lua", "perl": ".pl",
        }

        cwd_norm = _norm_remote_path(working_dir, self.remote_os) if working_dir else ""

        # Collect all matches, separating file-path blocks from language-tag blocks.
        file_blocks: list[tuple[str, str]] = []
        lang_blocks: list[tuple[str, str]] = []
        for match in pattern.finditer(generated):
            tag = match.group(1).strip()
            content = match.group(2)
            if "." in tag or "/" in tag or "\\" in tag:
                file_blocks.append((tag, content))
            else:
                lang_blocks.append((tag, content))

        # If no file-path blocks found, convert language-tag blocks using fallback names.
        if not file_blocks and lang_blocks:
            for idx, (tag, content) in enumerate(lang_blocks):
                ext = _LANG_EXT.get(tag.lower(), f".{tag.lower()}")
                fallback = f"main{ext}" if idx == 0 else f"file{idx}{ext}"
                file_blocks.append((fallback, content))

        # -- Fix shadowing: if foo.py and foo/bar.py both exist, rename the
        # subdirectory to lib/ so Python doesn't treat foo.py as the package.
        top_level_stems = set()
        for fn, _ in file_blocks:
            parts = fn.replace("\\", "/").split("/")
            if len(parts) == 1 and parts[0].endswith(".py"):
                top_level_stems.add(parts[0].rsplit(".", 1)[0])

        # Map of conflicting stems that need renaming (e.g. "blakely" -> "lib").
        shadow_renames: dict[str, str] = {}
        for stem in top_level_stems:
            for fn, _ in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] == stem:
                    shadow_renames[stem] = "lib"
                    break

        if shadow_renames:
            fixed_blocks: list[tuple[str, str]] = []
            for fn, content in file_blocks:
                parts = fn.replace("\\", "/").split("/")
                if len(parts) > 1 and parts[0] in shadow_renames:
                    parts[0] = shadow_renames[parts[0]]
                    fn = "/".join(parts)
                # Rewrite imports: from <old_pkg>.x import y -> from lib.x import y
                for old_name, new_name in shadow_renames.items():
                    content = content.replace(f"from {old_name}.", f"from {new_name}.")
                    content = content.replace(f"import {old_name}.", f"import {new_name}.")
                fixed_blocks.append((fn, content))
            file_blocks = fixed_blocks

        for filename, content in file_blocks:

            # Resolve to absolute path inside working_dir.
            if cwd_norm and not os.path.isabs(filename):
                if self.remote_os == "windows":
                    remote_path = cwd_norm.rstrip("\\") + "\\" + filename.replace("/", "\\")
                else:
                    remote_path = cwd_norm.rstrip("/") + "/" + filename.lstrip("/")
            else:
                remote_path = _norm_remote_path(filename, self.remote_os)

            # Ensure parent directory exists.
            try:
                if self.remote_os == "windows":
                    parent = str(PureWindowsPath(remote_path).parent)
                else:
                    parent = str(PurePosixPath(remote_path).parent)
                self._sftp_makedirs(sftp, parent)
                with sftp.open(remote_path, "w") as fh:
                    fh.write(content)
                files_written.append(filename)
            except Exception as exc:
                errors.append(f"{filename}: {exc}")

        sftp.close()

        if not files_written and not errors:
            return {
                "returncode": 1,
                "stdout": generated[:1000],
                "stderr": "Ollama responded but no code blocks with file paths were found.",
            }

        summary_parts = []
        if files_written:
            summary_parts.append(f"Wrote {len(files_written)} file(s): {', '.join(files_written)}")
        if errors:
            summary_parts.append(f"Errors: {'; '.join(errors)}")

        return {
            "returncode": 0 if files_written else 1,
            "stdout": "\n".join(summary_parts),
            "stderr": "",
            "files_written": files_written,
        }

    @staticmethod
    def _is_missing_command_output(text: str) -> bool:
        lowered = (text or "").lower()
        markers = (
            "is not recognized",
            "command not found",
            "no such file or directory",
            "cannot find the file specified",
            "not found",
        )
        return any(marker in lowered for marker in markers)

    def _run_python_snippet(
        self,
        client: paramiko.SSHClient,
        script: str,
        args: list[str],
        *,
        timeout: int = 60,
    ) -> dict[str, Any]:
        interpreters: list[list[str]] = [["python"]]
        if self.remote_os == "windows":
            interpreters.append(["py", "-3"])
        else:
            interpreters.append(["python3"])

        last_result: dict[str, Any] | None = None
        for prefix in interpreters:
            result = self._run_command(
                client,
                [*prefix, "-c", script, *args],
                cwd=None,
                timeout=timeout,
            )
            last_result = result
            if result.get("returncode", 1) == 0:
                return result
            combined = f"{result.get('stderr', '')}\n{result.get('stdout', '')}"
            if self._is_missing_command_output(combined):
                continue
            return result
        return last_result or {"returncode": 1, "stdout": "", "stderr": "Python runtime not found on remote host."}

    @staticmethod
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

    def _resolve_backend(self, *, agent: str, backend: str) -> tuple[str | None, str | None]:
        backend = (backend or "auto").strip().lower()
        if backend not in {"auto", "ollama", "native"}:
            return None, "backend must be one of: auto, ollama, native."
        if agent == "claude":
            if backend == "auto":
                return "ollama", None
            return backend, None
        if backend == "ollama":
            return None, f"backend=ollama is only supported for agent='claude'."
        if backend == "auto":
            return "native", None
        return backend, None

    def _ollama_check_model(
        self,
        client: paramiko.SSHClient,
        *,
        base_url: str,
        model: str,
    ) -> dict[str, Any]:
        script = (
            "import json, sys, urllib.request\n"
            "base=(sys.argv[1] if len(sys.argv)>1 else '').rstrip('/')\n"
            "model=sys.argv[2] if len(sys.argv)>2 else ''\n"
            "try:\n"
            "    resp=urllib.request.urlopen(base + '/api/tags', timeout=15)\n"
            "    data=json.loads(resp.read().decode('utf-8', errors='replace'))\n"
            "except Exception as exc:\n"
            "    print(f'CONNECT_ERROR:{exc}')\n"
            "    raise SystemExit(2)\n"
            "names=set()\n"
            "for item in (data.get('models') or []):\n"
            "    if isinstance(item, dict):\n"
            "        name=str(item.get('name') or '').strip()\n"
            "        if name:\n"
            "            names.add(name)\n"
            "if model in names:\n"
            "    print('MODEL_OK')\n"
            "    raise SystemExit(0)\n"
            "print('MODEL_MISSING')\n"
            "raise SystemExit(3)\n"
        )
        result = self._run_python_snippet(
            client,
            script,
            [base_url, model],
            timeout=90,
        )
        rc = int(result.get("returncode", 1))
        out = str(result.get("stdout") or "").strip()
        err = str(result.get("stderr") or "").strip()
        if rc == 0:
            return {"reachable": True, "present": True, "summary": "model present"}
        if rc == 3:
            return {"reachable": True, "present": False, "summary": "model missing"}
        if rc == 2:
            return {"reachable": False, "present": False, "summary": out or err or "cannot reach Ollama API"}
        return {"reachable": False, "present": False, "summary": err or out or "ollama preflight failed"}

    def _ollama_pull_model(
        self,
        client: paramiko.SSHClient,
        *,
        model: str,
        timeout: int,
    ) -> dict[str, Any]:
        pull_timeout = max(120, min(timeout, 1800))
        return self._run_command(
            client,
            ["ollama", "pull", model],
            cwd=None,
            timeout=pull_timeout,
        )

    def _ollama_context_warning(
        self,
        client: paramiko.SSHClient,
        *,
        model: str,
    ) -> str:
        show = self._run_command(client, ["ollama", "show", model], cwd=None, timeout=45)
        if int(show.get("returncode", 1)) != 0:
            return ""
        text = f"{show.get('stdout', '')}\n{show.get('stderr', '')}"
        patterns = (
            r"context length[^0-9]*([0-9][0-9,]*)",
            r"num_ctx[^0-9]*([0-9][0-9,]*)",
        )
        context_value: int | None = None
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            raw = match.group(1).replace(",", "")
            try:
                context_value = int(raw)
            except ValueError:
                context_value = None
            if context_value is not None:
                break
        if context_value is None:
            return ""
        if context_value >= max(1, int(bot_cfg.CLAUDE_OLLAMA_MIN_CONTEXT)):
            return ""
        return (
            f"Warning: detected model context {context_value} tokens, below "
            f"target {int(bot_cfg.CLAUDE_OLLAMA_MIN_CONTEXT)}."
        )

    def _run_coding_agent_native(
        self,
        *,
        client: paramiko.SSHClient,
        agent: str,
        prompt: str,
        cwd: str | None,
        timeout: int,
        model: str,
    ) -> dict[str, Any]:
        binary = self._coding_bins[agent]
        if self.remote_os == "windows":
            binary, available = self._resolve_windows_binary(client, binary)
            if not available:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"'{agent}' CLI is not installed or not on PATH. "
                        f"Expected binary: {self._coding_bins[agent]}"
                    ),
                }

        before_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        run: dict[str, Any]

        if agent == "claude":
            args = self._build_claude_command_args(
                binary=binary,
                prompt=prompt,
                model=model,
            )
            # Claude CLI can block indefinitely on SSH non-PTY channels.
            run = self._run_command(client, args, cwd=cwd, timeout=timeout, use_pty=True)
        else:
            args = [binary, *self._coding_prefix[agent], prompt]
            initial = self._run_command(client, args, cwd=cwd, timeout=timeout)
            if agent != "cline":
                run = initial
            elif initial["returncode"] == 0:
                run = initial
            elif not self._cline_auto_switch:
                run = initial
            elif not self._is_retryable_cline_failure(initial):
                run = initial
            else:
                run = self._run_cline_with_auto_switch(
                    client=client,
                    binary=binary,
                    prompt=prompt,
                    cwd=cwd if isinstance(cwd, str) else None,
                    timeout=timeout,
                    initial_result=initial,
                )

        if int(run.get("returncode", 1)) != 0:
            return run

        parsed_written: list[str] = []
        parse_errors: list[str] = []
        if agent == "claude":
            generated = str(run.get("stdout") or "")
            parsed_written, parse_errors = self._persist_generated_files_from_blocks(
                client=client,
                generated=generated,
                working_dir=cwd,
            )
        after_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        changed_written = self._diff_snapshots(before_snapshot, after_snapshot)

        combined: list[str] = []
        for path in [*parsed_written, *changed_written]:
            if path not in combined:
                combined.append(path)
        if combined:
            run["files_written"] = combined
        if parse_errors:
            warning = "; ".join(parse_errors)[:1200]
            current_err = str(run.get("stderr") or "").strip()
            run["stderr"] = f"{current_err}\nFILE_WRITE_WARNINGS: {warning}".strip()
        return run

    def _run_coding_agent_claude_ollama(
        self,
        *,
        client: paramiko.SSHClient,
        prompt: str,
        cwd: str | None,
        timeout: int,
        model: str,
        base_url: str,
        auto_pull_model: bool,
    ) -> dict[str, Any]:
        binary = self._coding_bins["claude"]
        if self.remote_os == "windows":
            binary, available = self._resolve_windows_binary(client, binary)
            if not available:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        "'claude' CLI is not installed or not on PATH. "
                        f"Expected binary: {self._coding_bins['claude']}"
                    ),
                }

        before_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        check = self._ollama_check_model(client, base_url=base_url, model=model)
        if not check["reachable"]:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": f"OLLAMA_SETUP_ERROR: {check['summary']}",
            }

        if not check["present"]:
            if not auto_pull_model:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"OLLAMA_MODEL_MISSING: '{model}' not found. "
                        "Set auto_pull_model=true or pre-pull the model."
                    ),
                }
            pull = self._ollama_pull_model(client, model=model, timeout=timeout)
            if int(pull.get("returncode", 1)) != 0:
                reason = str(pull.get("stderr") or pull.get("stdout") or "").strip()
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"OLLAMA_MODEL_SETUP_ERROR: pull failed for '{model}'. {reason}",
                }
            check_after = self._ollama_check_model(client, base_url=base_url, model=model)
            if not check_after["reachable"]:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"OLLAMA_SETUP_ERROR: {check_after['summary']}",
                }
            if not check_after["present"]:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"OLLAMA_MODEL_SETUP_ERROR: '{model}' still missing after pull."
                    ),
                }

        enforced_prompt = f"{self._OLLAMA_SYSTEM_PROMPT}\nTask:\n{prompt}"
        args = self._build_claude_command_args(
            binary=binary,
            prompt=enforced_prompt,
            model=model,
        )
        env = {
            "ANTHROPIC_AUTH_TOKEN": bot_cfg.CLAUDE_OLLAMA_AUTH_TOKEN or "ollama",
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_BASE_URL": base_url,
        }
        # Claude-over-Ollama requires PTY on this SSH path to avoid hangs.
        run = self._run_command(
            client,
            args,
            cwd=cwd,
            timeout=timeout,
            env=env,
            use_pty=True,
        )
        warning = self._ollama_context_warning(client, model=model)
        if warning:
            current_out = str(run.get("stdout") or "").strip()
            run["stdout"] = f"{warning}\n{current_out}".strip()
        if int(run.get("returncode", 1)) != 0:
            return run

        generated = str(run.get("stdout") or "")
        parsed_written, parse_errors = self._persist_generated_files_from_blocks(
            client=client,
            generated=generated,
            working_dir=cwd,
        )
        after_snapshot = self._snapshot_working_tree(client=client, working_dir=cwd)
        changed_written = self._diff_snapshots(before_snapshot, after_snapshot)
        combined: list[str] = []
        for path in [*parsed_written, *changed_written]:
            if path not in combined:
                combined.append(path)
        if combined:
            run["files_written"] = combined
        if parse_errors:
            warning = "; ".join(parse_errors)[:1200]
            current_err = str(run.get("stderr") or "").strip()
            run["stderr"] = f"{current_err}\nFILE_WRITE_WARNINGS: {warning}".strip()
        return run

    def _build_claude_command_args(
        self,
        *,
        binary: str,
        prompt: str,
        model: str = "",
    ) -> list[str]:
        args = [binary]
        if model:
            args.extend(["--model", model])
        if self._claude_permission_mode:
            args.extend(["--permission-mode", self._claude_permission_mode])
        if self._claude_disable_slash_commands:
            args.append("--disable-slash-commands")
        if self._claude_dangerously_skip_permissions:
            args.append("--dangerously-skip-permissions")
        args.extend(["-p", prompt])
        return args

    def _run_command_action(self, client: paramiko.SSHClient, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Run command action.
        
        Purpose:
        - Implement `_run_command_action` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `action`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        if action == "git_status":
            cwd = self._require_str(params, "working_dir")
            self._ensure_git_safe_directory(client, cwd)
            return self._run_command(client, ["git", "status", "--porcelain"], cwd=cwd)

        if action == "run_tests":
            cwd = self._require_str(params, "working_dir")
            runner = params.get("runner", "pytest")
            if runner == "pytest":
                result = self._run_command(client, ["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
                if int(result.get("returncode", 1)) != 0 and _python_module_missing(result, "pytest"):
                    install = self._run_command(
                        client,
                        ["python", "-m", "pip", "install", "pytest"],
                        cwd=cwd,
                        timeout=300,
                    )
                    if int(install.get("returncode", 1)) != 0:
                        detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                        base_err = str(result.get("stderr") or "").strip()
                        result["stderr"] = f"{base_err}\nPYTEST_SETUP_ERROR: {detail}".strip()
                        return result
                    retry = self._run_command(client, ["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
                    retry["stdout"] = f"Auto-installed pytest.\n{retry.get('stdout', '')}".strip()
                    return retry
                return result
            if runner == "npm":
                return self._run_command(client, ["npm", "test"], cwd=cwd)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown runner: {runner}"}

        if action == "lint_project":
            cwd = self._require_str(params, "working_dir")
            linter = params.get("linter", "ruff")
            if linter == "ruff":
                result = self._run_command(client, ["python", "-m", "ruff", "check", "."], cwd=cwd)
                if int(result.get("returncode", 1)) != 0 and _python_module_missing(result, "ruff"):
                    install = self._run_command(
                        client,
                        ["python", "-m", "pip", "install", "ruff"],
                        cwd=cwd,
                        timeout=300,
                    )
                    if int(install.get("returncode", 1)) != 0:
                        detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                        base_err = str(result.get("stderr") or "").strip()
                        result["stderr"] = f"{base_err}\nRUFF_SETUP_ERROR: {detail}".strip()
                        return result
                    retry = self._run_command(client, ["python", "-m", "ruff", "check", "."], cwd=cwd)
                    retry["stdout"] = f"Auto-installed ruff.\n{retry.get('stdout', '')}".strip()
                    return retry
                return result
            if linter == "eslint":
                return self._run_command(client, ["npx", "eslint", "."], cwd=cwd)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown linter: {linter}"}

        if action == "build_project":
            cwd = self._require_str(params, "working_dir")
            tool = params.get("build_tool", "npm")
            if tool == "npm":
                return self._run_command(client, ["npm", "run", "build"], cwd=cwd)
            if tool == "python":
                return self._run_command(client, ["python", "-m", "build"], cwd=cwd)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown build tool: {tool}"}

        if action == "install_dependencies":
            cwd = self._require_str(params, "working_dir")
            manager = params.get("manager", "pip")
            if manager == "pip":
                req_file = self._norm_join(cwd, "requirements.txt")
                return self._run_command(
                    client, ["python", "-m", "pip", "install", "-r", req_file], cwd=cwd, timeout=300,
                )
            if manager == "npm":
                return self._run_command(client, ["npm", "install"], cwd=cwd, timeout=300)
            return {"returncode": 1, "stdout": "", "stderr": f"Unknown manager: {manager}"}

        if action == "git_init":
            cwd = self._require_str(params, "working_dir")
            self._ensure_git_safe_directory(client, cwd)
            result = self._run_command(client, ["git", "init"], cwd=cwd)
            if result["returncode"] == 0:
                _ = self._run_command(client, ["git", "checkout", "-b", "main"], cwd=cwd)
            return result

        if action == "git_add_all":
            cwd = self._require_str(params, "working_dir")
            self._ensure_git_safe_directory(client, cwd)
            return self._run_command(client, ["git", "add", "-A"], cwd=cwd)

        if action == "git_commit":
            cwd = self._require_str(params, "working_dir")
            message = self._require_str(params, "message")
            self._ensure_git_safe_directory(client, cwd)
            stage = self._run_command(client, ["git", "add", "-u"], cwd=cwd)
            if stage["returncode"] != 0:
                return stage
            return self._run_command(client, ["git", "commit", "-m", message], cwd=cwd)

        if action == "git_push":
            cwd = self._require_str(params, "working_dir")
            remote = str(params.get("remote", "origin"))
            branch = str(params.get("branch", "main"))
            self._ensure_git_safe_directory(client, cwd)
            return self._run_command(client, ["git", "push", "-u", remote, branch], cwd=cwd)

        if action == "gh_create_repo":
            cwd = self._require_str(params, "working_dir")
            repo_name = self._require_str(params, "repo_name")
            description = str(params.get("description") or "")
            private = params.get("private", False) is True
            if not re.match(r"^[a-zA-Z0-9._-]+$", repo_name):
                return {"returncode": 1, "stdout": "", "stderr": "Invalid repo name characters."}
            if self.remote_os == "windows":
                exists = self._run_command(client, ["where", "gh"], cwd=None)
                if exists.get("returncode", 1) != 0:
                    return {
                        "returncode": 127,
                        "stdout": "",
                        "stderr": "GitHub CLI (gh) is not installed on the worker laptop.",
                    }
            visibility = "--private" if private else "--public"
            args = ["gh", "repo", "create", repo_name, visibility, "--source=.", "--push"]
            if description:
                args.extend(["--description", description])
            return self._run_command(client, args, cwd=cwd, timeout=120)

        if action == "open_in_vscode":
            path = self._require_str(params, "path")
            return self._run_command(client, ["code", path], cwd=None)

        if action == "check_coding_agents":
            if self.remote_os == "windows":
                lines = []
                for name, binary in self._coding_bins.items():
                    resolved_bin, available = self._resolve_windows_binary(client, binary)
                    if available:
                        lines.append(f"{name}: available ({resolved_bin})")
                    else:
                        lines.append(f"{name}: unavailable (expected binary: {binary})")
                out = "\n".join(lines)
                err = ""
                rc = 0
                return {"returncode": int(rc), "stdout": out, "stderr": err}
            # Linux fallback
            lines = []
            for name, binary in self._coding_bins.items():
                r = self._run_command(client, ["bash", "-lc", f"command -v {binary} || true"], cwd=None)
                if r["stdout"].strip():
                    lines.append(f"{name}: available ({r['stdout'].strip()})")
                else:
                    lines.append(f"{name}: unavailable (expected binary: {binary})")
            return {"returncode": 0, "stdout": "\n".join(lines), "stderr": ""}

        if action == "configure_coding_agent":
            agent = self._require_str(params, "agent").strip().lower()
            provider = self._require_str(params, "provider").strip().lower()
            model = str(params.get("model") or "").strip()
            base_url = str(params.get("base_url") or "").strip()

            if agent != "cline":
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "Only 'cline' is supported for provider switching right now.",
                }

            api_key = str(params.get("api_key") or "").strip()
            if not api_key:
                api_key = self._default_api_key_for_provider(provider)
            if not api_key:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        f"No API key available for provider '{provider}'. "
                        "Pass api_key explicitly or configure matching environment key."
                    ),
                }

            if not model:
                model = self._default_model_for_provider(provider)

            binary = self._coding_bins.get("cline", "cline")
            if self.remote_os == "windows":
                binary, available = self._resolve_windows_binary(client, binary)
                if not available:
                    return {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "Cline CLI is not installed or not on PATH.",
                    }

            configured = self._configure_cline_provider(
                client=client,
                binary=binary,
                provider=provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
            )
            if configured["returncode"] != 0:
                return configured

            verify = self._run_command(client, [binary, "--version"], cwd=None, timeout=30)
            if verify["returncode"] == 0:
                configured["stdout"] = (
                    f"Cline configured to provider '{provider}'"
                    + (f" (model: {model})" if model else "")
                )
            else:
                configured["stdout"] = (
                    f"Cline auth command succeeded for provider '{provider}'"
                    + (f" (model: {model})" if model else "")
                )
            configured["stderr"] = ""
            return configured

        if action == "run_coding_agent":
            agent = self._require_str(params, "agent").strip().lower()
            prompt = self._require_str(params, "prompt")
            cwd = params.get("working_dir")
            timeout = params.get("timeout_seconds", 1800)
            backend_raw = str(params.get("backend") or "auto").strip().lower()
            model = str(params.get("model") or "").strip()
            base_url = str(
                params.get("base_url")
                or bot_cfg.CLAUDE_OLLAMA_BASE_URL
                or "http://localhost:11434"
            ).strip().rstrip("/")
            auto_pull_model = self._bool_param(
                params.get("auto_pull_model"),
                bool(bot_cfg.CLAUDE_OLLAMA_AUTO_PULL),
            )

            if agent not in self._coding_bins:
                allowed = ", ".join(sorted(self._coding_bins.keys()))
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"Unknown coding agent '{agent}'. Allowed: {allowed}",
                }
            if cwd is not None and not isinstance(cwd, str):
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "working_dir must be a string path.",
                }
            if not isinstance(timeout, int) or timeout < 30 or timeout > 3600:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "timeout_seconds must be an integer between 30 and 3600.",
                }

            resolved_backend, backend_error = self._resolve_backend(
                agent=agent,
                backend=backend_raw,
            )
            if backend_error:
                return {"returncode": 1, "stdout": "", "stderr": backend_error}

            if agent == "claude" and resolved_backend == "ollama":
                return self._run_coding_agent_claude_ollama(
                    client=client,
                    prompt=prompt,
                    cwd=cwd if isinstance(cwd, str) else None,
                    timeout=timeout,
                    model=model or bot_cfg.CLAUDE_OLLAMA_DEFAULT_MODEL,
                    base_url=base_url or "http://localhost:11434",
                    auto_pull_model=auto_pull_model,
                )

            return self._run_coding_agent_native(
                client=client,
                agent=agent,
                prompt=prompt,
                cwd=cwd if isinstance(cwd, str) else None,
                timeout=timeout,
                model=model,
            )

        if action == "docker_build":
            cwd = self._require_str(params, "working_dir")
            tag = str(params.get("tag", "chathan-build:latest"))
            if not re.match(r"^[a-zA-Z0-9._/:@-]+$", tag):
                return {"returncode": 1, "stdout": "", "stderr": "Invalid Docker tag characters."}
            return self._run_command(client, ["docker", "build", "-t", tag, "."], cwd=cwd, timeout=600)

        if action == "docker_compose_up":
            cwd = self._require_str(params, "working_dir")
            return self._run_command(client, ["docker", "compose", "up", "-d"], cwd=cwd, timeout=300)

        if action == "close_app":
            app_name = self._require_str(params, "app").lower()
            exe = self._closeable_apps.get(app_name)
            if not exe:
                allowed = ", ".join(sorted(self._closeable_apps.keys()))
                return {"returncode": 1, "stdout": "", "stderr": f"'{app_name}' is not in the allowed list. Allowed: {allowed}"}
            if self.remote_os == "windows":
                return self._run_command(client, ["taskkill", "/F", "/IM", exe], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": "close_app currently supports Windows remote hosts only."}

        if action == "exec_command":
            cwd     = self._require_str(params, "working_dir")
            command = self._require_str(params, "command")
            parts   = command.strip().split()
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
            remote_cwd = _norm_remote_path(cwd, self.remote_os)
            return self._run_command(client, parts, cwd=remote_cwd)

        return {"returncode": 1, "stdout": "", "stderr": f"Action '{action}' is not supported in SSH tunnel mode."}

    def _ensure_git_safe_directory(self, client: paramiko.SSHClient, cwd: str) -> None:
        """
        Avoid Git's "dubious ownership" protection on Windows SSH-created folders.
        """
        safe_path = _norm_remote_path(cwd, self.remote_os)
        check = self._run_command(
            client,
            ["git", "config", "--global", "--get-all", "safe.directory"],
            cwd=None,
            timeout=30,
        )
        if check["returncode"] == 0:
            lines = [
                line.strip().lower()
                for line in (check.get("stdout") or "").splitlines()
                if line.strip()
            ]
            if safe_path.lower() in lines:
                return

        _ = self._run_command(
            client,
            ["git", "config", "--global", "--add", "safe.directory", safe_path],
            cwd=None,
            timeout=30,
        )

    @staticmethod
    def _default_api_key_for_provider(provider: str) -> str:
        """
        Default api key for provider.
        
        Purpose:
        - Implement `_default_api_key_for_provider` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `provider`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        keys = {
            "gemini": bot_cfg.GOOGLE_AI_API_KEY,
            "deepseek": bot_cfg.DEEPSEEK_API_KEY,
            "groq": bot_cfg.GROQ_API_KEY,
            "openrouter": bot_cfg.OPENROUTER_API_KEY,
            "openai": bot_cfg.OPENAI_API_KEY,
            "anthropic": bot_cfg.ANTHROPIC_API_KEY,
        }
        return str(keys.get(provider, "") or "").strip()

    @staticmethod
    def _default_model_for_provider(provider: str) -> str:
        """
        Default model for provider.
        
        Purpose:
        - Implement `_default_model_for_provider` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `provider`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        defaults = {
            "gemini": os.environ.get("OPENCLAW_CLINE_GEMINI_MODEL", bot_cfg.GEMINI_MODEL or "gemini-2.0-flash"),
            "deepseek": os.environ.get("OPENCLAW_CLINE_DEEPSEEK_MODEL", "deepseek-chat"),
            "groq": os.environ.get("OPENCLAW_CLINE_GROQ_MODEL", "llama-3.3-70b-versatile"),
            "openrouter": os.environ.get(
                "OPENCLAW_CLINE_OPENROUTER_MODEL",
                bot_cfg.OPENROUTER_MODEL or "qwen/qwen3-next-80b-a3b-instruct:free",
            ),
            "openai": os.environ.get("OPENCLAW_CLINE_OPENAI_MODEL", "gpt-4o-mini"),
            "anthropic": os.environ.get("OPENCLAW_CLINE_ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        }
        return str(defaults.get(provider, "") or "").strip()

    def _configure_cline_provider(
        self,
        *,
        client: paramiko.SSHClient,
        binary: str,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
    ) -> dict[str, Any]:
        """
        Configure cline provider.
        
        Purpose:
        - Implement `_configure_cline_provider` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `binary`: input used by this function to compute or route work.
        - `provider`: input used by this function to compute or route work.
        - `api_key`: input used by this function to compute or route work.
        - `model`: input used by this function to compute or route work.
        - `base_url`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        args = [binary, "auth", "-p", provider, "-k", api_key]
        if model:
            args.extend(["-m", model])
        if base_url:
            args.extend(["-b", base_url])
        return self._run_command(client, args, cwd=None, timeout=120)

    @staticmethod
    def _is_retryable_cline_failure(result: dict[str, Any]) -> bool:
        """
        Is retryable cline failure.
        
        Purpose:
        - Implement `_is_retryable_cline_failure` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `result`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

        text = (
            f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
        ).lower()
        markers = (
            "not authenticated",
            "api request failed",
            "provider returned error",
            "rate limit",
            "rate_limit_exceeded",
            "resource_exhausted",
            "quota",
            "quota exceeded",
            "insufficient_quota",
            "status\":402",
            "status\":429",
            "status':402",
            "status':429",
            "spend limit",
            "billing",
            "too many requests",
        )
        return any(marker in text for marker in markers)

    def _run_cline_with_auto_switch(
        self,
        *,
        client: paramiko.SSHClient,
        binary: str,
        prompt: str,
        cwd: str | None,
        timeout: int,
        initial_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run cline with auto switch.
        
        Purpose:
        - Implement `_run_cline_with_auto_switch` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `binary`: input used by this function to compute or route work.
        - `prompt`: input used by this function to compute or route work.
        - `cwd`: input used by this function to compute or route work.
        - `timeout`: input used by this function to compute or route work.
        - `initial_result`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        attempted: list[str] = []
        last = dict(initial_result)
        run_args = [binary, *self._coding_prefix["cline"], prompt]

        for provider in self._cline_provider_priority:
            api_key = self._default_api_key_for_provider(provider)
            if not api_key:
                continue
            model = self._default_model_for_provider(provider)
            base_url = self._cline_provider_base_urls.get(provider, "")
            attempted.append(provider)

            configured = self._configure_cline_provider(
                client=client,
                binary=binary,
                provider=provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
            )
            if configured.get("returncode", 1) != 0:
                last = configured
                continue

            run = self._run_command(client, run_args, cwd=cwd, timeout=timeout)
            notice = (
                f"Notice: auto-switched Cline provider to {provider}"
                + (f" (model: {model})." if model else ".")
            )
            run["stdout"] = (f"{notice}\n{run.get('stdout', '').strip()}").strip()
            if run.get("returncode", 1) == 0:
                run["stderr"] = (run.get("stderr") or "").strip()
                return run
            last = run
            if not self._is_retryable_cline_failure(run):
                return run

        if attempted:
            suffix = f"Auto-switch attempted providers: {', '.join(attempted)}."
            err = (last.get("stderr") or "").strip()
            out = (last.get("stdout") or "").strip()
            if err:
                last["stderr"] = f"{err}\n{suffix}"
            elif out:
                last["stderr"] = suffix
            else:
                last["stderr"] = f"Cline failed and {suffix}"
        return last

    def _resolve_windows_binary(self, client: paramiko.SSHClient, binary: str) -> tuple[str, bool]:
        """
        Resolve windows binary.
        
        Purpose:
        - Implement `_resolve_windows_binary` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `binary`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `tuple[str, bool]` when available; otherwise side effects only.
        """

        b = (binary or "").strip()
        if not b:
            return binary, False

        # Explicit path already provided.
        if any(ch in b for ch in ("\\", "/", ":")):
            return b, self._remote_path_exists(client, b)

        # PATH lookup first.
        where_result = self._run_command(client, ["where", b], cwd=None)
        if where_result.get("returncode", 1) == 0:
            lines = [ln.strip() for ln in (where_result.get("stdout") or "").splitlines() if ln.strip()]
            if lines:
                return lines[0], True

        # Fallback: npm global bin for current user.
        npm_bin = rf"C:\Users\{self.username}\AppData\Roaming\npm"
        candidates = [
            rf"{npm_bin}\{b}.cmd",
            rf"{npm_bin}\{b}.exe",
            rf"{npm_bin}\{b}",
        ]
        for cand in candidates:
            if self._remote_path_exists(client, cand):
                return cand, True

        return b, False

    @staticmethod
    def _remote_path_exists(client: paramiko.SSHClient, path: str) -> bool:
        """
        Remote path exists.
        
        Purpose:
        - Implement `_remote_path_exists` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `path`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

        sftp = client.open_sftp()
        try:
            sftp.stat(path)
            return True
        except OSError:
            return False
        finally:
            sftp.close()

    def _norm_join(self, parent: str | None, child: str) -> str:
        """
        Norm join.
        
        Purpose:
        - Implement `_norm_join` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `parent`: input used by this function to compute or route work.
        - `child`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `str` when available; otherwise side effects only.
        """

        if not parent:
            return child
        if self.remote_os == "windows":
            return str(PureWindowsPath(parent) / child)
        return str(PurePosixPath(parent) / child)

    def _file_read(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:
        """
        File read.
        
        Purpose:
        - Implement `_file_read` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        filepath = self._require_str(params, "file")
        sftp = client.open_sftp()
        try:
            with sftp.open(filepath, "r") as fh:
                content = fh.read().decode("utf-8", errors="replace")
            if len(content) > 65536:
                content = content[:65536] + "\n... (truncated at 64 KB)"
            return {"returncode": 0, "stdout": content, "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                ps = (
                    f"$p={_ps_quote(filepath)}; "
                    "if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { Write-Error \"File not found: $p\"; exit 1 }; "
                    "$c=[System.IO.File]::ReadAllText($p,[System.Text.Encoding]::UTF8); "
                    "if ($c.Length -gt 65536) { $c.Substring(0,65536) + [Environment]::NewLine + '... (truncated at 64 KB)' } else { $c }"
                )
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _file_write(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:
        """
        File write.
        
        Purpose:
        - Implement `_file_write` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        filepath = self._require_str(params, "file")
        content = params.get("content", "")
        if not isinstance(content, str):
            return {"returncode": 1, "stdout": "", "stderr": "content must be a string."}
        if len(content.encode("utf-8")) > 1_048_576:
            return {"returncode": 1, "stdout": "", "stderr": "Content exceeds 1 MB limit."}

        sftp = client.open_sftp()
        try:
            parent = str(PureWindowsPath(filepath).parent) if self.remote_os == "windows" else str(PurePosixPath(filepath).parent)
            self._sftp_makedirs(sftp, parent)
            with sftp.open(filepath, "w") as fh:
                fh.write(content)
            return {"returncode": 0, "stdout": f"Wrote {len(content)} bytes to {filepath}.", "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
                ps = (
                    f"$p={_ps_quote(filepath)}; "
                    "$d=Split-Path -Parent $p; if ($d) { New-Item -ItemType Directory -Path $d -Force | Out-Null }; "
                    f"$bytes=[System.Convert]::FromBase64String('{encoded}'); "
                    "[System.IO.File]::WriteAllBytes($p,$bytes);"
                )
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _create_directory(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:
        """
        Create directory.
        
        Purpose:
        - Implement `_create_directory` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        directory = self._require_str(params, "directory")
        sftp = client.open_sftp()
        try:
            self._sftp_makedirs(sftp, directory)
            return {"returncode": 0, "stdout": f"Created {directory}", "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                ps = f"$d={_ps_quote(directory)}; New-Item -ItemType Directory -Path $d -Force | Out-Null; Write-Output \"Created $d\""
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _sftp_makedirs(self, sftp: paramiko.SFTPClient, path: str) -> None:
        """
        Sftp makedirs.
        
        Purpose:
        - Implement `_sftp_makedirs` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `sftp`: input used by this function to compute or route work.
        - `path`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        if not path:
            return
        if self.remote_os == "windows":
            parts = list(PureWindowsPath(path).parts)
            if len(parts) == 1 and parts[0].endswith("\\"):
                return
            current = parts[0]
            for p in parts[1:]:
                current = str(PureWindowsPath(current) / p)
                try:
                    sftp.stat(current)
                except OSError:
                    sftp.mkdir(current)
            return

        parts = list(PurePosixPath(path).parts)
        current = ""
        for p in parts:
            current = str(PurePosixPath(current) / p)
            try:
                sftp.stat(current)
            except OSError:
                sftp.mkdir(current)

    def _list_directory(self, client: paramiko.SSHClient, params: dict[str, Any]) -> dict[str, Any]:
        """
        List directory.
        
        Purpose:
        - Implement `_list_directory` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `client`: input used by this function to compute or route work.
        - `params`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `dict[str, Any]` when available; otherwise side effects only.
        """

        directory = self._require_str(params, "directory")
        recursive = params.get("recursive", False) is True
        sftp = client.open_sftp()
        try:
            lines: list[str] = []
            self._walk_sftp(sftp, directory, recursive, 0, lines, {"count": 0})
            return {"returncode": 0, "stdout": "\n".join(lines), "stderr": ""}
        except OSError as exc:
            if self.remote_os == "windows":
                if recursive:
                    ps = (
                        f"$d={_ps_quote(directory)}; "
                        "Get-ChildItem -LiteralPath $d -Recurse -Force | "
                        "Select-Object FullName,Length,PSIsContainer | "
                        "ForEach-Object { if ($_.PSIsContainer) { \"[DIR] $($_.FullName)\" } else { \"$($_.FullName)  ($($_.Length) bytes)\" } }"
                    )
                else:
                    ps = (
                        f"$d={_ps_quote(directory)}; "
                        "Get-ChildItem -LiteralPath $d -Force | "
                        "ForEach-Object { if ($_.PSIsContainer) { \"[DIR] $($_.Name)/\" } else { \"$($_.Name)  ($($_.Length) bytes)\" } }"
                    )
                return self._run_command(client, ["powershell", "-NoProfile", "-Command", ps], cwd=None)
            return {"returncode": 1, "stdout": "", "stderr": str(exc)}
        finally:
            sftp.close()

    def _walk_sftp(
        self,
        sftp: paramiko.SFTPClient,
        directory: str,
        recursive: bool,
        depth: int,
        out: list[str],
        state: dict[str, int],
    ) -> None:
        """
        Walk sftp.
        
        Purpose:
        - Implement `_walk_sftp` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `sftp`: input used by this function to compute or route work.
        - `directory`: input used by this function to compute or route work.
        - `recursive`: input used by this function to compute or route work.
        - `depth`: input used by this function to compute or route work.
        - `out`: input used by this function to compute or route work.
        - `state`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `None` when available; otherwise side effects only.
        """

        max_depth = 3
        max_entries = 500

        entries = sorted(sftp.listdir_attr(directory), key=lambda e: e.filename.lower())
        for e in entries:
            if state["count"] >= max_entries:
                out.append("... (truncated)")
                return
            name = e.filename
            path = self._norm_join(directory, name)
            prefix = "  " * depth
            if stat.S_ISDIR(e.st_mode):
                out.append(f"{prefix}[DIR] {name}/")
                if recursive and depth < max_depth:
                    self._walk_sftp(sftp, path, True, depth + 1, out, state)
            else:
                out.append(f"{prefix}{name}  ({int(e.st_size)} bytes)")
            state["count"] += 1


_SSH_EXECUTOR: SSHTunnelExecutor | None = None


def get_ssh_executor() -> SSHTunnelExecutor:
    """
    Get ssh executor.
    
    Purpose:
    - Implement `get_ssh_executor` within this module's workflow.
    - Keep behavior localized so callers have one stable entrypoint.
    
    How it works:
    - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
    - Produces deterministic return data or side effects expected by calling code.
    
    Why this exists:
    - Prevents duplicated logic in upstream orchestration paths.
    - Improves debuggability by centralizing this behavior in one named function.
    
    Parameters:
    - None.
    
    Returns:
    - Return value typed as `SSHTunnelExecutor` when available; otherwise side effects only.
    """

    global _SSH_EXECUTOR
    if _SSH_EXECUTOR is None:
        _SSH_EXECUTOR = SSHTunnelExecutor()
    return _SSH_EXECUTOR

