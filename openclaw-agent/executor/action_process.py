from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any

from agent_config import CLOSEABLE_APPS
from executor.action_support import python_module_missing, require_param, run_subprocess


async def git_status(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    return await run_subprocess(["git", "status", "--porcelain"], cwd=cwd)


async def run_tests(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    runner = params.get("runner", "pytest")

    if runner == "pytest":
        result = await run_subprocess(["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
        if result["returncode"] != 0 and python_module_missing(result, "pytest"):
            install = await run_subprocess(["python", "-m", "pip", "install", "pytest"], cwd=cwd, timeout=300)
            if install["returncode"] != 0:
                detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                base_err = str(result.get("stderr") or "").strip()
                result["stderr"] = f"{base_err}\nPYTEST_SETUP_ERROR: {detail}".strip()
                return result
            retry = await run_subprocess(["python", "-m", "pytest", "--tb=short", "-q"], cwd=cwd)
            retry["stdout"] = f"Auto-installed pytest.\n{retry.get('stdout', '')}".strip()
            return retry
        return result
    if runner == "npm":
        return await run_subprocess(["npm", "test"], cwd=cwd)
    return {"returncode": 1, "stdout": "", "stderr": f"Unknown runner: {runner}"}


async def lint_project(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    linter = params.get("linter", "ruff")

    if linter == "ruff":
        result = await run_subprocess(["python", "-m", "ruff", "check", "."], cwd=cwd)
        if result["returncode"] != 0 and python_module_missing(result, "ruff"):
            install = await run_subprocess(["python", "-m", "pip", "install", "ruff"], cwd=cwd, timeout=300)
            if install["returncode"] != 0:
                detail = str(install.get("stderr") or install.get("stdout") or "").strip()
                base_err = str(result.get("stderr") or "").strip()
                result["stderr"] = f"{base_err}\nRUFF_SETUP_ERROR: {detail}".strip()
                return result
            retry = await run_subprocess(["python", "-m", "ruff", "check", "."], cwd=cwd)
            retry["stdout"] = f"Auto-installed ruff.\n{retry.get('stdout', '')}".strip()
            return retry
        return result
    if linter == "eslint":
        return await run_subprocess(["npx", "eslint", "."], cwd=cwd)
    return {"returncode": 1, "stdout": "", "stderr": f"Unknown linter: {linter}"}


async def start_dev_server(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    framework = params.get("framework", "npm")

    if framework == "npm":
        proc = await asyncio.create_subprocess_exec(
            "npm",
            "run",
            "dev",
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return {"returncode": 0, "stdout": f"Dev server started (pid={proc.pid}).", "stderr": ""}
    if framework == "uvicorn":
        app_module = params.get("app_module", "main:app")
        proc = await asyncio.create_subprocess_exec(
            "python",
            "-m",
            "uvicorn",
            app_module,
            "--reload",
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return {"returncode": 0, "stdout": f"Uvicorn started (pid={proc.pid}).", "stderr": ""}
    return {"returncode": 1, "stdout": "", "stderr": f"Unknown framework: {framework}"}


async def build_project(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    tool = params.get("build_tool", "npm")
    if tool == "npm":
        return await run_subprocess(["npm", "run", "build"], cwd=cwd)
    if tool == "python":
        return await run_subprocess(["python", "-m", "build"], cwd=cwd)
    return {"returncode": 1, "stdout": "", "stderr": f"Unknown build tool: {tool}"}


async def exec_command(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    argv = params.get("argv")
    if isinstance(argv, list):
        parts = [str(item) for item in argv if str(item)]
        if not parts:
            return {"returncode": 1, "stdout": "", "stderr": "Empty argv."}
    else:
        command = require_param(params, "command")
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
    actual = sys.executable if interpreter in ("python", "python3") else "node"
    return await run_subprocess([actual] + parts[1:], cwd=cwd)


async def git_commit(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    message = require_param(params, "message")
    stage_result = await run_subprocess(["git", "add", "-u"], cwd=cwd)
    if stage_result["returncode"] != 0:
        return stage_result
    return await run_subprocess(["git", "commit", "-m", message], cwd=cwd)


async def install_dependencies(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    manager = params.get("manager", "pip")
    if manager == "pip":
        req_file = os.path.join(cwd, "requirements.txt")
        return await run_subprocess(["python", "-m", "pip", "install", "-r", req_file], cwd=cwd, timeout=300)
    if manager == "npm":
        return await run_subprocess(["npm", "install"], cwd=cwd, timeout=300)
    return {"returncode": 1, "stdout": "", "stderr": f"Unknown manager: {manager}"}


async def git_init(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    result = await run_subprocess(["git", "init"], cwd=cwd)
    if result["returncode"] == 0:
        await run_subprocess(["git", "checkout", "-b", "main"], cwd=cwd)
    return result


async def git_add_all(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    return await run_subprocess(["git", "add", "-A"], cwd=cwd)


async def git_push(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    remote = params.get("remote", "origin")
    branch = params.get("branch", "main")
    return await run_subprocess(["git", "push", "-u", remote, branch], cwd=cwd)


async def gh_create_repo(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    repo_name = require_param(params, "repo_name")
    description = params.get("description", "")
    private = params.get("private", False) is True
    if not re.match(r"^[a-zA-Z0-9._-]+$", repo_name):
        return {"returncode": 1, "stdout": "", "stderr": "Invalid repo name characters."}
    visibility = "--private" if private else "--public"
    args = ["gh", "repo", "create", repo_name, visibility, "--source=.", "--push"]
    if description:
        args.extend(["--description", description])
    return await run_subprocess(args, cwd=cwd, timeout=60)


async def open_in_vscode(params: dict[str, Any]) -> dict[str, Any]:
    path = require_param(params, "path")
    return await run_subprocess(["code", path])


async def docker_build(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    tag = params.get("tag", "chathan-build:latest")
    if not re.match(r"^[a-zA-Z0-9._/:@-]+$", tag):
        return {"returncode": 1, "stdout": "", "stderr": "Invalid Docker tag characters."}
    return await run_subprocess(["docker", "build", "-t", tag, "."], cwd=cwd, timeout=600)


async def docker_compose_up(params: dict[str, Any]) -> dict[str, Any]:
    cwd = require_param(params, "working_dir")
    return await run_subprocess(["docker", "compose", "up", "-d"], cwd=cwd, timeout=300)


async def close_app(params: dict[str, Any]) -> dict[str, Any]:
    app_name = require_param(params, "app").lower()
    if app_name not in CLOSEABLE_APPS:
        allowed = ", ".join(sorted(CLOSEABLE_APPS.keys()))
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"'{app_name}' is not in the allowed list. Allowed: {allowed}",
        }
    return await run_subprocess(["taskkill", "/F", "/IM", CLOSEABLE_APPS[app_name]])
