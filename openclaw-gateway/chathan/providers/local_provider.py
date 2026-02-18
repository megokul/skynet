"""
CHATHAN Providers - Local Provider

Executes simple action-mapped commands directly on the gateway host.
Useful fallback when the remote agent is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from pathlib import Path
from typing import Any

from chathan.protocol.execution_spec import ExecutionSpec
from .base_provider import BaseExecutionProvider, ExecutionResult

logger = logging.getLogger("skynet.provider.local")


class LocalProvider(BaseExecutionProvider):
    """Execute directly on the gateway host."""

    name = "local"

    def __init__(self, allowed_paths: list[str] | None = None):
        self.allowed_paths = allowed_paths or [os.getcwd()]
        self._running: dict[str, asyncio.subprocess.Process] = {}

    async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        result = ExecutionResult(job_id=spec.job_id, status="running")
        logs: list[str] = []
        step_results: list[dict[str, Any]] = []

        for idx, step in enumerate(spec.steps, start=1):
            command = self._action_to_command(step.action, step.params)
            if not command:
                result.status = "failed"
                result.error = f"Unsupported action: {step.action}"
                result.exit_code = 2
                break

            working_dir = step.params.get("working_dir") or spec.sandbox_root or os.getcwd()
            if not self._is_allowed_path(working_dir):
                result.status = "failed"
                result.error = f"Working dir not allowed: {working_dir}"
                result.exit_code = 2
                break

            rc, out, err = await self._run_command(
                spec.job_id,
                command,
                cwd=working_dir,
                timeout=step.timeout_sec or 120,
                env=spec.env,
            )

            step_results.append(
                {
                    "step_id": step.id,
                    "action": step.action,
                    "exit_code": rc,
                    "stdout": out,
                    "stderr": err,
                }
            )

            if out:
                logs.append(out)
            if err:
                logs.append(f"STDERR: {err}")

            if rc != 0:
                result.status = "failed"
                result.error = f"Step {idx} ({step.action}) failed with exit code {rc}"
                result.exit_code = rc
                break
        else:
            result.status = "succeeded"
            result.exit_code = 0

        result.logs = "\n".join(logs)
        result.step_results = step_results
        return result

    async def health_check(self) -> bool:
        return True

    async def cancel(self, job_id: str) -> bool:
        proc = self._running.get(job_id)
        if not proc:
            return False
        proc.terminate()
        return True

    async def _run_command(
        self,
        job_id: str,
        command: list[str],
        cwd: str,
        timeout: int,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running[job_id] = process
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return 124, "", f"Command timed out after {timeout}s"
        finally:
            self._running.pop(job_id, None)

    def _is_allowed_path(self, candidate: str) -> bool:
        try:
            candidate_path = Path(candidate).resolve()
            for allowed in self.allowed_paths:
                allowed_path = Path(allowed).resolve()
                try:
                    candidate_path.relative_to(allowed_path)
                    return True
                except ValueError:
                    continue
            return False
        except Exception:
            return False

    def _action_to_command(self, action: str, params: dict[str, Any]) -> list[str] | None:
        is_windows = os.name == "nt"
        action_map = {
            "git_status": ["git", "status"],
            "git_diff": ["git", "diff"],
            "run_tests": shlex.split(params.get("command", "pytest -q")),
            "list_directory": ["cmd", "/c", "dir"] if is_windows else ["ls", "-la"],
            "docker_compose_up": ["docker-compose", "up", "-d"],
            "docker_build": ["docker", "build", "-t", params.get("tag", "app"), "."],
        }

        if action == "execute_command":
            command = params.get("command")
            if isinstance(command, str):
                return shlex.split(command)
            if isinstance(command, list):
                return [str(part) for part in command]
            return None

        return action_map.get(action)