"""
SKYNET Bot â€” Coding Orchestration

Handles the full coding loop after a project is saved:
  1. User taps "Start Coding"
  2. Bot asks about GitHub repo / project folder setup (buttons)
  3. User confirms â†’ background asyncio.Task starts
  4. Loop: LLM breaks plan into milestones â†’ user approves each â†’ CLAW worker executes
  5. Progress notifications after each milestone
  6. /status command shows live dashboard

Key design: _coding_loop runs as a background asyncio.Task.
Milestone approvals are signalled via asyncio.Event stored in bot_data.
"""
from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import re
from typing import Any

import config as cfg
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import (
    CB_CODING_RETRY_PREFIX,
    CB_CODING_GITHUB_SKIP,
    CB_CODING_GITHUB_YES,
    coding_github_setup,
    main_menu,
    milestone_review,
    retry_coding,
    run_project,
)
from bot.state import KEY_DB, KEY_ROUTER
from db.store import (
    create_task,
    create_task_gate_result,
    delete_task_gate_results,
    ensure_user,
    get_project,
    list_projects,
    list_tasks,
    update_task_status,
)
from gateway import is_worker_available, send_action

logger = logging.getLogger("skynet.bot.coding")

# ---------------------------------------------------------------------------
# Coding system prompt â€” shared between router-based and Ollama SSH paths.
# ---------------------------------------------------------------------------
_CODING_SYSTEM_PROMPT = (
    "You are an expert coding agent. Implement the task completely.\n"
    "For EVERY file you create or modify, output it in a fenced code block.\n"
    "The opening fence MUST be the filename (not a language name).\n\n"
    "Example:\n"
    "```main.py\n"
    "print('hello')\n"
    "```\n\n"
    "Rules:\n"
    "- The opening ``` MUST be followed by the actual filename, NEVER a language like python or js.\n"
    "- Write complete, working code â€” no placeholders, no '...'.\n"
    "- Include every file needed (source, config, requirements, etc.).\n"
    "- Do NOT add explanations outside code blocks.\n"
    "- Name the main entry-point file after the project name given in the task.\n"
)

# Language tag â†’ file extension for fallback naming.
_LANG_EXT: dict[str, str] = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
    "typescript": ".ts", "ts": ".ts", "java": ".java", "c": ".c",
    "cpp": ".cpp", "c++": ".cpp", "go": ".go", "rust": ".rs",
    "ruby": ".rb", "bash": ".sh", "sh": ".sh", "html": ".html",
    "css": ".css", "json": ".json", "yaml": ".yaml", "yml": ".yaml",
    "toml": ".toml", "sql": ".sql",
}

_QUALITY_PROFILE_LEGACY = "legacy"
_QUALITY_PROFILE_STRICT = "strict"
_CODING_PROFILE_LEGACY = "legacy"
_CODING_PROFILE_CLAUDE_OLLAMA = "claude_ollama"
_RUN_CONTRACT_FILE = "skynet_run.json"
_ALLOWED_INTERPRETERS = {"python", "python3", "node"}


def _parse_code_blocks(text: str) -> list[tuple[str, str]]:
    """Parse fenced code blocks from LLM output into (filename, content) pairs."""
    pattern = re.compile(r"```([^\n`]+)\n(.*?)```", re.DOTALL)

    file_blocks: list[tuple[str, str]] = []
    lang_blocks: list[tuple[str, str]] = []
    for match in pattern.finditer(text):
        tag = match.group(1).strip()
        content = match.group(2)
        if "." in tag or "/" in tag or "\\" in tag:
            file_blocks.append((tag, content))
        else:
            lang_blocks.append((tag, content))

    # If no file-path blocks, convert language-tag blocks using fallback names.
    if not file_blocks and lang_blocks:
        for idx, (tag, content) in enumerate(lang_blocks):
            ext = _LANG_EXT.get(tag.lower(), f".{tag.lower()}")
            fallback = f"main{ext}" if idx == 0 else f"file{idx}{ext}"
            file_blocks.append((fallback, content))

    return file_blocks

# bot_data keys for inter-handler signalling
_MS_EVENT_KEY    = "ms_event_{uid}"
_MS_DECISION_KEY = "ms_decision_{uid}"
_ACTIVE_LOOP_KEY = "coding_loop_{uid}"   # stores the asyncio.Task
_GITHUB_PREF_KEY = "coding_github_pref_{uid}_{pid}"

# User_data key set by project handler after save
_PROJECT_ID_KEY = "last_project_id"
_CODING_PID_KEY = "coding_project_id"


def _run_files_key(user_id: int, project_id: str) -> str:
    return f"run_files_{user_id}_{project_id}"


def _run_contract_key(user_id: int, project_id: str) -> str:
    return f"run_contract_{user_id}_{project_id}"


def _quality_profile(project: dict[str, Any] | None) -> str:
    raw = str((project or {}).get("quality_profile") or _QUALITY_PROFILE_LEGACY).strip().lower()
    if raw not in {_QUALITY_PROFILE_LEGACY, _QUALITY_PROFILE_STRICT}:
        return _QUALITY_PROFILE_LEGACY
    return raw


def _coding_profile(project: dict[str, Any] | None) -> str:
    raw = str(
        (project or {}).get("coding_profile")
        or cfg.CODING_DEFAULT_PROFILE
        or _CODING_PROFILE_LEGACY
    ).strip().lower()
    if raw not in {_CODING_PROFILE_LEGACY, _CODING_PROFILE_CLAUDE_OLLAMA}:
        return _CODING_PROFILE_LEGACY
    return raw


def _uses_claude_ollama(project: dict[str, Any] | None) -> bool:
    return _coding_profile(project) == _CODING_PROFILE_CLAUDE_OLLAMA


def _is_strict_project(project: dict[str, Any] | None) -> bool:
    if not cfg.STRICT_QUALITY_GATES_ENABLED:
        return False
    return _quality_profile(project) == _QUALITY_PROFILE_STRICT


def _action_error_text(result: dict[str, Any], action: str) -> str:
    if result.get("status") == "error":
        return str(result.get("error") or f"{action} failed").strip()
    inner = result.get("result", result)
    return str(
        inner.get("stderr")
        or inner.get("stdout")
        or f"{action} failed"
    ).strip()


def _action_inner_result(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("result", result)


def _action_exit_code(result: dict[str, Any]) -> int:
    inner = _action_inner_result(result)
    return int(inner.get("returncode", inner.get("exit_code", 0)))


def _action_excerpt(result: dict[str, Any], *, limit: int = 240) -> str:
    inner = _action_inner_result(result)
    text = str(inner.get("stderr") or inner.get("stdout") or "").strip()
    if not text:
        text = "(no output)"
    return text[:limit]


def _is_infra_error(message: str) -> bool:
    lower = (message or "").lower()
    infra_markers = (
        "ssh action failed",
        "no agent connected",
        "agent disconnected",
        "worker not connected",
        "connection refused",
        "timed out",
        "timeout",
        "network is unreachable",
        "transport",
        "socket",
        "authentication failed",
        "could not resolve",
    )
    return any(marker in lower for marker in infra_markers)


def _is_manifest_missing_error(message: str) -> bool:
    lower = (message or "").lower()
    markers = (
        "no such file",
        "cannot find path",
        "does not exist",
        "not found",
    )
    return any(marker in lower for marker in markers)


def _is_safe_relative_path(path: str) -> bool:
    raw = (path or "").strip()
    if not raw:
        return False
    if any(ord(ch) < 32 for ch in raw):
        return False
    norm = raw.replace("\\", "/")
    if norm.startswith("/") or norm.startswith("\\"):
        return False
    if re.match(r"^[A-Za-z]:", norm):
        return False
    parts = [part for part in norm.split("/") if part not in ("", ".")]
    if not parts:
        return False
    if any(part == ".." for part in parts):
        return False
    return True


def _normalize_manifest_path(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")


def _build_manifest_command(
    *,
    interpreter: str,
    entrypoint: str,
    args: list[str],
) -> str:
    parts = [interpreter, entrypoint, *args]
    return " ".join(parts)


def _validate_cached_run_contract(contract: Any) -> dict[str, Any] | None:
    if not isinstance(contract, dict):
        return None
    interpreter = str(contract.get("interpreter") or "").strip().lower()
    entrypoint = str(contract.get("entrypoint") or "").strip()
    command = str(contract.get("command") or "").strip()
    args = contract.get("args")
    if interpreter not in _ALLOWED_INTERPRETERS:
        return None
    if not _is_safe_relative_path(entrypoint):
        return None
    if not isinstance(args, list) or any(
        (not isinstance(token, str) or not token or any(ch.isspace() for ch in token))
        for token in args
    ):
        return None
    if not command.startswith(f"{interpreter} "):
        return None
    return {
        "interpreter": interpreter,
        "entrypoint": _normalize_manifest_path(entrypoint),
        "args": args,
        "command": command,
    }


def _has_cached_run_contract(
    *,
    bot_data: dict[str, Any],
    user_id: int,
    project_id: str,
) -> bool:
    key = _run_contract_key(user_id, project_id)
    return _validate_cached_run_contract(bot_data.get(key)) is not None


def _agent_status_line(report: str, agent: str) -> str | None:
    target = f"{agent.strip().lower()}:"
    for raw in (report or "").splitlines():
        line = raw.strip()
        if line.lower().startswith(target):
            return line
    return None


def _agent_is_explicitly_unavailable(report: str, agent: str) -> bool:
    line = _agent_status_line(report, agent)
    if not line:
        return False
    lower = line.lower()
    return "unavailable" in lower and "available" in lower


async def _preflight_coding_environment(
    *,
    project: dict[str, Any],
) -> tuple[bool, str]:
    """
    Validate coding prerequisites before milestone execution.

    For claude_ollama projects we require the worker to report Claude CLI
    availability. If telemetry is absent, do not hard-fail to keep backward
    compatibility with older workers/tests.
    """
    if not _uses_claude_ollama(project):
        return True, ""

    try:
        result = await send_action(
            "check_coding_agents",
            {},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        return False, f"Preflight check failed: {type(exc).__name__}: {exc}"

    if result.get("status") == "error":
        return False, _action_error_text(result, "check_coding_agents")

    report = str(_action_inner_result(result).get("stdout") or "")
    if _agent_is_explicitly_unavailable(report, "claude"):
        detail = _agent_status_line(report, "claude") or "claude: unavailable"
        return (
            False,
            f"{detail}. Install Claude Code or set OPENCLAW_SSH_CLAUDE_BIN.",
        )

    return True, ""


async def _record_gate_result(
    *,
    db,
    task_id: int,
    attempt: int,
    gate_name: str,
    status: str,
    command: str = "",
    summary: str = "",
) -> None:
    await create_task_gate_result(
        db,
        task_id=task_id,
        attempt=attempt,
        gate_name=gate_name,
        status=status,
        command=command,
        summary=summary[:500],
    )


def _runtime_from_contract(contract: dict[str, Any]) -> str:
    interpreter = str(contract.get("interpreter") or "").strip().lower()
    if interpreter in {"python", "python3"}:
        return "python"
    return "node"


def _has_detected_tests(*, runtime: str, files: list[str]) -> bool:
    for path in files:
        lower = _normalize_slashes(path).lower()
        base = lower.rsplit("/", 1)[-1]
        if runtime == "python":
            if ("/tests/" in f"/{lower}") and lower.endswith(".py"):
                return True
            if base.startswith("test_") and base.endswith(".py"):
                return True
            if base.endswith("_test.py"):
                return True
        else:
            if "/tests/" in f"/{lower}" and lower.endswith((".js", ".ts", ".mjs", ".cjs")):
                return True
            if base.endswith((".test.js", ".spec.js", ".test.ts", ".spec.ts")):
                return True
    return False


async def _list_project_files(
    *,
    working_dir: str,
) -> tuple[list[str], str, str, bool]:
    command = f"list_directory --recursive {working_dir}"
    try:
        list_result = await send_action(
            "list_directory",
            {"directory": working_dir, "recursive": True},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return [], command, message, True

    if list_result.get("status") == "error":
        message = _action_error_text(list_result, "list_directory")
        return [], command, message, _is_infra_error(message)

    if _action_exit_code(list_result) != 0:
        message = _action_excerpt(list_result)
        return [], command, message, _is_infra_error(message)

    listing = str(_action_inner_result(list_result).get("stdout") or "")
    files = _extract_file_paths_from_listing(listing, working_dir=working_dir)
    return files, command, "", False


async def _load_and_validate_run_contract(
    *,
    working_dir: str,
) -> tuple[dict[str, Any] | None, str, bool]:
    manifest_path = f"{working_dir}/{_RUN_CONTRACT_FILE}"
    try:
        manifest_result = await send_action(
            "file_read",
            {"file": manifest_path},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return None, message, True

    if manifest_result.get("status") == "error":
        message = _action_error_text(manifest_result, "file_read")
        return None, message, _is_infra_error(message)

    if _action_exit_code(manifest_result) != 0:
        message = _action_excerpt(manifest_result)
        infra = _is_infra_error(message) and not _is_manifest_missing_error(message)
        return None, message, infra

    manifest_raw = str(_action_inner_result(manifest_result).get("stdout") or "")
    try:
        payload = json.loads(manifest_raw)
    except Exception as exc:
        return None, f"Invalid {_RUN_CONTRACT_FILE}: {exc}", False

    if not isinstance(payload, dict):
        return None, f"Invalid {_RUN_CONTRACT_FILE}: expected a JSON object", False

    interpreter = str(payload.get("interpreter") or "").strip().lower()
    if interpreter not in _ALLOWED_INTERPRETERS:
        return None, "run_contract.interpreter must be python, python3, or node", False

    entrypoint_raw = str(payload.get("entrypoint") or "").strip()
    if not _is_safe_relative_path(entrypoint_raw):
        return None, "run_contract.entrypoint must be a safe relative path", False
    entrypoint = _normalize_manifest_path(entrypoint_raw)

    args = payload.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list):
        return None, "run_contract.args must be an array when provided", False
    clean_args: list[str] = []
    for token in args:
        if not isinstance(token, str):
            return None, "run_contract.args must contain only strings", False
        token = token.strip()
        if not token:
            return None, "run_contract.args cannot contain empty tokens", False
        if any(ch.isspace() for ch in token):
            return None, "run_contract.args tokens cannot include whitespace", False
        clean_args.append(token)

    entrypoint_path = f"{working_dir}/{entrypoint}"
    try:
        entry_result = await send_action(
            "file_read",
            {"file": entrypoint_path},
            timeout=20,
            confirmed=True,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return None, message, True

    if entry_result.get("status") == "error":
        message = _action_error_text(entry_result, "file_read")
        return None, message, _is_infra_error(message)

    if _action_exit_code(entry_result) != 0:
        return None, f"Entrypoint file not found: {entrypoint}", False

    contract = {
        "interpreter": interpreter,
        "entrypoint": entrypoint,
        "args": clean_args,
        "command": _build_manifest_command(
            interpreter=interpreter,
            entrypoint=entrypoint,
            args=clean_args,
        ),
    }
    return contract, "run contract validated", False


async def _run_quality_fix_pass(
    *,
    project: dict[str, Any],
    milestone_text: str,
    working_dir: str,
    failing_gates: list[dict[str, str]],
) -> list[str]:
    failure_lines = []
    for gate in failing_gates:
        gate_name = gate.get("gate_name", "unknown")
        command = gate.get("command", "")
        summary = gate.get("summary", "")
        line = f"- {gate_name}"
        if command:
            line += f" | cmd: {command}"
        if summary:
            line += f" | error: {summary}"
        failure_lines.append(line)

    fix_prompt = (
        f"Project: {project['name']} ({project['project_type']})\n"
        f"Working directory: {working_dir}\n\n"
        f"Milestone task:\n{milestone_text}\n\n"
        "The previous implementation failed strict quality gates. "
        "Fix the code and update any needed files so all gates pass:\n"
        + "\n".join(failure_lines)
        + "\n\nRequirements:\n"
          f"- Include a valid {_RUN_CONTRACT_FILE}.\n"
          "- Add runnable tests if missing.\n"
          "- Ensure lint and tests pass.\n"
          "- Return complete files only."
    )

    payload: dict[str, Any] = {
        "agent": "claude",
        "prompt": fix_prompt,
        "working_dir": working_dir,
        "timeout_seconds": 1800,
    }

    if _uses_claude_ollama(project):
        if not cfg.ANTHROPIC_API_KEY:
            raise RuntimeError("FALLBACK_UNAVAILABLE: ANTHROPIC_API_KEY is not configured")
        payload["backend"] = "native"
        if cfg.CLAUDE_OLLAMA_DEFAULT_MODEL:
            payload["model"] = cfg.CLAUDE_OLLAMA_DEFAULT_MODEL
    else:
        payload["backend"] = "auto"

    result = await send_action(
        "run_coding_agent",
        payload,
        timeout=1800,
        confirmed=True,
    )
    if result.get("status") == "error":
        message = _action_error_text(result, "run_coding_agent")
        raise RuntimeError(message)

    inner = _action_inner_result(result)
    return_code = int(inner.get("returncode", inner.get("exit_code", 0)))
    if return_code != 0:
        raise RuntimeError(_action_excerpt(result))

    files_written = inner.get("files_written") or []
    if isinstance(files_written, list):
        return [str(path).strip() for path in files_written if str(path).strip()]
    return []


async def _run_strict_quality_gates(
    *,
    db,
    task_id: int,
    project: dict[str, Any],
    milestone_text: str,
    working_dir: str,
) -> dict[str, Any]:
    await delete_task_gate_results(db, task_id=task_id)

    max_retries = max(0, int(cfg.STRICT_QUALITY_GATES_FIX_RETRIES))
    max_attempts = 1 + max_retries
    last_contract: dict[str, Any] | None = None

    for attempt in range(1, max_attempts + 1):
        failed_gates: list[dict[str, str]] = []
        infra_failure = False

        preflight_cmd = f"list_directory {working_dir}"
        try:
            preflight = await send_action(
                "list_directory",
                {"directory": working_dir},
                timeout=15,
                confirmed=True,
            )
        except Exception as exc:
            summary = f"{type(exc).__name__}: {exc}"
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="infra_preflight",
                status="failed",
                command=preflight_cmd,
                summary=summary,
            )
            return {
                "passed": False,
                "infra_failure": True,
                "error_message": f"INFRA_FAILURE: {summary[:220]}",
                "failed_gate_names": ["infra_preflight"],
                "run_contract": None,
                "fix_written_files": [],
            }

        if preflight.get("status") == "error" or _action_exit_code(preflight) != 0:
            summary = _action_error_text(preflight, "list_directory")
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="infra_preflight",
                status="failed",
                command=preflight_cmd,
                summary=summary,
            )
            return {
                "passed": False,
                "infra_failure": True,
                "error_message": f"INFRA_FAILURE: {summary[:220]}",
                "failed_gate_names": ["infra_preflight"],
                "run_contract": None,
                "fix_written_files": [],
            }

        await _record_gate_result(
            db=db,
            task_id=task_id,
            attempt=attempt,
            gate_name="infra_preflight",
            status="passed",
            command=preflight_cmd,
            summary="worker connectivity OK",
        )

        run_contract, run_summary, run_infra = await _load_and_validate_run_contract(
            working_dir=working_dir,
        )
        if run_contract is None:
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="run_contract",
                status="failed",
                command=f"file_read {_RUN_CONTRACT_FILE}",
                summary=run_summary,
            )
            if run_infra:
                return {
                    "passed": False,
                    "infra_failure": True,
                    "error_message": f"INFRA_FAILURE: {run_summary[:220]}",
                    "failed_gate_names": ["run_contract"],
                    "run_contract": None,
                    "fix_written_files": [],
                }
            failed_gates.append(
                {
                    "gate_name": "run_contract",
                    "command": f"file_read {_RUN_CONTRACT_FILE}",
                    "summary": run_summary,
                }
            )
            last_contract = None
        else:
            await _record_gate_result(
                db=db,
                task_id=task_id,
                attempt=attempt,
                gate_name="run_contract",
                status="passed",
                command=f"file_read {_RUN_CONTRACT_FILE}",
                summary=run_summary,
            )
            last_contract = run_contract

        if not last_contract:
            for gate_name in ("lint", "tests", "smoke"):
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name=gate_name,
                    status="skipped",
                    command="",
                    summary="Skipped because run_contract failed",
                )
        else:
            runtime = _runtime_from_contract(last_contract)

            lint_linter = "ruff" if runtime == "python" else "eslint"
            lint_cmd = (
                "python -m ruff check ." if lint_linter == "ruff" else "npx eslint ."
            )
            try:
                lint_result = await send_action(
                    "lint_project",
                    {"working_dir": working_dir, "linter": lint_linter},
                    timeout=120,
                    confirmed=True,
                )
            except Exception as exc:
                lint_summary = f"{type(exc).__name__}: {exc}"
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="lint",
                    status="failed",
                    command=lint_cmd,
                    summary=lint_summary,
                )
                infra_failure = True
                failed_gates.append(
                    {"gate_name": "lint", "command": lint_cmd, "summary": lint_summary}
                )
            else:
                lint_failed = (
                    lint_result.get("status") == "error" or _action_exit_code(lint_result) != 0
                )
                lint_summary = (
                    _action_error_text(lint_result, "lint_project")
                    if lint_failed
                    else _action_excerpt(lint_result)
                )
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="lint",
                    status="failed" if lint_failed else "passed",
                    command=lint_cmd,
                    summary=lint_summary,
                )
                if lint_failed:
                    if _is_infra_error(lint_summary):
                        infra_failure = True
                    failed_gates.append(
                        {"gate_name": "lint", "command": lint_cmd, "summary": lint_summary}
                    )

            test_runner = "pytest" if runtime == "python" else "npm"
            tests_cmd = "python -m pytest --tb=short -q" if runtime == "python" else "npm test"
            files, tests_scan_cmd, list_error, list_infra = await _list_project_files(
                working_dir=working_dir
            )
            if list_infra:
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="tests",
                    status="failed",
                    command=tests_scan_cmd,
                    summary=list_error or "Failed to list files for test discovery",
                )
                infra_failure = True
                failed_gates.append(
                    {
                        "gate_name": "tests",
                        "command": tests_scan_cmd,
                        "summary": list_error or "Failed to list files for test discovery",
                    }
                )
            elif list_error:
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="tests",
                    status="failed",
                    command=tests_scan_cmd,
                    summary=list_error,
                )
                failed_gates.append(
                    {
                        "gate_name": "tests",
                        "command": tests_scan_cmd,
                        "summary": list_error,
                    }
                )
            elif not _has_detected_tests(runtime=runtime, files=files):
                summary = "No tests detected; strict mode requires tests."
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="tests",
                    status="failed",
                    command=tests_scan_cmd,
                    summary=summary,
                )
                failed_gates.append(
                    {
                        "gate_name": "tests",
                        "command": tests_scan_cmd,
                        "summary": summary,
                    }
                )
            else:
                try:
                    tests_result = await send_action(
                        "run_tests",
                        {"working_dir": working_dir, "runner": test_runner},
                        timeout=300,
                        confirmed=True,
                    )
                except Exception as exc:
                    tests_summary = f"{type(exc).__name__}: {exc}"
                    await _record_gate_result(
                        db=db,
                        task_id=task_id,
                        attempt=attempt,
                        gate_name="tests",
                        status="failed",
                        command=tests_cmd,
                        summary=tests_summary,
                    )
                    infra_failure = True
                    failed_gates.append(
                        {
                            "gate_name": "tests",
                            "command": tests_cmd,
                            "summary": tests_summary,
                        }
                    )
                else:
                    tests_failed = (
                        tests_result.get("status") == "error"
                        or _action_exit_code(tests_result) != 0
                    )
                    tests_summary = (
                        _action_error_text(tests_result, "run_tests")
                        if tests_failed
                        else _action_excerpt(tests_result)
                    )
                    await _record_gate_result(
                        db=db,
                        task_id=task_id,
                        attempt=attempt,
                        gate_name="tests",
                        status="failed" if tests_failed else "passed",
                        command=tests_cmd,
                        summary=tests_summary,
                    )
                    if tests_failed:
                        if _is_infra_error(tests_summary):
                            infra_failure = True
                        failed_gates.append(
                            {
                                "gate_name": "tests",
                                "command": tests_cmd,
                                "summary": tests_summary,
                            }
                        )

            smoke_cmd = str(last_contract.get("command") or "").strip()
            try:
                smoke_result = await send_action(
                    "exec_command",
                    {"command": smoke_cmd, "working_dir": working_dir},
                    timeout=120,
                    confirmed=True,
                )
            except Exception as exc:
                smoke_summary = f"{type(exc).__name__}: {exc}"
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="smoke",
                    status="failed",
                    command=smoke_cmd,
                    summary=smoke_summary,
                )
                infra_failure = True
                failed_gates.append(
                    {"gate_name": "smoke", "command": smoke_cmd, "summary": smoke_summary}
                )
            else:
                smoke_failed = (
                    smoke_result.get("status") == "error"
                    or _action_exit_code(smoke_result) != 0
                )
                smoke_summary = (
                    _action_error_text(smoke_result, "exec_command")
                    if smoke_failed
                    else _action_excerpt(smoke_result)
                )
                await _record_gate_result(
                    db=db,
                    task_id=task_id,
                    attempt=attempt,
                    gate_name="smoke",
                    status="failed" if smoke_failed else "passed",
                    command=smoke_cmd,
                    summary=smoke_summary,
                )
                if smoke_failed:
                    if _is_infra_error(smoke_summary):
                        infra_failure = True
                    failed_gates.append(
                        {
                            "gate_name": "smoke",
                            "command": smoke_cmd,
                            "summary": smoke_summary,
                        }
                    )

        if infra_failure:
            top = failed_gates[0] if failed_gates else {"summary": "infra failure"}
            return {
                "passed": False,
                "infra_failure": True,
                "error_message": f"INFRA_FAILURE: {top.get('summary', '')[:220]}",
                "failed_gate_names": [gate["gate_name"] for gate in failed_gates],
                "run_contract": None,
                "fix_written_files": [],
            }

        if not failed_gates:
            return {
                "passed": True,
                "infra_failure": False,
                "error_message": "",
                "failed_gate_names": [],
                "run_contract": last_contract,
                "pass_summary": "QUALITY_GATES_PASSED: infra_preflight,run_contract,lint,tests,smoke",
                "fix_written_files": [],
            }

        if attempt < max_attempts:
            try:
                fix_written_files = await _run_quality_fix_pass(
                    project=project,
                    milestone_text=milestone_text,
                    working_dir=working_dir,
                    failing_gates=failed_gates,
                )
            except Exception as exc:
                reason = str(exc).strip() or "quality auto-fix pass failed"
                if reason.startswith("FALLBACK_UNAVAILABLE:"):
                    failed_names = [gate["gate_name"] for gate in failed_gates]
                    return {
                        "passed": False,
                        "infra_failure": False,
                        "error_message": reason,
                        "failed_gate_names": failed_names,
                        "run_contract": last_contract,
                        "fix_written_files": [],
                    }
                logger.warning(
                    "Quality auto-fix pass failed for task %s attempt %s: %s",
                    task_id,
                    attempt,
                    exc,
                )
            else:
                if fix_written_files:
                    logger.info(
                        "Quality auto-fix pass wrote %d file(s) for task %s",
                        len(fix_written_files),
                        task_id,
                    )
            continue

        failed_names = [gate["gate_name"] for gate in failed_gates]
        short = ",".join(failed_names[:3])
        return {
            "passed": False,
            "infra_failure": False,
            "error_message": f"GATES_FAILED: {short}",
            "failed_gate_names": failed_names,
            "run_contract": last_contract,
            "fix_written_files": [],
        }

    return {
        "passed": False,
        "infra_failure": False,
        "error_message": "GATES_FAILED: unknown",
        "failed_gate_names": [],
        "run_contract": None,
        "fix_written_files": [],
    }


# â”€â”€ Entry: Start Coding â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def start_coding_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped ðŸš€ Start Coding â€” ask GitHub/folder setup preference."""
    await update.callback_query.answer()

    project_id = context.user_data.get(_PROJECT_ID_KEY)
    if not project_id:
        await update.callback_query.message.reply_text(
            "No active project found. Start a project first.",
            reply_markup=main_menu(),
        )
        return

    context.user_data[_CODING_PID_KEY] = project_id

    await update.callback_query.message.reply_text(
        "Should I set up a GitHub repo and project folder on your laptop?",
        reply_markup=coding_github_setup(),
    )


async def _start_coding_loop(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    message,
    user_id: int,
    chat_id: int,
    project: dict,
    do_github: bool,
) -> bool:
    """Start a coding session task, guarding against duplicate active loops."""
    loop_key = _ACTIVE_LOOP_KEY.format(uid=user_id)
    existing = context.bot_data.get(loop_key)
    if existing and not existing.done():
        await message.reply_text("A coding session is already running for you!")
        return False

    context.bot_data.pop(f"run_project_{user_id}", None)
    context.bot_data.pop(_run_files_key(user_id, project["id"]), None)
    context.bot_data.pop(_run_contract_key(user_id, project["id"]), None)
    slug = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"

    await message.reply_text(
        "Starting coding sessionâ€¦\n"
        f"ðŸ“ Project folder: <code>{working_dir}</code>\n\n"
        "I'll send you each milestone for approval before executing. "
        "Use /status anytime to check progress.",
        parse_mode="HTML",
    )

    task = asyncio.create_task(
        _coding_loop(context.application, chat_id, user_id, project, do_github)
    )
    context.bot_data[loop_key] = task
    return True


async def coding_github_choice_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User chose GitHub setup option â€” spin up the background coding loop."""
    await update.callback_query.answer()

    cb_data    = update.callback_query.data or ""
    project_id = context.user_data.pop(_CODING_PID_KEY, None)
    user_id    = update.effective_user.id
    chat_id    = update.effective_chat.id

    if not project_id:
        await update.callback_query.message.reply_text("Session expired â€” start over.")
        return

    db = context.bot_data.get(KEY_DB)
    project = await get_project(db, project_id)
    if not project:
        await update.callback_query.message.reply_text("Project not found in database.")
        return

    do_github = (cb_data == CB_CODING_GITHUB_YES)

    context.bot_data[_GITHUB_PREF_KEY.format(uid=user_id, pid=project_id)] = do_github

    await _start_coding_loop(
        context=context,
        message=update.callback_query.message,
        user_id=user_id,
        chat_id=chat_id,
        project=project,
        do_github=do_github,
    )


# â”€â”€ Milestone approval callbacks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def retry_coding_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Retry coding for a saved project, reusing the previous GitHub preference when known."""
    await update.callback_query.answer()

    cb_data = update.callback_query.data or ""
    project_id = cb_data.removeprefix(CB_CODING_RETRY_PREFIX).strip()
    if not project_id:
        await update.callback_query.message.reply_text(
            "Invalid retry request.",
            reply_markup=main_menu(),
        )
        return

    db = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user
    user = await ensure_user(
        db,
        telegram_user_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name or "",
    )

    project = await get_project(db, project_id)
    if not project or int(project.get("user_id", -1)) != int(user["id"]):
        await update.callback_query.message.reply_text(
            "This retry link is invalid or you no longer have access to this project.",
            reply_markup=main_menu(),
        )
        return

    context.user_data[_PROJECT_ID_KEY] = project_id
    pref_key = _GITHUB_PREF_KEY.format(uid=tg_user.id, pid=project_id)
    remembered_pref = context.bot_data.get(pref_key)
    if isinstance(remembered_pref, bool):
        mode_label = "GitHub repo + folder setup" if remembered_pref else "folder-only setup"
        await update.callback_query.message.reply_text(
            f"Retrying with your previous preference: {mode_label}."
        )
        await _start_coding_loop(
            context=context,
            message=update.callback_query.message,
            user_id=tg_user.id,
            chat_id=update.effective_chat.id,
            project=project,
            do_github=remembered_pref,
        )
        return

    context.user_data[_CODING_PID_KEY] = project_id
    await update.callback_query.message.reply_text(
        "Should I set up a GitHub repo and project folder on your laptop?",
        reply_markup=coding_github_setup(),
    )

async def approve_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped âœ… Run It â€” signal the coding loop to proceed."""
    await update.callback_query.answer("Runningâ€¦")
    user_id  = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "approve"
        event.set()
    else:
        await update.callback_query.message.reply_text(
            "No active milestone waiting for approval."
        )


async def skip_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped â­ Skip â€” signal the coding loop to skip this milestone."""
    await update.callback_query.answer("Skippingâ€¦")
    user_id   = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "skip"
        event.set()


async def stop_milestone_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped ðŸ›‘ Stop Session â€” signal the coding loop to abort."""
    await update.callback_query.answer("Stoppingâ€¦")
    user_id   = update.effective_user.id
    event_key = _MS_EVENT_KEY.format(uid=user_id)
    event: asyncio.Event | None = context.bot_data.get(event_key)
    if event:
        context.bot_data[_MS_DECISION_KEY.format(uid=user_id)] = "stop"
        event.set()
    else:
        await update.callback_query.message.reply_text(
            "No active coding session to stop."
        )


# â”€â”€ Dashboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def dashboard_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/status â€” show the latest project's task progress."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    db      = context.bot_data.get(KEY_DB)
    tg_user = update.effective_user

    user = await ensure_user(
        db,
        telegram_user_id=tg_user.id,
        username=tg_user.username    or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name   or "",
    )
    projects = await list_projects(db, user_id=user["id"])
    if not projects:
        await update.message.reply_text(
            "No projects yet. Tap ðŸš€ Start a Project to begin.",
            reply_markup=main_menu(),
        )
        return

    project = projects[0]  # most recent
    tasks   = await list_tasks(db, project_id=project["id"])

    STATUS_EMOJI = {
        "pending": "â³",
        "running": "âš™ï¸",
        "done":    "âœ…",
        "failed":  "âŒ",
    }

    if tasks:
        task_lines = "\n".join(
            f"{STATUS_EMOJI.get(t['status'], 'â“')} {t['title']}"
            for t in tasks
        )
    else:
        task_lines = "No tasks yet â€” coding hasn't started."

    loop_key   = _ACTIVE_LOOP_KEY.format(uid=tg_user.id)
    is_running = (
        loop_key in context.bot_data
        and context.bot_data[loop_key]
        and not context.bot_data[loop_key].done()
    )
    status_note = " | ðŸ”„ Coding in progress" if is_running else ""

    slug        = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"

    text = (
        f"<b>ðŸ“Š {project['name']}</b> â€” {project['project_type']}\n"
        f"ðŸ“ <code>{working_dir}</code>\n"
        f"Status: {project['status']}{status_note}\n\n"
        f"{task_lines}"
    )

    # Show Run Project button if coding is done and a project_id is stored.
    run_pid = context.bot_data.get(f"run_project_{tg_user.id}")
    show_run_cta = False
    if run_pid and not is_running:
        run_project_row = await get_project(db, run_pid)
        if run_project_row:
            if _is_strict_project(run_project_row):
                show_run_cta = _has_cached_run_contract(
                    bot_data=context.bot_data,
                    user_id=tg_user.id,
                    project_id=run_pid,
                )
            else:
                show_run_cta = True

    if show_run_cta:
        keyboard = run_project()
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("ðŸ  Main Menu", callback_data="nav:main_menu")],
        ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# â”€â”€ Background coding loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _coding_loop(
    app,
    chat_id: int,
    user_id: int,
    project: dict,
    do_github: bool,
) -> None:
    """
    Background task: orchestrate milestone-by-milestone project execution.

    1. (Optional) Set up GitHub repo + project folder on CLAW worker.
    2. Extract milestones from the stored plan via LLM.
    3. For each milestone:
       a. Send to user with âœ… Run It / â­ Skip buttons.
       b. Wait up to 1 h for user decision.
       c. If approved: dispatch run_coding_agent to CLAW worker.
       d. Notify user of result.
    4. Send completion message.
    """
    db     = app.bot_data.get(KEY_DB)
    router = app.bot_data.get(KEY_ROUTER)
    slug   = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"
    strict_mode = _is_strict_project(project)
    run_files_cache_key = _run_files_key(user_id, project["id"])
    run_contract_cache_key = _run_contract_key(user_id, project["id"])
    last_valid_run_contract: dict[str, Any] | None = None

    try:
        # â”€â”€ Always create the project folder on the worker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if is_worker_available():
            try:
                await send_action(
                    "create_directory",
                    {"directory": working_dir},
                    confirmed=True,
                )
            except Exception:
                pass  # Directory may already exist â€” not fatal.
        else:
            await app.bot.send_message(
                chat_id, "âš ï¸ Worker not connected â€” cannot create project folder."
            )
            return

        # â”€â”€ Optional GitHub setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        preflight_ok, preflight_error = await _preflight_coding_environment(
            project=project,
        )
        if not preflight_ok:
            await app.bot.send_message(
                chat_id,
                (
                    "\u26A0\uFE0F Coding preflight failed.\n"
                    f"<code>{html_mod.escape(preflight_error[:320])}</code>\n\n"
                    "Fix worker setup and tap Retry Coding."
                ),
                parse_mode="HTML",
                reply_markup=retry_coding(project["id"]),
            )
            return

        if do_github:
            await app.bot.send_message(chat_id, "ðŸ”§ Setting up GitHub repo and project folderâ€¦")
            try:
                # Step 1: git init.
                init_result = await send_action(
                    "git_init",
                    {"working_dir": working_dir},
                    confirmed=True,
                )
                if init_result.get("status") == "error":
                    raise RuntimeError(init_result.get("error", "git init failed"))
                _init_inner = init_result.get("result", {})
                if _init_inner.get("returncode", 0) != 0:
                    raise RuntimeError(_init_inner.get("stderr") or _init_inner.get("stdout") or "git init failed")

                # Step 2: write a README so the initial push has a commit.
                readme_content = (
                    f"# {project['name']}\n\n"
                    f"{project.get('description') or project.get('project_type', '')}\n\n"
                    "_Created by SKYNET_\n"
                )
                readme_path = f"{working_dir}/README.md"
                fw_result = await send_action(
                    "file_write",
                    {"file": readme_path, "content": readme_content},  # key is "file" not "path"
                    confirmed=True,
                )
                _fw_inner = fw_result.get("result", fw_result)
                if _fw_inner.get("returncode", 0) != 0:
                    raise RuntimeError(
                        _fw_inner.get("stderr") or _fw_inner.get("stdout") or "file_write failed"
                    )

                # Step 3: stage + commit README.
                await send_action("git_add_all", {"working_dir": working_dir}, confirmed=True)
                commit_result = await send_action(
                    "git_commit",
                    {"working_dir": working_dir, "message": "Initial commit"},
                    confirmed=True,
                )
                _commit_inner = commit_result.get("result", {})
                if _commit_inner.get("returncode", 0) != 0:
                    raise RuntimeError(
                        _commit_inner.get("stderr") or _commit_inner.get("stdout") or "git commit failed"
                    )

                # Step 4: create GitHub repo and push the initial commit.
                gh_result = await send_action(
                    "gh_create_repo",
                    {
                        "working_dir": working_dir,
                        "repo_name":   slug,
                        "description": f"Created by SKYNET â€” {project['project_type']}",
                        "private":     True,
                    },
                    timeout=120,
                    confirmed=True,
                )
                if gh_result.get("status") == "error":
                    raise RuntimeError(gh_result.get("error", "Unknown error"))
                _gh_inner = gh_result.get("result", {})
                if _gh_inner.get("returncode", 0) != 0:
                    raise RuntimeError(_gh_inner.get("stderr") or _gh_inner.get("stdout") or "gh_create_repo failed")
                await app.bot.send_message(chat_id, "âœ… GitHub repo created and pushed.")
            except Exception as exc:
                await app.bot.send_message(
                    chat_id, f"âš ï¸ GitHub setup failed: {exc}\nContinuing anywayâ€¦"
                )

        # â”€â”€ Extract milestones from plan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        await app.bot.send_message(chat_id, "ðŸ“‹ Breaking the plan into milestonesâ€¦")
        milestones = await _extract_milestones(router, project)
        total = len(milestones)

        if not milestones:
            await app.bot.send_message(
                chat_id,
                "Could not extract milestones from the plan. "
                "Please refine your plan and try again.",
                reply_markup=main_menu(),
            )
            return

        await app.bot.send_message(
            chat_id, f"Found <b>{total} milestone(s)</b>. Let's go!", parse_mode="HTML"
        )

        successful_milestones = 0
        failed_milestones = 0
        skipped_milestones = 0
        all_written_files: list[str] = []

        # â”€â”€ Milestone loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for i, milestone_text in enumerate(milestones, 1):
            # Register approval event before rendering buttons so fast taps are not lost.
            event = asyncio.Event()
            event_key    = _MS_EVENT_KEY.format(uid=user_id)
            decision_key = _MS_DECISION_KEY.format(uid=user_id)
            app.bot_data[event_key] = event
            app.bot_data.pop(decision_key, None)

            # Show milestone to user.
            await app.bot.send_message(
                chat_id,
                f"<b>Milestone {i}/{total}</b>\n\n{milestone_text}",
                parse_mode="HTML",
                reply_markup=milestone_review(),
            )

            # Wait for user decision (up to 1 hour).

            try:
                await asyncio.wait_for(event.wait(), timeout=3600)
            except asyncio.TimeoutError:
                await app.bot.send_message(
                    chat_id, f"â° Milestone {i} timed out â€” skipping."
                )
                app.bot_data.pop(event_key, None)
                skipped_milestones += 1
                continue

            app.bot_data.pop(event_key, None)
            decision = app.bot_data.pop(decision_key, "skip")

            if decision == "stop":
                await app.bot.send_message(
                    chat_id,
                    f"ðŸ›‘ Session stopped at milestone {i}/{total}.\n"
                    "Use /status to review completed milestones.",
                )
                return

            if decision == "skip":
                await app.bot.send_message(chat_id, f"â­ Milestone {i} skipped.")
                skipped_milestones += 1
                continue

            # Create DB task record.
            short_title = milestone_text[:80].split("\n")[0]
            task_rec = await create_task(
                db,
                project_id=project["id"],
                title=f"Milestone {i}: {short_title}",
                description=milestone_text,
            )
            await update_task_status(db, task_rec["id"], status="running")
            await app.bot.send_message(chat_id, f"âš™ï¸ Executing milestone {i}â€¦")

            # Dispatch to CLAW worker.
            if not is_worker_available():
                await app.bot.send_message(
                    chat_id, "âš ï¸ Worker disconnected â€” cannot execute. Skipping."
                )
                await update_task_status(
                    db, task_rec["id"],
                    status="failed", error_message="Agent not connected",
                )
                failed_milestones += 1
                continue

            prompt = (
                f"Project: {project['name']} ({project['project_type']})\n"
                f"Working directory: {working_dir}\n\n"
                f"Task:\n{milestone_text}\n\n"
                "Implement this task completely. Write all necessary files, "
                "then run tests if applicable."
            )

            # Feed previously written code so the model builds incrementally.
            if all_written_files:
                existing_code = ""
                for fname in all_written_files:
                    if not fname.endswith((".py", ".js", ".ts", ".html", ".css")):
                        continue
                    try:
                        read_result = await send_action(
                            "file_read",
                            {"file": f"{working_dir}/{fname}"},
                            timeout=10,
                            confirmed=True,
                        )
                        inner_read = read_result.get("result", read_result)
                        content = (inner_read.get("stdout") or "").strip()
                        if content and len(content) < 3000:
                            existing_code += f"\n\n```{fname}\n{content}\n```"
                    except Exception:
                        pass
                if existing_code:
                    prompt += (
                        "\n\nExisting files (build on these, do NOT rewrite unchanged code):"
                        + existing_code
                    )

            try:
                # â”€â”€ Try router-based coding first (Gemini/Groq/Claude) â”€â”€â”€â”€
                claude_ollama_mode = _uses_claude_ollama(project)

                if claude_ollama_mode:
                    # New profile: always use Claude CLI against Ollama in attempt 1.
                    max_attempts = 3
                    for attempt in range(1, max_attempts + 1):
                        result = await send_action(
                            "run_coding_agent",
                            {
                                "agent": "claude",
                                "backend": "ollama",
                                "model": cfg.CLAUDE_OLLAMA_DEFAULT_MODEL,
                                "prompt": prompt,
                                "working_dir": working_dir,
                                "timeout_seconds": 1800,
                                "auto_pull_model": cfg.CLAUDE_OLLAMA_AUTO_PULL,
                            },
                            timeout=1800,
                            confirmed=True,
                        )
                        if result.get("status") == "error":
                            raise RuntimeError(result.get("error", "run_coding_agent failed"))

                        inner = result.get("result", result)
                        return_code = inner.get("returncode", inner.get("exit_code", 0))
                        written = inner.get("files_written") or []

                        if return_code == 0 and written:
                            break

                        if attempt < max_attempts:
                            reason = "no files generated" if not written else f"exit code {return_code}"
                            await app.bot.send_message(
                                chat_id,
                                f"âš ï¸ Attempt {attempt}/{max_attempts} â€” {reason}. Retryingâ€¦"
                            )
                            continue

                        if return_code != 0:
                            detail = (
                                inner.get("stderr")
                                or inner.get("stdout")
                                or f"Failed after {max_attempts} attempts (exit {return_code})"
                            )
                            raise RuntimeError(str(detail))
                else:
                    # Legacy profile keeps router-first behavior.
                    router_written: list[str] = []
                    try:
                        coding_resp = await router.chat(
                            messages=[{"role": "user", "content": prompt}],
                            system=_CODING_SYSTEM_PROMPT,
                            max_tokens=4096,
                            task_type="coding",
                        )
                        if coding_resp.text:
                            blocks = _parse_code_blocks(coding_resp.text)
                            if blocks:
                                for fname, file_content in blocks:
                                    await send_action(
                                        "file_write",
                                        {"file": f"{working_dir}/{fname}", "content": file_content},
                                        timeout=15,
                                        confirmed=True,
                                    )
                                    router_written.append(fname)
                                logger.info(
                                    "Router coding wrote %d file(s) via %s: %s",
                                    len(router_written),
                                    coding_resp.provider_name,
                                    ", ".join(router_written),
                                )
                    except Exception as exc:
                        logger.info("Router coding unavailable, using coding CLI fallback: %s", exc)

                    if router_written:
                        inner = {
                            "returncode": 0,
                            "stdout": f"Wrote {len(router_written)} file(s): {', '.join(router_written)}",
                            "files_written": router_written,
                        }

                    if not router_written:
                        max_attempts = 3
                        for attempt in range(1, max_attempts + 1):
                            result = await send_action(
                                "run_coding_agent",
                                {
                                    "agent": "claude",
                                    "backend": "auto",
                                    "prompt": prompt,
                                    "working_dir": working_dir,
                                    "timeout_seconds": 1800,
                                },
                                timeout=1800,
                                confirmed=True,
                            )
                            if result.get("status") == "error":
                                raise RuntimeError(result.get("error", "run_coding_agent failed"))

                            inner = result.get("result", result)
                            return_code = inner.get("returncode", inner.get("exit_code", 0))
                            written = inner.get("files_written") or []

                            if return_code == 0 and written:
                                break

                            if attempt < max_attempts:
                                reason = "no files generated" if not written else f"exit code {return_code}"
                                await app.bot.send_message(
                                    chat_id,
                                    f"âš ï¸ Attempt {attempt}/{max_attempts} â€” {reason}. Retryingâ€¦"
                                )
                                continue

                            if return_code != 0:
                                detail = (
                                    inner.get("stderr")
                                    or inner.get("stdout")
                                    or f"Failed after {max_attempts} attempts (exit {return_code})"
                                )
                                raise RuntimeError(str(detail))

                summary = (inner.get("stdout") or inner.get("stderr") or "")[:500].strip()

                # Track written files for the run handler.
                written = inner.get("files_written") or []
                if written:
                    all_written_files.extend(written)
                else:
                    # Fallback: parse "Wrote N file(s): a.py, b.py" from stdout
                    m = re.search(r"Wrote \d+ file\(s\): (.+)", summary)
                    if m:
                        all_written_files.extend(
                            f.strip() for f in m.group(1).split(",") if f.strip()
                        )

                if strict_mode:
                    gate_result = await _run_strict_quality_gates(
                        db=db,
                        task_id=task_rec["id"],
                        project=project,
                        milestone_text=milestone_text,
                        working_dir=working_dir,
                    )
                    if not gate_result.get("passed"):
                        failed_names = gate_result.get("failed_gate_names") or []
                        err = str(gate_result.get("error_message") or "GATES_FAILED")
                        if (
                            not err.startswith("INFRA_FAILURE:")
                            and not err.startswith("FALLBACK_UNAVAILABLE:")
                            and failed_names
                        ):
                            err = f"GATES_FAILED: {','.join(failed_names)}"
                        err = err[:300]
                        await update_task_status(
                            db,
                            task_rec["id"],
                            status="failed",
                            error_message=err,
                        )
                        failed_milestones += 1
                        await app.bot.send_message(
                            chat_id,
                            f"Ã¢ÂÅ’ Milestone {i} failed:\n<code>{html_mod.escape(err)}</code>",
                            parse_mode="HTML",
                        )
                        continue
                    run_contract = gate_result.get("run_contract")
                    if isinstance(run_contract, dict):
                        last_valid_run_contract = run_contract
                    pass_summary = str(gate_result.get("pass_summary") or "").strip()
                    if pass_summary:
                        summary = (summary + "\n" + pass_summary).strip()[:500]

                await update_task_status(
                    db, task_rec["id"], status="done", result_summary=summary
                )
                successful_milestones += 1
                notice = f"âœ… Milestone {i} complete!"
                if summary:
                    notice += f"\n\n{summary}"
                await app.bot.send_message(chat_id, notice)

            except Exception as exc:
                err = str(exc)[:300]
                await update_task_status(
                    db, task_rec["id"], status="failed", error_message=err
                )
                failed_milestones += 1
                await app.bot.send_message(
                    chat_id, f"âŒ Milestone {i} failed:\n<code>{html_mod.escape(str(err))}</code>",
                    parse_mode="HTML",
                )

        # â”€â”€ Done â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        milestone_summary = (
            f"complete={successful_milestones}, "
            f"failed={failed_milestones}, "
            f"skipped={skipped_milestones}"
        )
        if successful_milestones > 0:
            unique_written: list[str] = []
            seen_written: set[str] = set()
            for path in all_written_files:
                clean = str(path).strip()
                if not clean:
                    continue
                key = clean.lower()
                if key in seen_written:
                    continue
                seen_written.add(key)
                unique_written.append(clean)

            app.bot_data[f"run_project_{user_id}"] = project["id"]
            app.bot_data[run_files_cache_key] = unique_written
            if strict_mode and last_valid_run_contract:
                app.bot_data[run_contract_cache_key] = last_valid_run_contract
            elif strict_mode:
                app.bot_data.pop(run_contract_cache_key, None)

            can_run_now = (not strict_mode) or bool(last_valid_run_contract)
            await app.bot.send_message(
                chat_id,
                f"\U0001F389 <b>{project['name']}</b> coding session complete!\n"
                f"\U0001F4C1 <code>{working_dir}</code>\n"
                f"{milestone_summary}\n\n"
                "Use /status to review milestones or run the project now.",
                parse_mode="HTML",
                reply_markup=run_project() if can_run_now else main_menu(),
            )
        else:
            await app.bot.send_message(
                chat_id,
                f"\u26A0\uFE0F <b>{project['name']}</b> session finished with no successful milestones.\n"
                f"\U0001F4C1 <code>{working_dir}</code>\n"
                f"{milestone_summary}\n\n"
                "Tap Retry Coding to run again with your previous GitHub setup mode, "
                "or use /status to inspect failures first.",
                parse_mode="HTML",
                reply_markup=retry_coding(project["id"]),
            )

    except Exception:
        logger.exception("Coding loop crashed for project %s user %s", project["id"], user_id)
        await app.bot.send_message(
            chat_id,
            "An unexpected error occurred in the coding loop. "
            "Use /status to see what was completed.",
            reply_markup=main_menu(),
        )


# â”€â”€ Run Project â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def run_project_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """User tapped Run Project and wants execution on the worker."""
    await update.callback_query.answer()

    user_id = update.effective_user.id
    db = context.bot_data.get(KEY_DB)

    # Prefer the project from the last coding session; fallback to most recent.
    pid_key = f"run_project_{user_id}"
    project_id = context.bot_data.get(pid_key)
    project = None
    if project_id:
        project = await get_project(db, project_id)

    if not project:
        tg_user = update.effective_user
        user = await ensure_user(
            db,
            telegram_user_id=tg_user.id,
            username=tg_user.username or "",
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name or "",
        )
        projects = await list_projects(db, user_id=user["id"])
        project = projects[0] if projects else None

    if not project:
        await update.callback_query.message.reply_text(
            "No project found to run.",
            reply_markup=main_menu(),
        )
        return

    strict_mode = _is_strict_project(project)
    run_files_cache_key = _run_files_key(user_id, project["id"])
    run_contract_cache_key = _run_contract_key(user_id, project["id"])

    if not is_worker_available():
        await update.callback_query.message.reply_text(
            "Worker not connected - cannot run the project right now.",
            reply_markup=run_project() if not strict_mode else main_menu(),
        )
        return

    slug = _slugify(project["name"])
    working_dir = f"{cfg.WORKER_PROJECTS_DIR}/{slug}"
    project_type = str(project.get("project_type", "") or "")

    run_cmd: str | None = None
    run_target: str | None = None

    if strict_mode:
        cached_contract = _validate_cached_run_contract(
            context.bot_data.get(run_contract_cache_key)
        )
        if cached_contract:
            run_cmd = cached_contract["command"]
            run_target = cached_contract["entrypoint"]
        else:
            manifest_contract, manifest_summary, manifest_infra = await _load_and_validate_run_contract(
                working_dir=working_dir,
            )
            if manifest_contract:
                context.bot_data[run_contract_cache_key] = manifest_contract
                run_cmd = manifest_contract["command"]
                run_target = manifest_contract["entrypoint"]
            else:
                detail = html_mod.escape(manifest_summary[:260])
                if manifest_infra:
                    msg = (
                        f"Run failed: infrastructure error validating <code>{_RUN_CONTRACT_FILE}</code>: {detail}"
                    )
                else:
                    msg = (
                        f"Strict run contract is missing or invalid (<code>{_RUN_CONTRACT_FILE}</code>): {detail}"
                    )
                await update.callback_query.message.reply_text(
                    msg,
                    parse_mode="HTML",
                    reply_markup=main_menu(),
                )
                return
    else:
        stored_files = context.bot_data.get(run_files_cache_key) or []
        if stored_files:
            resolved = _select_entrypoint(
                files=stored_files,
                slug=slug,
                project_type=project_type,
            )
        else:
            resolved = None

        if not resolved:
            try:
                list_result = await send_action(
                    "list_directory",
                    {"directory": working_dir, "recursive": True},
                    timeout=20,
                    confirmed=True,
                )
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                await update.callback_query.message.reply_text(
                    f"Run failed: infrastructure error while listing files: <code>{html_mod.escape(detail[:260])}</code>",
                    parse_mode="HTML",
                    reply_markup=run_project(),
                )
                return

            if list_result.get("status") == "error" or _action_exit_code(list_result) != 0:
                detail = _action_error_text(list_result, "list_directory")
                await update.callback_query.message.reply_text(
                    f"Run failed: infrastructure error while listing files: <code>{html_mod.escape(detail[:260])}</code>",
                    parse_mode="HTML",
                    reply_markup=run_project(),
                )
                return

            listing = str(_action_inner_result(list_result).get("stdout") or "")
            discovered_files = _extract_file_paths_from_listing(
                listing,
                working_dir=working_dir,
            )
            resolved = _select_entrypoint(
                files=discovered_files,
                slug=slug,
                project_type=project_type,
            )

        if not resolved:
            await update.callback_query.message.reply_text(
                f"No runnable entry point found in <code>{html_mod.escape(working_dir)}</code>.\n"
                "The coding agent may not have finished writing files. Try running the coding loop again.",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
            return

        run_cmd, run_target = resolved

    if not run_cmd:
        await update.callback_query.message.reply_text(
            "No runnable command is available for this project.",
            reply_markup=main_menu(),
        )
        return

    await update.callback_query.message.reply_text(
        f"Running <code>{html_mod.escape(run_target or '')}</code> on your laptop...",
        parse_mode="HTML",
    )

    run_markup = run_project() if (not strict_mode or _has_cached_run_contract(
        bot_data=context.bot_data,
        user_id=user_id,
        project_id=project["id"],
    )) else main_menu()

    try:
        result = await send_action(
            "exec_command",
            {"command": run_cmd, "working_dir": working_dir},
            timeout=60,
            confirmed=True,
        )
        if result.get("status") == "error":
            detail = _action_error_text(result, "exec_command")
            if _is_infra_error(detail):
                raise RuntimeError(f"Infrastructure error: {detail}")
            raise RuntimeError(detail)

        inner = _action_inner_result(result)
        stdout = (inner.get("stdout") or "").strip()
        stderr = (inner.get("stderr") or "").strip()
        exit_code = inner.get("returncode", inner.get("exit_code", 0))

        output = html_mod.escape((stdout or stderr or "(no output)")[:1000])
        status_line = (
            f"Finished (exit {exit_code})"
            if exit_code == 0
            else f"Exited with code {exit_code}"
        )
        await update.callback_query.message.reply_text(
            f"<pre>{output}</pre>\n\n{status_line}",
            parse_mode="HTML",
            reply_markup=run_markup,
        )
    except Exception as exc:
        await update.callback_query.message.reply_text(
            f"Run failed: {html_mod.escape(str(exc)[:300])}",
            parse_mode="HTML",
            reply_markup=run_markup,
        )

def _project_prefers_node(project_type: str) -> bool:
    lowered = project_type.lower()
    return any(token in lowered for token in ("javascript", "node", "react", "next.js", "js"))


def _normalize_slashes(path: str) -> str:
    return path.replace("\\", "/")


def _to_relative_path(path: str, *, working_dir: str) -> str:
    """
    Best-effort conversion of discovered paths to a path runnable from working_dir.
    """
    norm_path = _normalize_slashes(path).strip()
    norm_working_dir = _normalize_slashes(working_dir).rstrip("/")
    if not norm_path:
        return norm_path
    if norm_working_dir and norm_path.lower().startswith((norm_working_dir + "/").lower()):
        return norm_path[len(norm_working_dir) + 1 :]
    return norm_path


def _extract_file_paths_from_listing(listing: str, *, working_dir: str) -> list[str]:
    """
    Parse list_directory output (including recursive [DIR] format) into file paths.
    """
    files: list[str] = []
    dir_stack: list[str] = []

    for raw_line in listing.splitlines():
        line = raw_line.rstrip()
        if not line or line == "... (truncated)":
            continue

        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        depth = max(0, indent // 2)

        if stripped.startswith("[DIR] "):
            dir_name = stripped[len("[DIR] ") :].strip().rstrip("/\\")
            if not dir_name:
                continue

            # Absolute fallback formats may include a full path in [DIR] lines.
            if re.match(r"^[A-Za-z]:[\\/]", dir_name) or dir_name.startswith("/"):
                rel_dir = _to_relative_path(dir_name, working_dir=working_dir).strip("/")
                dir_stack = [p for p in _normalize_slashes(rel_dir).split("/") if p]
                continue

            if depth <= len(dir_stack):
                dir_stack = dir_stack[:depth]
            dir_stack.append(dir_name)
            continue

        file_name = re.sub(r"\s+\(\d+\s+bytes\)\s*$", "", stripped).strip()
        if not file_name:
            continue

        if re.match(r"^[A-Za-z]:[\\/]", file_name) or file_name.startswith("/"):
            rel_file = _to_relative_path(file_name, working_dir=working_dir)
        else:
            prefix = "/".join(dir_stack[:depth]) if depth <= len(dir_stack) else "/".join(dir_stack)
            rel_file = f"{prefix}/{file_name}" if prefix else file_name
        rel_file = _normalize_slashes(rel_file).lstrip("./")
        if rel_file:
            files.append(rel_file)

    # Preserve order while removing duplicates (case-insensitive for Windows paths).
    unique_files: list[str] = []
    seen: set[str] = set()
    for filepath in files:
        key = filepath.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(filepath)
    return unique_files


def _select_entrypoint(
    *,
    files: list[str],
    slug: str,
    project_type: str,
) -> tuple[str, str] | None:
    """
    Return (command, target_path) for the best .py/.js entrypoint candidate.
    """
    slug_lower = slug.lower()
    type_prefers_node = _project_prefers_node(project_type)
    candidates: list[tuple[int, str, str]] = []  # (score, interpreter, path)

    for path in files:
        norm_path = _normalize_slashes(path).strip()
        lower = norm_path.lower()
        if lower.endswith(".py"):
            interpreter = "python"
        elif lower.endswith(".js"):
            interpreter = "node"
        else:
            continue

        basename = lower.rsplit("/", 1)[-1]
        depth = lower.count("/")
        score = 0

        if basename in (f"{slug_lower}.py", f"{slug_lower}.js"):
            score += 120
        if basename in ("main.py", "app.py", "index.py", "main.js", "app.js", "index.js", "server.js"):
            score += 90

        if depth == 0:
            score += 20
        score -= depth * 2

        if " " in norm_path:
            score -= 10

        if type_prefers_node and interpreter == "node":
            score += 40
        if not type_prefers_node and interpreter == "python":
            score += 15

        candidates.append((score, interpreter, norm_path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[2].lower()))
    _, interpreter, target = candidates[0]
    return f"{interpreter} {target}", target


async def _extract_milestones(router, project: dict) -> list[str]:
    """
    Ask the LLM to extract an ordered list of coding milestones from the plan.
    Returns a list of milestone description strings.
    """
    plan = project.get("description", "")
    if not plan:
        return []

    system = (
        "You are a project planner. Extract the coding milestones from the project plan "
        "as a JSON array of strings. Each element is ONE self-contained coding task "
        "(e.g. 'Set up project structure', 'Implement login endpoint'). "
        "Output ONLY a valid JSON array, no extra text."
    )
    messages = [
        {
            "role": "user",
            "content": f"Project: {project['name']}\n\nPlan:\n{plan}\n\n"
                       "Return the milestones as a JSON array of strings.",
        }
    ]

    try:
        response = await router.chat(
            messages=messages,
            system=system,
            max_tokens=1024,
            task_type="planning",
        )
        raw = (response.text or "").strip()
        # Strip markdown code fences if present.
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        milestones = json.loads(raw)
        if isinstance(milestones, list) and all(isinstance(m, str) for m in milestones):
            return [m.strip() for m in milestones if m.strip()]
    except Exception:
        logger.warning("JSON milestone extraction failed â€” falling back to line parsing")

    # Fallback: split on numbered list items (1. ... 2. ...)
    fallback = _parse_milestones_fallback(plan)
    if fallback:
        return fallback

    # Last resort: ask the LLM to generate milestones from the project name and type
    # (handles cases where the plan text is garbage or a meta-response).
    logger.warning("No milestones found in plan text â€” generating from project info")
    try:
        gen_system = (
            "You are a project planner. Generate 2-4 coding milestones for the given project. "
            "Each milestone is ONE self-contained coding task. "
            "Output ONLY a valid JSON array of strings, no extra text."
        )
        gen_messages = [{
            "role": "user",
            "content": (
                f"Project name: {project['name']}\n"
                f"Type: {project.get('project_type', 'Other')}\n"
                f"Description: {plan[:500]}\n\n"
                "Generate milestones as a JSON array of strings."
            ),
        }]
        response = await router.chat(
            messages=gen_messages, system=gen_system,
            max_tokens=512, task_type="planning",
        )
        raw = (response.text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        milestones = json.loads(raw)
        if isinstance(milestones, list) and all(isinstance(m, str) for m in milestones):
            return [m.strip() for m in milestones if m.strip()]
    except Exception:
        logger.warning("Last-resort milestone generation also failed")

    return []


def _parse_milestones_fallback(plan: str) -> list[str]:
    """Extract numbered list items from free-form plan text."""
    pattern = re.compile(r"^\s*\d+\.\s+(.+)", re.MULTILINE)
    matches = pattern.findall(plan)
    return [m.strip() for m in matches if m.strip()]


def _slugify(name: str) -> str:
    """Convert a project name to a safe directory/repo slug."""
    slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "project"
