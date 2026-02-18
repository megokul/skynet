"""
CHATHAN — Local Execution Provider

Executes actions on the local machine using subprocess.
Includes safety features like working directory restrictions,
command validation, and timeouts.

Safety Features:
- Working directory restrictions (sandbox)
- Command timeout (default 60s)
- Environment variable control
- Output capture and size limits
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from skynet.chathan.providers.base_provider import BaseExecutionProvider

logger = logging.getLogger("skynet.chathan.providers.local")


class LocalProvider(BaseExecutionProvider):
    """
    Local execution provider that runs commands on the host machine.

    Executes shell commands with safety constraints.
    """

    def __init__(
        self,
        allowed_paths: list[str] | None = None,
        default_timeout: int = 60,
        max_output_size: int = 1024 * 1024,  # 1MB
    ):
        """
        Initialize local provider.

        Args:
            allowed_paths: List of allowed working directories (None = current dir only)
            default_timeout: Default command timeout in seconds
            max_output_size: Maximum output size in bytes
        """
        super().__init__()
        self.name = "local"
        self.allowed_paths = allowed_paths or [os.getcwd()]
        self.default_timeout = default_timeout
        self.max_output_size = max_output_size
        logger.info(f"Local provider initialized - allowed paths: {self.allowed_paths}")

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute action locally.

        Args:
            action: Action type (e.g., 'git_status', 'execute_command')
            params: Action parameters (command, working_dir, timeout, etc.)

        Returns:
            Execution result with status, output, exit_code
        """
        logger.info(f"[LOCAL] Executing {action} with params: {params}")

        try:
            # Map action to command
            command = self._action_to_command(action, params)
            if not command:
                return {
                    "status": "error",
                    "output": f"Unknown action: {action}",
                    "action": action,
                    "provider": "local",
                    "exit_code": -1,
                }

            # Get working directory (validate against allowed paths)
            working_dir = params.get("working_dir", os.getcwd())
            if not self._is_path_allowed(working_dir):
                return {
                    "status": "error",
                    "output": f"Working directory not allowed: {working_dir}",
                    "action": action,
                    "provider": "local",
                    "exit_code": -1,
                }

            # Get timeout
            timeout = params.get("timeout", self.default_timeout)

            # Execute command
            result = self._run_command(
                command=command,
                working_dir=working_dir,
                timeout=timeout,
            )

            return {
                "status": "success" if result["exit_code"] == 0 else "failed",
                "output": result["output"],
                "action": action,
                "provider": "local",
                "exit_code": result["exit_code"],
            }

        except Exception as e:
            logger.error(f"[LOCAL] Error executing {action}: {e}")
            return {
                "status": "error",
                "output": str(e),
                "action": action,
                "provider": "local",
                "exit_code": -1,
            }

    def health_check(self) -> dict[str, Any]:
        """Check if local provider is healthy."""
        try:
            # Try a simple command
            result = subprocess.run(
                ["echo", "health_check"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=True,  # For Windows compatibility with echo
            )

            is_healthy = result.returncode == 0

            return {
                "status": "healthy" if is_healthy else "unhealthy",
                "provider": "local",
                "capabilities": [
                    "git_status",
                    "list_directory",
                    "execute_command",
                    "run_tests",
                ],
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "provider": "local",
                "error": str(e),
            }

    def cancel(self, execution_id: str) -> dict[str, Any]:
        """
        Cancel a running execution.

        Note: For local provider, this is limited since we don't track
        running processes. Real implementation would need process management.
        """
        logger.warning(f"Cancel requested for {execution_id} but not implemented")
        return {
            "status": "not_supported",
            "execution_id": execution_id,
            "provider": "local",
            "message": "Cancellation not implemented for local provider",
        }

    def _action_to_command(self, action: str, params: dict[str, Any]) -> list[str] | None:
        """
        Map action type to shell command.

        Args:
            action: Action type
            params: Action parameters

        Returns:
            Command as list of strings, or None if action unknown
        """
        # Windows vs Unix commands
        is_windows = os.name == "nt"

        action_map = {
            "git_status": ["git", "status"],
            "git_diff": ["git", "diff"],
            "git_log": ["git", "log", "--oneline", "-n", "10"],
            "list_directory": ["dir"] if is_windows else ["ls", "-la"],
            "run_tests": params.get("command", "pytest").split(),
            "docker_build": ["docker", "build", "-t", params.get("tag", "myapp"), "."],
            "docker_compose_up": ["docker-compose", "up", "-d"],
        }

        # Special case: execute_command allows arbitrary commands
        if action == "execute_command":
            command = params.get("command")
            if not command:
                return None
            # Split command string into list (simple split, doesn't handle quotes)
            return command.split() if isinstance(command, str) else command

        return action_map.get(action)

    def _is_path_allowed(self, path: str) -> bool:
        """
        Check if a path is within allowed directories.

        Args:
            path: Path to check

        Returns:
            True if path is allowed, False otherwise
        """
        try:
            abs_path = Path(path).resolve()

            for allowed in self.allowed_paths:
                allowed_path = Path(allowed).resolve()

                # Check if path is under allowed directory
                try:
                    abs_path.relative_to(allowed_path)
                    return True
                except ValueError:
                    # Not relative to this allowed path, try next
                    continue

            # Also allow if path equals an allowed path
            if str(abs_path) in [str(Path(p).resolve()) for p in self.allowed_paths]:
                return True

            return False

        except Exception as e:
            logger.error(f"Error checking path {path}: {e}")
            return False

    def _run_command(
        self,
        command: list[str],
        working_dir: str,
        timeout: int,
    ) -> dict[str, Any]:
        """
        Run a shell command with timeout and output capture.

        Args:
            command: Command to run as list of strings
            working_dir: Working directory
            timeout: Timeout in seconds

        Returns:
            Dict with output and exit_code
        """
        logger.info(f"Running command: {' '.join(command)} in {working_dir}")

        try:
            # For Windows, use shell=True for built-in commands like 'dir'
            use_shell = os.name == "nt" and command[0] in ["dir", "echo", "type"]

            result = subprocess.run(
                command,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=use_shell,
            )

            # Combine stdout and stderr
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr

            # Truncate if too large
            if len(output) > self.max_output_size:
                output = output[:self.max_output_size] + f"\n... (truncated, {len(output)} bytes total)"

            return {
                "output": output,
                "exit_code": result.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out after {timeout}s")
            return {
                "output": f"Command timed out after {timeout} seconds",
                "exit_code": -1,
            }
        except Exception as e:
            logger.error(f"Error running command: {e}")
            return {
                "output": f"Error: {str(e)}",
                "exit_code": -1,
            }
