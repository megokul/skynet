from __future__ import annotations

import base64
import re
import shlex
from pathlib import PurePosixPath, PureWindowsPath

import gateway_config as bot_cfg


def env_bool(name: str, default: bool = False) -> bool:
    raw = bot_cfg.get_str(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = bot_cfg.get_str(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def parse_roots(raw: str, remote_os: str) -> list[str]:
    parts = [part.strip() for part in raw.replace(",", ";").split(";") if part.strip()]
    if parts:
        return parts
    if remote_os == "windows":
        return [r"%USERPROFILE%\Projects", r"%USERPROFILE%\Documents"]
    return ["/home", "/tmp"]


def parse_provider_priority(raw: str) -> list[str]:
    allowed = {"gemini", "deepseek", "groq", "openrouter", "openai", "anthropic"}
    parts = [part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip()]
    ordered: list[str] = []
    for part in parts:
        if part in allowed and part not in ordered:
            ordered.append(part)
    if ordered:
        return ordered
    return ["gemini", "deepseek", "groq", "openrouter"]


def norm_remote_path(path: str, remote_os: str) -> str:
    if remote_os == "windows":
        return str(PureWindowsPath(path))
    return str(PurePosixPath(path))


def is_allowed_path(path: str, allowed_roots: list[str], remote_os: str) -> bool:
    candidate = norm_remote_path(path, remote_os)
    if remote_os == "windows":
        cand = candidate.replace("/", "\\").rstrip("\\").lower()
        for root in allowed_roots:
            normalized_root = norm_remote_path(root, remote_os).replace("/", "\\").rstrip("\\").lower()
            if cand == normalized_root or cand.startswith(normalized_root + "\\"):
                return True
        return False
    cand = candidate.rstrip("/")
    for root in allowed_roots:
        normalized_root = norm_remote_path(root, remote_os).rstrip("/")
        if cand == normalized_root or cand.startswith(normalized_root + "/"):
            return True
    return False


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_windows_command(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    if not args:
        raise ValueError("args must not be empty")
    script_lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
    ]
    if cwd:
        script_lines.append(f"Set-Location -LiteralPath {ps_quote(cwd)}")
    if env:
        for key, value in env.items():
            script_lines.append(f"$env:{key} = {ps_quote(str(value))}")
    script_lines.append("$__args = @()")
    for arg in args:
        encoded_arg = base64.b64encode(str(arg).encode("utf-8")).decode("ascii")
        script_lines.append(
            "$__args += [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{encoded_arg}'))"
        )
    script_lines.append("$__cmd = $__args[0]")
    script_lines.append("$__rest = @()")
    script_lines.append("if ($__args.Length -gt 1) { $__rest = $__args[1..($__args.Length-1)] }")
    script_lines.append("& $__cmd @__rest")
    script_lines.append("$code = $LASTEXITCODE")
    script_lines.append("if ($null -eq $code) { $code = 0 }")
    script_lines.append("exit $code")
    script = "\n".join(script_lines)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return (
        "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}"
    )


def sanitize_powershell_output(text: str) -> str:
    if not text:
        return text
    cleaned = text.replace("_x000D__x000A_", "\n").replace("_x000D_", "\r").replace("_x000A_", "\n")
    cleaned = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", cleaned)
    cleaned = re.sub(r"\x1B\][^\x07]*\x07", "", cleaned)
    cleaned = cleaned.replace("\x1b", "")
    if "<Objs Version=" in cleaned and "</Objs>" in cleaned:
        parts = re.findall(r"<S S=\"(?:Error|Warning|Verbose)\">(.*?)</S>", cleaned, flags=re.DOTALL)
        if parts:
            cleaned = "\n".join(parts)
        else:
            cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return cleaned.strip()


def build_linux_command(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    run = " ".join(shlex.quote(str(arg)) for arg in args)
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
