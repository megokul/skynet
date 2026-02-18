"""
CHATHAN — Docker Provider

Executes actions inside Docker containers for isolated execution.

Each action runs in a fresh container that is automatically cleaned up.
This provides sandboxing and isolation for untrusted or sensitive operations.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

logger = logging.getLogger("skynet.chathan.docker")


class DockerProvider:
    """
    Executes tasks inside Docker containers.

    Each action runs in a fresh container with automatic cleanup.
    Provides isolation and sandboxing for execution.
    """

    def __init__(
        self,
        docker_image: str = "ubuntu:22.04",
        container_name_prefix: str = "skynet_exec_",
        timeout: int = 300,  # 5 minutes default
        auto_pull: bool = True,
    ):
        """
        Initialize DockerProvider.

        Args:
            docker_image: Docker image to use for containers
            container_name_prefix: Prefix for container names
            timeout: Default timeout for command execution (seconds)
            auto_pull: Automatically pull image if not present
        """
        self.name = "docker"
        self.docker_image = docker_image
        self.container_prefix = container_name_prefix
        self.timeout = timeout
        self.auto_pull = auto_pull
        self._image_pulled = False
        logger.info(f"Docker provider initialized - image: {docker_image}")

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute action inside a Docker container.

        This is a synchronous wrapper that runs the async Docker operations.

        Args:
            action: Action type (e.g., 'git_status', 'execute_command')
            params: Action parameters

        Returns:
            Execution result with status, output, exit_code
        """
        logger.info(f"[DOCKER] Executing {action} with params: {params}")

        try:
            result = asyncio.run(self._execute_async(action, params))
            return result
        except Exception as e:
            logger.error(f"[DOCKER] Failed to execute {action}: {e}")
            return {
                "status": "error",
                "output": f"Docker execution error: {e}",
                "action": action,
                "provider": "docker",
                "exit_code": -1,
            }

    async def _execute_async(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute action asynchronously inside Docker container.

        Args:
            action: Action type
            params: Action parameters

        Returns:
            Execution result
        """
        # Ensure image is available
        if self.auto_pull and not self._image_pulled:
            await self._pull_image()

        # Map action to command
        command = self._action_to_command(action, params)
        if not command:
            return {
                "status": "error",
                "output": f"Unknown action: {action}",
                "action": action,
                "provider": "docker",
                "exit_code": -1,
            }

        # Get working directory
        working_dir = params.get("working_dir", "/workspace")

        # Execute in container
        try:
            result = await self._run_in_container(command, working_dir)
            return result
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "output": f"Command timed out after {self.timeout}s",
                "action": action,
                "provider": "docker",
                "exit_code": -1,
            }
        except Exception as e:
            return {
                "status": "error",
                "output": f"Container execution failed: {e}",
                "action": action,
                "provider": "docker",
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

        # Generic command execution
        elif action == "execute_command":
            return params.get("command", "")

        # Unknown action
        return None

    async def _run_in_container(self, command: str, working_dir: str) -> dict[str, Any]:
        """
        Run command in a Docker container.

        Creates container, executes command, gets output, cleans up.

        Args:
            command: Shell command to execute
            working_dir: Working directory inside container

        Returns:
            Execution result
        """
        import uuid
        container_name = f"{self.container_prefix}{uuid.uuid4().hex[:8]}"

        try:
            # Run command in container (using docker run with --rm for auto-cleanup)
            proc = await asyncio.create_subprocess_exec(
                "docker", "run",
                "--rm",  # Auto-remove container
                "--name", container_name,
                "-w", working_dir,  # Set working directory
                self.docker_image,
                "sh", "-c", command,
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
                # Kill container if timeout
                await self._kill_container(container_name)
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
                "action": "docker_exec",
                "provider": "docker",
                "exit_code": exit_code,
            }

        except asyncio.TimeoutError:
            # Timeout already handled above
            raise
        except Exception as e:
            logger.error(f"Container execution failed: {e}")
            raise

    async def _pull_image(self) -> None:
        """Pull the Docker image if not present."""
        logger.info(f"Pulling Docker image: {self.docker_image}")
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "pull", self.docker_image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            if proc.returncode == 0:
                self._image_pulled = True
                logger.info(f"Image pulled successfully: {self.docker_image}")
            else:
                logger.warning(f"Failed to pull image {self.docker_image}, will try to use existing")

        except Exception as e:
            logger.warning(f"Image pull failed: {e}, will try to use existing image")

    async def _kill_container(self, container_name: str) -> None:
        """Force kill a running container."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "kill", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception as e:
            logger.warning(f"Failed to kill container {container_name}: {e}")

    def health_check(self) -> dict[str, Any]:
        """
        Check if Docker is available and working.

        Returns:
            Health check result
        """
        try:
            result = asyncio.run(self._health_check_async())
            return result
        except Exception as e:
            logger.error(f"[DOCKER] Health check failed: {e}")
            return {
                "status": "unhealthy",
                "provider": "docker",
                "error": str(e),
            }

    async def _health_check_async(self) -> dict[str, Any]:
        """Check Docker availability asynchronously."""
        try:
            # Check if docker command is available
            proc = await asyncio.create_subprocess_exec(
                "docker", "version", "--format", "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                version = stdout.decode("utf-8").strip()
                return {
                    "status": "healthy",
                    "provider": "docker",
                    "docker_version": version,
                }
            else:
                error = stderr.decode("utf-8").strip()
                return {
                    "status": "unhealthy",
                    "provider": "docker",
                    "error": f"Docker daemon not running: {error}",
                }

        except FileNotFoundError:
            return {
                "status": "unhealthy",
                "provider": "docker",
                "error": "Docker command not found - is Docker installed?",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": "docker",
                "error": str(e),
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        """
        Cancel a running job by killing its container.

        Args:
            job_id: Job ID to cancel

        Returns:
            Cancellation result
        """
        container_name = f"{self.container_prefix}{job_id}"

        try:
            result = asyncio.run(self._kill_container(container_name))
            return {
                "status": "success",
                "provider": "docker",
                "message": f"Container {container_name} killed",
            }
        except Exception as e:
            logger.error(f"[DOCKER] Cancellation failed: {e}")
            return {
                "status": "error",
                "provider": "docker",
                "error": str(e),
            }
