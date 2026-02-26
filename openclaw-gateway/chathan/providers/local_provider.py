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
        - `allowed_paths`: input used by this function to compute or route work.
        
        Returns:
        - Function-specific value or side effects consumed by upstream callers.
        """

        self.allowed_paths = allowed_paths or [os.getcwd()]
        self._running: dict[str, asyncio.subprocess.Process] = {}

    async def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        """
        Execute.
        
        Purpose:
        - Implement `execute` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `spec`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `ExecutionResult` when available; otherwise side effects only.
        """

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
        - Return value typed as `bool` when available; otherwise side effects only.
        """

        return True

    async def cancel(self, job_id: str) -> bool:
        """
        Cancel.
        
        Purpose:
        - Implement `cancel` within this module's workflow.
        - Keep behavior localized so callers have one stable entrypoint.
        
        How it works:
        - Consumes declared inputs, performs local validation/transforms, and applies the function logic.
        - Produces deterministic return data or side effects expected by calling code.
        
        Why this exists:
        - Prevents duplicated logic in upstream orchestration paths.
        - Improves debuggability by centralizing this behavior in one named function.
        
        Parameters:
        - `job_id`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

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
        - `job_id`: input used by this function to compute or route work.
        - `command`: input used by this function to compute or route work.
        - `cwd`: input used by this function to compute or route work.
        - `timeout`: input used by this function to compute or route work.
        - `env`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `tuple[int, str, str]` when available; otherwise side effects only.
        """

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
        - `candidate`: input used by this function to compute or route work.
        
        Returns:
        - Return value typed as `bool` when available; otherwise side effects only.
        """

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
        """
        Action to command.
        
        Purpose:
        - Implement `_action_to_command` within this module's workflow.
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
        - Return value typed as `list[str] | None` when available; otherwise side effects only.
        """

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
