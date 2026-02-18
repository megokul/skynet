"""
Timeout Management — Multi-level timeout enforcement for task execution.

Implements 4-level timeout hierarchy:
1. Global Execution Timeout (30 min) - Maximum for entire job
2. Per-Step Timeout (10 min) - Maximum for single step
3. Provider Action Timeout (5 min) - Maximum for provider operation
4. Command Timeout (varies) - Specific command timeouts (git, build, deploy)

Prevents stuck executions and ensures system responsiveness.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, TypeVar

logger = logging.getLogger("skynet.execution.timeout")

T = TypeVar("T")


class ExecutionTimeoutError(Exception):
    """Raised when execution exceeds timeout."""

    def __init__(self, message: str, timeout_seconds: float):
        """
        Initialize timeout error.

        Args:
            message: Error message
            timeout_seconds: Timeout value that was exceeded
        """
        super().__init__(message)
        self.timeout_seconds = timeout_seconds


class TimeoutManager:
    """
    Centralized timeout management for all execution layers.

    Provides consistent timeout enforcement across:
    - Global job execution
    - Individual steps
    - Provider actions
    - Specific commands

    Usage:
        # Execute with timeout
        result = await TimeoutManager.execute_with_timeout(
            my_async_function(),
            timeout=300,
            timeout_key='provider_action'
        )

        # Execute with default timeout for action type
        result = await TimeoutManager.execute_with_timeout(
            run_tests(),
            timeout_key='run_tests'
        )
    """

    # ========================================================================
    # Default Timeout Values (in seconds)
    # ========================================================================

    DEFAULT_TIMEOUTS: dict[str, float] = {
        # Global limits
        "total_execution": 1800,  # 30 minutes - maximum job duration
        "execution_step": 600,  # 10 minutes - maximum step duration
        "provider_action": 300,  # 5 minutes - default provider action timeout
        # Git operations
        "git_status": 30,  # 30 seconds
        "git_clone": 300,  # 5 minutes
        "git_commit": 60,  # 1 minute
        "git_push": 180,  # 3 minutes
        "git_pull": 180,  # 3 minutes
        # Build and test operations
        "run_tests": 600,  # 10 minutes
        "build": 600,  # 10 minutes
        "lint_project": 180,  # 3 minutes
        "install_dependencies": 480,  # 8 minutes
        # Docker operations
        "docker_build": 900,  # 15 minutes
        "docker_run": 300,  # 5 minutes
        "docker_compose_up": 600,  # 10 minutes
        # Deployment operations
        "deploy_staging": 900,  # 15 minutes
        "deploy_prod": 1200,  # 20 minutes
        # File operations (fast)
        "list_directory": 10,  # 10 seconds
        "execute_command": 60,  # 1 minute (default for unknown commands)
        # SSH operations
        "ssh_execute": 300,  # 5 minutes
    }

    # ========================================================================
    # Timeout Execution
    # ========================================================================

    @classmethod
    async def execute_with_timeout(
        cls,
        coro: Awaitable[T],
        timeout: float | None = None,
        timeout_key: str | None = None,
    ) -> T:
        """
        Execute coroutine with timeout.

        Args:
            coro: Async coroutine to execute
            timeout: Timeout in seconds (if None, uses timeout_key)
            timeout_key: Key to look up timeout in DEFAULT_TIMEOUTS

        Returns:
            Result of coroutine execution

        Raises:
            ExecutionTimeoutError: If execution exceeds timeout
        """
        # Determine timeout value
        if timeout is None and timeout_key:
            timeout = cls.DEFAULT_TIMEOUTS.get(
                timeout_key, cls.DEFAULT_TIMEOUTS["provider_action"]
            )
        elif timeout is None:
            timeout = cls.DEFAULT_TIMEOUTS["provider_action"]

        logger.debug(f"Executing with timeout: {timeout}s (key={timeout_key})")

        try:
            result = await asyncio.wait_for(coro, timeout=timeout)
            return result
        except asyncio.TimeoutError as e:
            error_msg = f"Execution exceeded timeout of {timeout}s"
            if timeout_key:
                error_msg += f" (timeout_key={timeout_key})"

            logger.error(error_msg)
            raise ExecutionTimeoutError(error_msg, timeout) from e

    @classmethod
    async def execute_with_multiple_timeouts(
        cls,
        coro: Awaitable[T],
        step_timeout: float,
        global_timeout: float,
        timeout_key: str | None = None,
    ) -> T:
        """
        Execute with both step-level and global timeout.

        Uses whichever timeout is stricter (lower).

        Args:
            coro: Async coroutine to execute
            step_timeout: Step-level timeout
            global_timeout: Global remaining time
            timeout_key: Optional key for logging

        Returns:
            Result of coroutine execution

        Raises:
            ExecutionTimeoutError: If execution exceeds timeout
        """
        # Use stricter (lower) timeout
        effective_timeout = min(step_timeout, global_timeout)

        logger.debug(
            f"Executing with multiple timeouts: "
            f"step={step_timeout}s, global={global_timeout}s, "
            f"using={effective_timeout}s"
        )

        return await cls.execute_with_timeout(
            coro, timeout=effective_timeout, timeout_key=timeout_key
        )

    # ========================================================================
    # Timeout Information
    # ========================================================================

    @classmethod
    def get_timeout(cls, timeout_key: str) -> float:
        """
        Get timeout value for a specific key.

        Args:
            timeout_key: Timeout key (action name, etc.)

        Returns:
            Timeout in seconds
        """
        return cls.DEFAULT_TIMEOUTS.get(
            timeout_key, cls.DEFAULT_TIMEOUTS["provider_action"]
        )

    @classmethod
    def get_all_timeouts(cls) -> dict[str, float]:
        """Get all default timeout values."""
        return dict(cls.DEFAULT_TIMEOUTS)

    @classmethod
    def set_timeout(cls, timeout_key: str, timeout_seconds: float) -> None:
        """
        Set custom timeout for a specific key.

        Args:
            timeout_key: Timeout key
            timeout_seconds: Timeout value in seconds
        """
        cls.DEFAULT_TIMEOUTS[timeout_key] = timeout_seconds
        logger.info(f"Set timeout for '{timeout_key}' to {timeout_seconds}s")

    # ========================================================================
    # Timeout Calculation Helpers
    # ========================================================================

    @classmethod
    def calculate_remaining_time(
        cls, elapsed_seconds: float, total_timeout: float
    ) -> float:
        """
        Calculate remaining time for global timeout.

        Args:
            elapsed_seconds: Time elapsed so far
            total_timeout: Total allowed time

        Returns:
            Remaining time in seconds (minimum 0)
        """
        remaining = total_timeout - elapsed_seconds
        return max(0.0, remaining)

    @classmethod
    def is_timeout_exceeded(
        cls, elapsed_seconds: float, timeout_seconds: float
    ) -> bool:
        """
        Check if timeout has been exceeded.

        Args:
            elapsed_seconds: Time elapsed
            timeout_seconds: Timeout limit

        Returns:
            True if timeout exceeded, False otherwise
        """
        return elapsed_seconds >= timeout_seconds


# ============================================================================
# Convenience Functions
# ============================================================================


async def with_timeout(coro: Awaitable[T], seconds: float) -> T:
    """
    Simple timeout wrapper.

    Args:
        coro: Async coroutine
        seconds: Timeout in seconds

    Returns:
        Result of coroutine

    Raises:
        ExecutionTimeoutError: If timeout exceeded
    """
    return await TimeoutManager.execute_with_timeout(coro, timeout=seconds)
