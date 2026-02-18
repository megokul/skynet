"""
Execution Router — Direct synchronous execution without queue.

Provides immediate execution for:
- Interactive commands
- Health checks
- Quick queries
- Testing

Bypasses Celery queue for low-latency execution.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TYPE_CHECKING

from .timeout import TimeoutManager, ExecutionTimeoutError

if TYPE_CHECKING:
    from skynet.scheduler import ProviderScheduler

logger = logging.getLogger("skynet.execution.router")

# Thread pool for running synchronous provider methods
_executor = ThreadPoolExecutor(max_workers=4)


class ExecutionRouter:
    """
    Direct execution router (bypass queue).

    Executes tasks immediately without going through Celery queue.
    Useful for:
    - Interactive commands
    - Health checks
    - Quick queries
    - Testing

    Usage:
        router = ExecutionRouter(scheduler=scheduler)
        result = await router.execute_plan(execution_spec, total_timeout=300)
    """

    def __init__(
        self,
        scheduler: ProviderScheduler | None = None,
        default_provider: str = "local",
        timeout_manager: TimeoutManager | None = None,
    ):
        """
        Initialize ExecutionRouter.

        Args:
            scheduler: Provider scheduler for intelligent selection
            default_provider: Fallback provider if scheduler unavailable
            timeout_manager: Timeout manager (uses default if None)
        """
        self.scheduler = scheduler
        self.default_provider = default_provider
        self.timeout_manager = timeout_manager or TimeoutManager()

        logger.info(
            f"ExecutionRouter initialized "
            f"(scheduler={'enabled' if scheduler else 'disabled'}, "
            f"default_provider={default_provider})"
        )

    # ========================================================================
    # Main Execution API
    # ========================================================================

    async def execute_plan(
        self,
        execution_spec: dict[str, Any],
        total_timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute plan directly (synchronous, no queue).

        Args:
            execution_spec: Execution specification with steps
            total_timeout: Total execution timeout (default: 30 min)

        Returns:
            Execution result dictionary with status, results, provider, etc.
        """
        if total_timeout is None:
            total_timeout = self.timeout_manager.get_timeout("total_execution")

        job_id = execution_spec.get("job_id", "direct_execution")

        logger.info(
            f"Direct execution started: {job_id} (timeout={total_timeout}s)"
        )

        start_time = time.time()

        try:
            result = await self.timeout_manager.execute_with_timeout(
                self._execute_plan_impl(execution_spec, total_timeout, start_time),
                timeout=total_timeout,
                timeout_key="total_execution",
            )

            elapsed = time.time() - start_time
            logger.info(
                f"Direct execution completed: {job_id} "
                f"(elapsed={elapsed:.1f}s, status={result['status']})"
            )

            return result

        except ExecutionTimeoutError as e:
            elapsed = time.time() - start_time
            logger.error(
                f"Direct execution timed out: {job_id} (elapsed={elapsed:.1f}s)"
            )

            return {
                "job_id": job_id,
                "status": "timeout",
                "error": str(e),
                "timeout_seconds": e.timeout_seconds,
                "elapsed_seconds": elapsed,
                "results": [],
            }

        except Exception as e:
            elapsed = time.time() - start_time
            logger.exception(
                f"Direct execution failed: {job_id} (elapsed={elapsed:.1f}s)"
            )

            return {
                "job_id": job_id,
                "status": "error",
                "error": str(e),
                "elapsed_seconds": elapsed,
                "results": [],
            }

    async def _execute_plan_impl(
        self,
        execution_spec: dict[str, Any],
        total_timeout: float,
        start_time: float,
    ) -> dict[str, Any]:
        """
        Internal implementation of plan execution.

        Args:
            execution_spec: Execution specification
            total_timeout: Total timeout
            start_time: Start timestamp

        Returns:
            Execution result
        """
        # Select provider
        provider_name = await self._select_provider(execution_spec)

        # Get provider instance
        provider = self._get_provider(provider_name)

        # Extract steps
        steps = self._extract_steps(execution_spec)

        logger.info(
            f"Executing {len(steps)} steps with provider '{provider_name}'"
        )

        # Execute steps sequentially
        results = []
        all_success = True

        for i, step in enumerate(steps, 1):
            # Calculate remaining time
            elapsed = time.time() - start_time
            remaining = self.timeout_manager.calculate_remaining_time(
                elapsed, total_timeout
            )

            if remaining <= 0:
                # Global timeout exceeded
                logger.warning(f"Global timeout exceeded before step {i}")
                break

            # Execute step with timeout
            try:
                step_timeout = self.timeout_manager.get_timeout("execution_step")
                effective_timeout = min(step_timeout, remaining)

                result = await self._execute_step(
                    provider, step, effective_timeout
                )

                results.append(result)

                if result.get("status") != "success":
                    all_success = False

                logger.debug(
                    f"  [{i}/{len(steps)}] {step.get('action')} → "
                    f"{result.get('status')}"
                )

            except ExecutionTimeoutError as e:
                logger.warning(f"  [{i}/{len(steps)}] Timeout: {e}")
                results.append({
                    "action": step.get("action"),
                    "status": "timeout",
                    "error": str(e),
                })
                all_success = False
                break  # Abort remaining steps on timeout

            except Exception as e:
                logger.error(f"  [{i}/{len(steps)}] Error: {e}")
                results.append({
                    "action": step.get("action"),
                    "status": "error",
                    "error": str(e),
                })
                all_success = False
                # Continue with remaining steps despite error

        # Return overall result
        return {
            "job_id": execution_spec.get("job_id"),
            "status": "success" if all_success else "partial_failure",
            "provider": provider_name,
            "results": results,
            "steps_completed": len(results),
            "steps_total": len(steps),
            "elapsed_seconds": time.time() - start_time,
        }

    # ========================================================================
    # Provider Selection and Execution
    # ========================================================================

    async def _select_provider(self, execution_spec: dict[str, Any]) -> str:
        """
        Select provider for execution.

        Args:
            execution_spec: Execution specification

        Returns:
            Selected provider name
        """
        # Check for pre-selected provider
        if "provider" in execution_spec:
            return execution_spec["provider"]

        # Use scheduler if available
        if self.scheduler:
            try:
                provider = await self.scheduler.select_provider(
                    execution_spec, fallback=self.default_provider
                )
                logger.info(f"Scheduler selected provider: {provider}")
                return provider
            except Exception as e:
                logger.warning(
                    f"Scheduler selection failed: {e}, "
                    f"using default: {self.default_provider}"
                )

        # Fallback to default
        return self.default_provider

    def _get_provider(self, provider_name: str):
        """
        Get provider instance.

        Args:
            provider_name: Provider name

        Returns:
            Provider instance

        Raises:
            ValueError: If provider not found
        """
        # Import providers here to avoid circular imports
        from skynet.chathan.providers.mock_provider import MockProvider
        from skynet.chathan.providers.local_provider import LocalProvider

        providers = {
            "mock": MockProvider(),
            "local": LocalProvider(),
            # TODO: Add other providers as needed
        }

        if provider_name not in providers:
            raise ValueError(f"Unknown provider: {provider_name}")

        return providers[provider_name]

    async def _execute_step(
        self, provider, step: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        """
        Execute single step with provider.

        Args:
            provider: Provider instance
            step: Step specification
            timeout: Step timeout

        Returns:
            Step result
        """
        action = step.get("action", "unknown")
        params = step.get("params", {})

        # Get action-specific timeout
        action_timeout = self.timeout_manager.get_timeout(action)
        effective_timeout = min(action_timeout, timeout)

        logger.debug(
            f"Executing step '{action}' with timeout {effective_timeout}s"
        )

        # Execute provider method in thread pool (providers are synchronous)
        loop = asyncio.get_event_loop()

        result = await self.timeout_manager.execute_with_timeout(
            loop.run_in_executor(_executor, provider.execute, action, params),
            timeout=effective_timeout,
            timeout_key=action,
        )

        return {
            "action": action,
            "status": result.get("status", "unknown"),
            "output": result.get("output", ""),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def _extract_steps(self, execution_spec: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Extract steps from execution spec.

        Supports both 'steps' and 'actions' formats.

        Args:
            execution_spec: Execution specification

        Returns:
            List of step dictionaries
        """
        # Try 'steps' first (dispatcher format)
        steps = execution_spec.get("steps", [])
        if steps:
            return steps

        # Fallback to 'actions' (legacy format)
        actions = execution_spec.get("actions", [])
        return actions

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"ExecutionRouter("
            f"scheduler={'✓' if self.scheduler else '✗'}, "
            f"default_provider={self.default_provider})"
        )
