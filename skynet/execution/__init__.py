"""
SKYNET Execution — Direct execution and timeout management.

Provides:
- ExecutionRouter: Direct synchronous execution (bypass queue)
- TimeoutManager: Multi-level timeout enforcement
- ExecutionTimeoutError: Timeout exception

Usage:
    from skynet.execution import ExecutionRouter, TimeoutManager

    # Direct execution
    router = ExecutionRouter(scheduler=scheduler)
    result = await router.execute_plan(execution_spec)

    # Timeout management
    result = await TimeoutManager.execute_with_timeout(
        coro,
        timeout=300,
        timeout_key='provider_action'
    )
"""

from .timeout import TimeoutManager, ExecutionTimeoutError
from .router import ExecutionRouter

__all__ = [
    "TimeoutManager",
    "ExecutionTimeoutError",
    "ExecutionRouter",
]
