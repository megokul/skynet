"""
CHATHAN — SSH Provider

Executes actions on remote machines via SSH.

Uses standard SSH command for remote execution without additional dependencies.
Supports key-based and password authentication (via ssh command options).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("skynet.chathan.ssh")


class SSHProvider:
    """
    Executes tasks on remote machines via SSH.

    Uses the standard `ssh` command for remote execution.
    Each action executes a command on the remote host.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 22,
        username: str = "ubuntu",
        key_path: str | None = None,
        timeout: int = 120,  # 2 minutes default
        working_dir: str = "/tmp",
    ):
        """
        Initialize SSHProvider.

        Args:
            host: Remote host to connect to
            port: SSH port
            username: SSH username
            key_path: Path to SSH private key (if None, uses default SSH config)
            timeout: Default timeout for command execution (seconds)
            working_dir: Default working directory on remote host
        """
        self.name = "ssh"
        self.host = host
        self.port = port
        self.username = username
        self.key_path = key_path
        self.timeout = timeout
        self.working_dir = working_dir
        logger.info(f"SSH provider initialized - {username}@{host}:{port}")

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute action on remote host via SSH.

        This is a synchronous wrapper that runs the async SSH operations.

        Args:
            action: Action type (e.g., 'git_status', 'execute_command')
            params: Action parameters

        Returns:
            Execution result with status, output, exit_code
        """
        logger.info(f"[SSH] Executing {action} on {self.host} with params: {params}")

        try:
            result = asyncio.run(self._execute_async(action, params))
            return result
        except Exception as e:
            logger.error(f"[SSH] Failed to execute {action}: {e}")
            return {
                "status": "error",
                "output": f"SSH execution error: {e}",
                "action": action,
                "provider": "ssh",
                "exit_code": -1,
            }

    async def _execute_async(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute action asynchronously via SSH.

        Args:
            action: Action type
            params: Action parameters

        Returns:
            Execution result
        """
        # Map action to command
        command = self._action_to_command(action, params)
        if not command:
            return {
                "status": "error",
                "output": f"Unknown action: {action}",
                "action": action,
                "provider": "ssh",
                "exit_code": -1,
            }

        # Get working directory
        working_dir = params.get("working_dir", self.working_dir)

        # Build SSH command
        ssh_cmd = self._build_ssh_command(command, working_dir)

        # Execute via SSH
        try:
            result = await self._run_ssh_command(ssh_cmd)
            return result
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "output": f"Command timed out after {self.timeout}s",
                "action": action,
                "provider": "ssh",
                "exit_code": -1,
            }
        except Exception as e:
            return {
                "status": "error",
                "output": f"SSH execution failed: {e}",
                "action": action,
                "provider": "ssh",
                "exit_code": -1,
            }

    def _action_to_command(self, action: str, params: dict[str, Any]) -> str | None:
        """
        Map action to shell command.

        Args:
            action: Action type
            params: Action parameters

        Returns:
            Shell command string or None if action is unknown
        """
        # Git actions
        if action == "git_status":
            return "git status"
        elif action == "git_diff":
            return "git diff"
        elif action == "git_log":
            limit = params.get("limit", 10)
            return f"git log -n {limit}"
        elif action == "git_pull":
            return "git pull"

        # File operations
        elif action == "list_directory":
            path = params.get("path", ".")
            return f"ls -la {path}"
        elif action == "read_file":
            file_path = params.get("file_path", "")
            if file_path:
                return f"cat {file_path}"

        # Test actions
        elif action == "run_tests":
            test_command = params.get("test_command", "pytest")
            return test_command

        # Build actions
        elif action == "build_project":
            build_command = params.get("build_command", "make")
            return build_command

        # System actions
        elif action == "check_disk_space":
            return "df -h"
        elif action == "check_memory":
            return "free -h"

        # Generic command execution
        elif action == "execute_command":
            return params.get("command", "")

        # Unknown action
        return None

    def _build_ssh_command(self, command: str, working_dir: str) -> list[str]:
        """
        Build SSH command arguments.

        Args:
            command: Command to execute on remote host
            working_dir: Working directory on remote host

        Returns:
            List of command arguments for subprocess
        """
        ssh_cmd = ["ssh"]

        # Add port if not default
        if self.port != 22:
            ssh_cmd.extend(["-p", str(self.port)])

        # Add key path if specified
        if self.key_path:
            ssh_cmd.extend(["-i", self.key_path])

        # Add common options
        ssh_cmd.extend([
            "-o", "StrictHostKeyChecking=no",  # Don't prompt for host key
            "-o", "ConnectTimeout=10",  # Connection timeout
        ])

        # Add host
        ssh_cmd.append(f"{self.username}@{self.host}")

        # Add command (cd to working dir, then execute)
        remote_command = f"cd {working_dir} && {command}"
        ssh_cmd.append(remote_command)

        return ssh_cmd

    async def _run_ssh_command(self, ssh_cmd: list[str]) -> dict[str, Any]:
        """
        Run SSH command and return result.

        Args:
            ssh_cmd: SSH command arguments

        Returns:
            Execution result
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                # Kill process if timeout
                proc.kill()
                await proc.wait()
                raise

            # Decode output
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Build output
            output = stdout_str
            if stderr_str:
                output += f"\n[STDERR]: {stderr_str}"

            # Check return code
            exit_code = proc.returncode or 0
            status = "success" if exit_code == 0 else "error"

            return {
                "status": status,
                "output": output.strip() if output else "",
                "action": "ssh_exec",
                "provider": "ssh",
                "exit_code": exit_code,
            }

        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.error(f"SSH command execution failed: {e}")
            raise

    def health_check(self) -> dict[str, Any]:
        """
        Check if SSH connection is available.

        Returns:
            Health check result
        """
        try:
            result = asyncio.run(self._health_check_async())
            return result
        except Exception as e:
            logger.error(f"[SSH] Health check failed: {e}")
            return {
                "status": "unhealthy",
                "provider": "ssh",
                "error": str(e),
            }

    async def _health_check_async(self) -> dict[str, Any]:
        """Check SSH availability asynchronously."""
        try:
            # Try simple echo command
            ssh_cmd = self._build_ssh_command("echo 'SSH OK'", "/tmp")

            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=10,  # Quick timeout for health check
                )
            except asyncio.TimeoutError:
                return {
                    "status": "unhealthy",
                    "provider": "ssh",
                    "error": "Connection timeout",
                }

            if proc.returncode == 0:
                return {
                    "status": "healthy",
                    "provider": "ssh",
                    "host": f"{self.username}@{self.host}:{self.port}",
                }
            else:
                error = stderr.decode("utf-8").strip()
                return {
                    "status": "unhealthy",
                    "provider": "ssh",
                    "error": f"SSH connection failed: {error}",
                }

        except FileNotFoundError:
            return {
                "status": "unhealthy",
                "provider": "ssh",
                "error": "SSH command not found - is OpenSSH installed?",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": "ssh",
                "error": str(e),
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        """
        Cancel a running job (not implemented for SSH).

        Args:
            job_id: Job ID to cancel

        Returns:
            Cancellation result
        """
        logger.warning(f"[SSH] Job cancellation not implemented for job {job_id}")
        return {
            "status": "not_supported",
            "provider": "ssh",
            "message": "SSH provider does not support job cancellation",
        }
